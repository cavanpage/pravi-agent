"""`pravi dev "task ..."` — Slice 1A surface.

Spins up a worktree of the target repo on a fresh branch, runs the developer
agent (claude-agent-sdk) against the requested task, and (by default) leaves
the worktree intact so you can `cd` in and inspect what Claude did. Use
`--cleanup` for ephemeral runs.

This command does NOT open a PR, does NOT run tests, and does NOT consult
the architect — those land in Slices 1B / 1C.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from temporalio.client import Client
from temporalio.common import (
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDReusePolicy,
)

from pravi.activities.dev_activity import build_request_from_registry
from pravi.config import get_settings
from pravi.domains.registry import DomainRegistry
from pravi.logging_setup import configure_logging
from pravi.temporal_utils import (
    DOMAIN,
    PRAVI_STATUS,
    REPO_NAME,
    TICKET_ID,
    repo_slug,
    slugify,
)
from pravi.workflows.dev_workflow import (
    DevWorkflow,
    DevWorkflowInput,
    DevWorkflowResult,
)

console = Console()


def dev(
    task: Annotated[str, typer.Argument(help="Task description for the dev agent.")],
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
        typer.Option(
            "--domains-file",
            help="Override path to a domains.yaml.",
        ),
    ] = None,
    base_ref: Annotated[str, typer.Option(help="Base git ref for the worktree.")] = "main",
    cleanup: Annotated[
        bool,
        typer.Option(
            "--cleanup/--keep",
            help="Remove worktree + branch after the run. Default keeps for inspection.",
        ),
    ] = False,
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if repo is None:
        if not settings.target_repos:
            raise typer.BadParameter("no --repo provided and PRAVI_TARGET_REPOS is empty")
        repo = settings.target_repos[0]
    repo = repo.expanduser().resolve()

    registry = DomainRegistry.load(repo, override_file=domains_file)
    chosen = registry.get(domain) if domain else registry.domains[0]

    session_id = f"dev-{uuid.uuid4().hex[:8]}"
    branch = f"pravi/{session_id}-{slugify(chosen.name)}"

    dev_request = build_request_from_registry(
        repo_path=str(repo),
        worktree_path="",  # filled in by the workflow from the worktree result
        domain_name=chosen.name,
        task=task,
        domains_file=domains_file,
    )

    inp = DevWorkflowInput(
        repo_path=str(repo),
        ticket_id=session_id,
        branch=branch,
        base_ref=base_ref,
        dev_request=dev_request,
        llm_task_queue=settings.temporal_task_queue_llm,
        cleanup_worktree=cleanup,
        delete_branch_on_cleanup=cleanup,
    )

    workflow_id = f"dev-{repo_slug(repo)}-{session_id}"
    console.print(
        f"[bold]pravi dev[/] session [cyan]{session_id}[/]  "
        f"domain=[magenta]{chosen.name}[/]  repo={repo}"
    )
    console.print(f"workflow id: [dim]{workflow_id}[/]  (Temporal UI: http://localhost:8233)")
    if not cleanup:
        console.print(
            "[yellow]worktree will be kept[/] — use --cleanup to remove afterwards"
        )

    result = asyncio.run(_run_workflow(inp, workflow_id, chosen.name, str(repo), session_id))

    dev = result.dev
    status = "[green]✓ success[/]" if dev.success else "[red]✗ failed[/]"
    console.print()
    console.print(
        f"{status}  turns={dev.num_turns} duration={dev.duration_ms / 1000:.1f}s "
        f"cost=${dev.total_cost_usd or 0:.4f}"
    )
    if dev.tool_uses:
        console.print(f"tools used: {', '.join(dev.tool_uses[:20])}")
    if dev.errors:
        console.print(f"[red]errors:[/] {'; '.join(dev.errors)}")
    if dev.summary:
        console.print()
        console.print("[bold]agent summary:[/]")
        console.print(dev.summary)

    if not cleanup:
        console.print()
        console.print(f"[bold]worktree preserved:[/] {result.worktree_path}")
        console.print(f"  inspect with:  cd {result.worktree_path} && git diff {base_ref}")


async def _run_workflow(
    inp: DevWorkflowInput,
    workflow_id: str,
    domain_name: str,
    repo_path: str,
    session_id: str,
) -> DevWorkflowResult:
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    handle = await client.start_workflow(
        DevWorkflow.run,
        inp,
        id=workflow_id,
        task_queue=settings.temporal_task_queue_features,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        search_attributes=TypedSearchAttributes(
            [
                SearchAttributePair(REPO_NAME, repo_slug(repo_path)),
                SearchAttributePair(DOMAIN, domain_name),
                SearchAttributePair(TICKET_ID, session_id),
                SearchAttributePair(PRAVI_STATUS, "dev-run"),
            ]
        ),
    )
    return await handle.result()
