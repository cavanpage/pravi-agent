"""`pravi ticket start` and `pravi plan` — the Slice 1B human-in-the-loop surface.

`ticket start <id>` creates Ticket+Repo rows (idempotent), launches a
FeatureWorkflow that immediately blocks waiting for an approved plan, and
streams a tail of the workflow's status until it completes or you Ctrl-C.

`plan <id>` is the human side: it runs the architect (read-only Claude),
opens the draft in $EDITOR, prompts for approve/revise/cancel, and on approve
persists a Plan row and signals the waiting workflow with the plan ID.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from sqlalchemy import select
from temporalio.client import Client
from temporalio.common import (
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError

from pravi.agents.architect import ArchitectRequest, draft_plan
from pravi.cli.plan_editor import edit_plan
from pravi.config import get_settings
from pravi.db.models import Plan, Repo, Ticket, TicketStatus
from pravi.db.session import session_scope
from pravi.domains.registry import DomainRegistry
from pravi.logging_setup import configure_logging
from pravi.temporal_utils import (
    DOMAIN,
    PRAVI_STATUS,
    REPO_NAME,
    TICKET_ID,
    feature_workflow_id,
    repo_slug,
    slugify,
)
from pravi.workflows.feature_workflow import (
    FeatureWorkflow,
    FeatureWorkflowInput,
)

console = Console()


async def _ensure_repo_and_ticket(
    *,
    repo: Path,
    external_id: str,
    title: str,
    body: str,
    domain_name: str | None,
) -> int:
    """Upsert Repo + Ticket rows. Returns the (numeric) Ticket.id."""
    async with session_scope() as session:
        existing_repo = (
            await session.execute(select(Repo).where(Repo.local_path == str(repo)))
        ).scalar_one_or_none()
        if existing_repo is None:
            existing_repo = Repo(name=repo.name, local_path=str(repo))
            session.add(existing_repo)
            await session.flush()

        existing_ticket = (
            await session.execute(
                select(Ticket).where(
                    Ticket.repo_id == existing_repo.id,
                    Ticket.external_id == external_id,
                )
            )
        ).scalar_one_or_none()

        if existing_ticket is None:
            ticket = Ticket(
                repo_id=existing_repo.id,
                external_id=external_id,
                title=title,
                body=body,
                domain_name=domain_name,
                status=TicketStatus.pending,
            )
            session.add(ticket)
            await session.flush()
            return ticket.id

        # Update editable fields if changed — title/body/domain can be amended.
        existing_ticket.title = title
        existing_ticket.body = body
        if domain_name:
            existing_ticket.domain_name = domain_name
        return existing_ticket.id


def ticket_start(
    external_id: Annotated[
        str,
        typer.Argument(help="External ticket ID (GitHub issue # later; any string today)."),
    ],
    title: Annotated[str, typer.Option(help="Ticket title.")],
    body: Annotated[str, typer.Option(help="Ticket body / description.")] = "",
    body_file: Annotated[
        Path | None,
        typer.Option(
            "--body-file",
            help="Read body from this file instead of --body (use '-' for stdin).",
        ),
    ] = None,
    repo: Annotated[
        Path | None,
        typer.Option(help="Target repo path. Defaults to first PRAVI_TARGET_REPOS entry."),
    ] = None,
    domain: Annotated[
        str | None,
        typer.Option(help="Domain name. Defaults to the first domain in domains.yaml."),
    ] = None,
    domains_file: Annotated[
        Path | None,
        typer.Option("--domains-file", help="Override path to a domains.yaml."),
    ] = None,
    base_ref: Annotated[str, typer.Option(help="Base git ref for the worktree.")] = "main",
    cleanup_worktree: Annotated[
        bool,
        typer.Option("--cleanup/--keep", help="Remove worktree after dev (default: keep)."),
    ] = False,
    detach: Annotated[
        bool,
        typer.Option("--detach", help="Start the workflow and exit instead of streaming."),
    ] = False,
) -> None:
    """Persist a ticket and start the FeatureWorkflow (blocks on plan signal)."""
    settings = get_settings()
    configure_logging(settings.log_level)

    if body_file is not None:
        body = sys.stdin.read() if str(body_file) == "-" else body_file.read_text(encoding="utf-8")

    if repo is None:
        if not settings.target_repos:
            raise typer.BadParameter("no --repo provided and PRAVI_TARGET_REPOS is empty")
        repo = settings.target_repos[0]
    repo = repo.expanduser().resolve()

    registry = DomainRegistry.load(repo, override_file=domains_file)
    chosen = registry.get(domain) if domain else registry.domains[0]

    try:
        asyncio.run(
            _ticket_start_flow(
                repo=repo,
                external_id=external_id,
                title=title,
                body=body,
                chosen_name=chosen.name,
                chosen_description=chosen.description,
                chosen_paths=list(chosen.paths),
                base_ref=base_ref,
                cleanup_worktree=cleanup_worktree,
                detach=detach,
            )
        )
    except RPCError as e:
        if "WorkflowExecutionAlreadyStarted" in str(e):
            console.print(
                f"[red]error[/] workflow [bold]{feature_workflow_id(repo, external_id)}[/] "
                f"is already running. Terminate or wait for it to finish before re-starting."
            )
            raise typer.Exit(code=1) from e
        raise


async def _ticket_start_flow(
    *,
    repo: Path,
    external_id: str,
    title: str,
    body: str,
    chosen_name: str,
    chosen_description: str,
    chosen_paths: list[str],
    base_ref: str,
    cleanup_worktree: bool,
    detach: bool,
) -> None:
    ticket_id = await _ensure_repo_and_ticket(
        repo=repo,
        external_id=external_id,
        title=title,
        body=body,
        domain_name=chosen_name,
    )

    inp = FeatureWorkflowInput(
        ticket_id=ticket_id,
        domain_name=chosen_name,
        domain_description=chosen_description,
        domain_paths=chosen_paths,
        base_ref=base_ref,
        llm_task_queue=get_settings().temporal_task_queue_llm,
        cleanup_worktree=cleanup_worktree,
    )
    workflow_id = feature_workflow_id(repo, external_id)
    console.print(
        f"[bold]pravi ticket start[/] ticket=[cyan]{external_id}[/] "
        f"(db id={ticket_id})  domain=[magenta]{chosen_name}[/]  repo={repo}"
    )
    console.print(f"workflow id: [dim]{workflow_id}[/]")
    console.print(
        f"[yellow]waiting for plan approval[/] — run "
        f"[bold]pravi plan {external_id} --repo {repo}[/] in another terminal"
    )

    await _start_and_optionally_wait(
        inp, workflow_id, chosen_name, str(repo), external_id, detach
    )


async def _start_and_optionally_wait(
    inp: FeatureWorkflowInput,
    workflow_id: str,
    domain_name: str,
    repo_path: str,
    external_id: str,
    detach: bool,
) -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    handle = await client.start_workflow(
        FeatureWorkflow.run,
        inp,
        id=workflow_id,
        task_queue=settings.temporal_task_queue_features,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        search_attributes=TypedSearchAttributes(
            [
                SearchAttributePair(REPO_NAME, repo_slug(repo_path)),
                SearchAttributePair(DOMAIN, domain_name),
                SearchAttributePair(TICKET_ID, external_id),
                SearchAttributePair(PRAVI_STATUS, "waiting_for_plan"),
            ]
        ),
    )

    if detach:
        console.print(f"workflow started ({handle.id}) — detached; check Temporal UI for progress")
        return

    # Tail status until the workflow completes.
    last_status: str | None = None
    while True:
        try:
            status = await handle.query(FeatureWorkflow.current_status)
        except RPCError:
            break
        if status != last_status:
            console.print(f"[blue]status[/] {status}")
            last_status = status
        try:
            result = await asyncio.wait_for(handle.result(), timeout=2.0)
        except TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        else:
            console.print(f"[green]workflow finished[/] — {result.summary}")
            if result.dev:
                console.print(
                    f"dev: turns={result.dev.num_turns} cost=${result.dev.total_cost_usd or 0:.4f}"
                )
            if result.worktree_path:
                console.print(f"worktree: {result.worktree_path}")
            return


# ---------------------------------------------------------------------------
# pravi plan
# ---------------------------------------------------------------------------


def plan(
    external_id: Annotated[str, typer.Argument(help="External ticket ID.")],
    repo: Annotated[
        Path | None,
        typer.Option(help="Target repo path. Defaults to first PRAVI_TARGET_REPOS entry."),
    ] = None,
    domains_file: Annotated[
        Path | None,
        typer.Option("--domains-file", help="Override path to a domains.yaml."),
    ] = None,
    no_editor: Annotated[
        bool,
        typer.Option(
            "--no-editor",
            help="Skip $EDITOR; auto-approve the architect draft as-is (for scripted runs).",
        ),
    ] = False,
    approver: Annotated[
        str | None,
        typer.Option(help="Recorded on the Plan row as the approver (defaults to $USER)."),
    ] = None,
) -> None:
    """Draft a plan with the architect, edit + approve, signal the workflow."""
    settings = get_settings()
    configure_logging(settings.log_level)

    if repo is None:
        if not settings.target_repos:
            raise typer.BadParameter("no --repo provided and PRAVI_TARGET_REPOS is empty")
        repo = settings.target_repos[0]
    repo = repo.expanduser().resolve()

    # Single event loop for the whole flow — SQLAlchemy async engine connections
    # are bound to the loop they were created in, so multi-`asyncio.run` would
    # explode when the second call reuses the cached engine from the first.
    asyncio.run(
        _plan_flow(
            external_id=external_id,
            repo=repo,
            domains_file=domains_file,
            no_editor=no_editor,
            approver=approver,
        )
    )


async def _plan_flow(
    *,
    external_id: str,
    repo: Path,
    domains_file: Path | None,
    no_editor: bool,
    approver: str | None,
) -> None:
    ticket_id, ticket_title, ticket_body, domain_name = await _load_ticket_for_plan(
        external_id=external_id, repo=repo
    )

    # Sanity-check the workflow is alive BEFORE spending tokens on the architect.
    # A cancelled / completed / failed workflow can't accept the signal, and
    # discovering that after an editor session is needlessly painful.
    await _assert_workflow_signalable(repo=repo, external_id=external_id)

    registry = DomainRegistry.load(repo, override_file=domains_file)
    chosen = registry.get(domain_name) if domain_name else registry.domains[0]

    console.print(
        f"[bold]drafting plan[/] for ticket [cyan]{external_id}[/] "
        f"(domain=[magenta]{chosen.name}[/])"
    )
    arch_req = ArchitectRequest(
        repo_path=str(repo),
        repo_name=repo.name,
        domain_name=chosen.name,
        domain_description=chosen.description,
        domain_paths=list(chosen.paths),
        ticket_title=ticket_title,
        ticket_body=ticket_body,
    )
    arch_result = await draft_plan(arch_req)
    if not arch_result.success:
        console.print(f"[red]architect failed:[/] {'; '.join(arch_result.errors)}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]architect ok[/] turns={arch_result.num_turns} "
        f"duration={arch_result.duration_ms / 1000:.1f}s "
        f"cost=${arch_result.total_cost_usd or 0:.4f}"
    )

    slug = f"{slugify(repo.name)}-{slugify(external_id)}"
    if no_editor:
        approved_content = arch_result.plan_md
        console.print("[yellow]--no-editor[/] — using architect draft as-is")
    else:
        # `edit_plan` blocks on $EDITOR + stdin prompts; run it off the loop.
        edited = await asyncio.to_thread(edit_plan, slug, arch_result.plan_md)
        if edited.decision == "cancel":
            console.print("[red]cancelled[/] (signalling workflow cancel)")
            await _signal_cancel(repo=repo, external_id=external_id)
            return
        approved_content = edited.content

    plan_id = await _save_plan(
        ticket_id=ticket_id,
        domain_name=chosen.name,
        domain_snapshot=chosen.model_dump(),
        content_md=approved_content,
        approver=approver,
    )
    console.print(f"[green]plan saved[/] id={plan_id}")

    await _signal_plan_approved(repo=repo, external_id=external_id, plan_id=plan_id)
    console.print(
        "[green]signalled workflow[/] — tail it with the original `pravi ticket start` terminal"
    )


async def _load_ticket_for_plan(*, external_id: str, repo: Path) -> tuple[int, str, str, str | None]:
    async with session_scope() as session:
        stmt = (
            select(Ticket)
            .join(Repo, Ticket.repo_id == Repo.id)
            .where(Repo.local_path == str(repo), Ticket.external_id == external_id)
        )
        ticket = (await session.execute(stmt)).scalar_one_or_none()
        if ticket is None:
            raise typer.BadParameter(
                f"no ticket with external_id={external_id!r} for repo {repo}. "
                f"Did you run `pravi ticket start {external_id} ...` first?"
            )
        return ticket.id, ticket.title, ticket.body or "", ticket.domain_name


async def _save_plan(
    *,
    ticket_id: int,
    domain_name: str,
    domain_snapshot: dict,
    content_md: str,
    approver: str | None,
) -> int:
    import os
    from datetime import datetime

    async with session_scope() as session:
        plan_row = Plan(
            ticket_id=ticket_id,
            domain_name=domain_name,
            domain_snapshot=domain_snapshot,
            content_md=content_md,
            approved_at=datetime.now(UTC),
            approved_by=approver or os.environ.get("USER") or "unknown",
        )
        session.add(plan_row)
        await session.flush()
        return plan_row.id


async def _signal_plan_approved(*, repo: Path, external_id: str, plan_id: int) -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    workflow_id = feature_workflow_id(repo, external_id)
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(FeatureWorkflow.approve_plan, plan_id)


async def _assert_workflow_signalable(*, repo: Path, external_id: str) -> None:
    """Raise typer.Exit if the workflow doesn't exist or has already closed."""
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    workflow_id = feature_workflow_id(repo, external_id)
    try:
        desc = await client.get_workflow_handle(workflow_id).describe()
    except RPCError as e:
        if "NotFound" in str(e) or "not found" in str(e).lower():
            console.print(
                f"[red]no workflow with id[/] [bold]{workflow_id}[/]. "
                f"Run `pravi ticket start {external_id} ...` first."
            )
            raise typer.Exit(code=1) from e
        raise
    # WorkflowExecutionStatus: 1=RUNNING, 2=COMPLETED, 3=FAILED, 4=CANCELED,
    # 5=TERMINATED, 6=CONTINUED_AS_NEW, 7=TIMED_OUT
    status_int = int(desc.status)
    if status_int != 1:
        names = {
            2: "COMPLETED",
            3: "FAILED",
            4: "CANCELED",
            5: "TERMINATED",
            6: "CONTINUED_AS_NEW",
            7: "TIMED_OUT",
        }
        console.print(
            f"[red]workflow [bold]{workflow_id}[/] is {names.get(status_int, status_int)}[/] — "
            f"can't accept signals. Re-run `pravi ticket start {external_id} ...` to start a new run."
        )
        raise typer.Exit(code=1)


async def _signal_cancel(*, repo: Path, external_id: str) -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    workflow_id = feature_workflow_id(repo, external_id)
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(FeatureWorkflow.cancel)
