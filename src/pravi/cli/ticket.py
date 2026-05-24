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
from temporalio.service import RPCError

from pravi.config import get_settings
from pravi.domains.registry import DomainRegistry
from pravi.logging_setup import configure_logging
from pravi.temporal_utils import (
    DOMAIN,
    PRAVI_STATUS,
    REPO_NAME,
    TICKET_ID,
    feature_workflow_id,
    repo_slug,
)
from pravi.workflows.smoke_workflow import (
    SmokeWorkflow,
    SmokeWorkflowInput,
    SmokeWorkflowResult,
)

app = typer.Typer(help="Manage feature tickets.", no_args_is_help=True)
console = Console()


@app.command("run")
def run_ticket(
    ticket_id: Annotated[
        str | None,
        typer.Argument(help="Ticket ID (e.g., GitHub issue number). Omit if --fake."),
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
        typer.Option(
            "--domains-file",
            help="Override path to a domains.yaml (defaults to <repo>/.builder/domains.yaml).",
        ),
    ] = None,
    base_ref: Annotated[str, typer.Option(help="Base git ref for the worktree.")] = "main",
    fake: Annotated[bool, typer.Option(help="Run a smoke workflow with a generated ID.")] = False,
    smoke_command: Annotated[
        str | None,
        typer.Option(
            help=(
                "Override the smoke command. Defaults to the chosen domain's `test` command. "
                "Pass quoted, e.g. --smoke-command 'npm test --workspace=shared'."
            ),
        ),
    ] = None,
) -> None:
    """Run a Slice-0 smoke workflow against a worktree.

    Slice 0 only creates the worktree, runs the smoke command, and tears down.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    if repo is None:
        if not settings.target_repos:
            raise typer.BadParameter(
                "no --repo provided and PRAVI_TARGET_REPOS is empty",
            )
        repo = settings.target_repos[0]
    repo = repo.expanduser().resolve()

    registry = DomainRegistry.load(repo, override_file=domains_file)
    chosen = registry.get(domain) if domain else registry.domains[0]

    if smoke_command is not None:
        cmd = smoke_command.split()
    elif chosen.test:
        cmd = chosen.test.split()
    else:
        raise typer.BadParameter(
            f"domain {chosen.name!r} has no `test` command; pass --smoke-command",
        )

    tid = ticket_id or f"fake-{uuid.uuid4().hex[:8]}"
    if fake and ticket_id:
        console.print("[yellow]--fake ignored: explicit ticket_id provided[/]")
    branch = f"pravi/{tid}-{chosen.name}"

    is_fake_run = tid.startswith("fake-")
    inp = SmokeWorkflowInput(
        repo_path=str(repo),
        ticket_id=tid,
        branch=branch,
        base_ref=base_ref,
        smoke_command=cmd,
        delete_branch_on_cleanup=is_fake_run,
    )

    workflow_id = f"smoke-{feature_workflow_id(repo, tid)}"
    console.print(
        f"[bold]pravi[/] starting workflow for ticket [cyan]{tid}[/] "
        f"on domain [magenta]{chosen.name}[/] (repo: {repo})"
    )

    try:
        result = asyncio.run(_run_workflow(inp, workflow_id, chosen.name))
    except RPCError as e:
        # ALLOW_DUPLICATE_FAILED_ONLY: a running or successfully-closed
        # workflow with the same ID will refuse to start. Surface a friendly
        # message instead of a stack trace.
        if "WorkflowExecutionAlreadyStarted" in str(e):
            console.print(
                f"[red]error[/] workflow [bold]{workflow_id}[/] is already running "
                f"or completed. Terminate it in the Temporal UI to re-run, "
                f"or wait for it to fail."
            )
            raise typer.Exit(code=1) from e
        raise
    console.print(f"[green]done[/] {result.summary}")


async def _run_workflow(
    inp: SmokeWorkflowInput,
    workflow_id: str,
    domain_name: str,
) -> SmokeWorkflowResult:
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    handle = await client.start_workflow(
        SmokeWorkflow.run,
        inp,
        id=workflow_id,
        task_queue=settings.temporal_task_queue_features,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        search_attributes=TypedSearchAttributes(
            [
                SearchAttributePair(REPO_NAME, repo_slug(inp.repo_path)),
                SearchAttributePair(DOMAIN, domain_name),
                SearchAttributePair(TICKET_ID, inp.ticket_id),
                SearchAttributePair(PRAVI_STATUS, "smoke"),
            ]
        ),
    )
    console.print(f"workflow id: [dim]{handle.id}[/]  (Temporal UI: http://localhost:8233)")
    return await handle.result()


@app.command("list-domains")
def list_domains(
    repo: Annotated[
        Path | None,
        typer.Option(help="Target repo path. Defaults to first PRAVI_TARGET_REPOS entry."),
    ] = None,
    domains_file: Annotated[
        Path | None,
        typer.Option("--domains-file", help="Override path to a domains.yaml."),
    ] = None,
) -> None:
    """List domains declared in a target repo's `.builder/domains.yaml`."""
    settings = get_settings()
    if repo is None:
        if not settings.target_repos:
            raise typer.BadParameter(
                "no --repo provided and PRAVI_TARGET_REPOS is empty",
            )
        repo = settings.target_repos[0]
    registry = DomainRegistry.load(repo.expanduser().resolve(), override_file=domains_file)
    for d in registry.domains:
        console.print(f"[bold]{d.name}[/]  paths={d.paths}  test={d.test!r}")
