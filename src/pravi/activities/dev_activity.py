"""Temporal activity that runs the developer agent inside a worktree.

Routes to the `pravi-llm` task queue (set by the workflow at execute time)
so concurrency caps on this pool actually bound LLM spend.

Retry policy: maximum_attempts=1 is enforced at the *workflow* level via the
execute_activity call — the bounded dev/test loop is the only legitimate
form of "retry" for LLM work.

If the workflow passes `ticket_id`, this activity also:
  - Creates a Run row at start, finalises it at end.
  - Streams each transcript entry to the per-ticket NOTIFY channel via
    `pravi.events.emit` so the web UI's <LiveRunPanel> renders sub-second
    telemetry. Standalone (CLI-only) callers can pass `ticket_id=None`
    and event emission is skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from temporalio import activity

from pravi.agents.factory import get_dev_agent
from pravi.agents.protocols import DevRunRequest, DevRunResult
from pravi.budget import effective_remaining
from pravi.config import get_settings
from pravi.db.models import Run, RunKind, RunStatus
from pravi.db.session import session_scope
from pravi.domains.registry import DomainRegistry
from pravi.events import KIND_RUN_FINISHED, KIND_RUN_STARTED, emit_event
from pravi.prompts.developer import VERSION as DEV_PROMPT_VERSION
from pravi.prompts.developer import system_prompt as build_system_prompt

log = structlog.get_logger(__name__)


@dataclass
class DevActivityRequest:
    """All inputs to a single developer-agent run.

    The workflow snapshots the relevant domain config and passes it in here
    so the activity is fully self-contained (no I/O to load config) — this
    keeps the activity replayable and isolates it from .builder/domains.yaml
    drift mid-run.

    ``ticket_id`` is optional: when present the activity records a Run row
    and pushes live events to Postgres NOTIFY. Standalone DevWorkflow runs
    (which have no ticket) pass None and skip both.
    """

    repo_path: str
    repo_name: str
    worktree_path: str
    domain_name: str
    domain_description: str
    domain_paths: list[str]
    task: str
    ticket_id: int | None = None


@dataclass
class DevActivityResult:
    success: bool
    summary: str
    prompt_version: str
    stop_reason: str | None
    num_turns: int
    duration_ms: int
    total_cost_usd: float | None
    session_id: str | None
    run_id: int | None = None
    transcript_kinds: list[str] = field(default_factory=list)
    tool_uses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _build_request(req: DevActivityRequest) -> DevRunRequest:
    settings = get_settings()
    sp = build_system_prompt(
        repo_name=req.repo_name,
        domain_name=req.domain_name,
        domain_description=req.domain_description,
        domain_paths=req.domain_paths,
        cwd=req.worktree_path,
    )
    return DevRunRequest(
        cwd=req.worktree_path,
        system_prompt=sp,
        user_prompt=req.task,
        max_wall_seconds=settings.dev_max_wall_seconds,
        max_turns=settings.dev_max_turns,
        max_cost_usd=settings.dev_max_cost_usd,
    )


async def _create_run_row(ticket_id: int) -> int:
    """Mark a new developer Run as started; return its id."""
    async with session_scope() as session:
        row = Run(
            ticket_id=ticket_id,
            kind=RunKind.developer,
            status=RunStatus.started,
            prompt_version=DEV_PROMPT_VERSION,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _finalise_run_row(run_id: int, result: DevRunResult) -> None:
    """Stamp ended_at + final status on the Run row."""
    async with session_scope() as session:
        row = await session.get(Run, run_id)
        if row is None:
            log.warning("dev_activity.run_row_missing", run_id=run_id)
            return
        row.status = RunStatus.succeeded if result.success else RunStatus.failed
        row.ended_at = datetime.now(UTC)
        row.error = "; ".join(result.errors)[:2000] if result.errors else None


async def _refuse_for_budget(
    *,
    req: DevActivityRequest,
    run_id: int,
    sdk_req: DevRunRequest,
    budget_remaining: float,
) -> DevActivityResult:
    """Short-circuit when the cumulative ceiling is already at/past zero.

    Stamps the Run as budget_exhausted and emits run_started + run_finished
    so the UI's LiveRunPanel and the /runs dashboard show what happened
    without burning a single token.
    """
    assert req.ticket_id is not None
    error_msg = (
        f"budget exhausted: remaining ${budget_remaining:.4f} (≤ 0). "
        f"Raise cost_ceiling_usd on the ticket or an ancestor to retry."
    )
    log.warning(
        "dev_activity.budget_refused",
        ticket_id=req.ticket_id,
        run_id=run_id,
        budget_remaining_usd=budget_remaining,
    )
    async with session_scope() as session:
        row = await session.get(Run, run_id)
        if row is not None:
            row.status = RunStatus.budget_exhausted
            row.ended_at = datetime.now(UTC)
            row.error = error_msg

        await emit_event(
            session,
            ticket_id=req.ticket_id,
            run_id=run_id,
            kind=KIND_RUN_STARTED,
            message="refused: budget exhausted",
            payload={"budget_remaining_usd": budget_remaining},
        )
        await emit_event(
            session,
            ticket_id=req.ticket_id,
            run_id=run_id,
            kind=KIND_RUN_FINISHED,
            message=error_msg,
            payload={
                "success": False,
                "stop_reason": "budget_exhausted",
                "num_turns": 0,
                "duration_ms": 0,
                "total_cost_usd": 0.0,
                "budget_remaining_usd": budget_remaining,
                "errors": [error_msg],
            },
        )
    return DevActivityResult(
        success=False,
        summary=error_msg,
        prompt_version=DEV_PROMPT_VERSION,
        stop_reason="budget_exhausted",
        num_turns=0,
        duration_ms=0,
        total_cost_usd=0.0,
        session_id=None,
        run_id=run_id,
        errors=[error_msg],
    )


def _make_event_sink(ticket_id: int, run_id: int):
    """Closure that emits live events for one (ticket, run) pair."""

    async def sink(kind: str, message: str, payload: dict[str, Any] | None) -> None:
        async with session_scope() as session:
            await emit_event(
                session,
                ticket_id=ticket_id,
                run_id=run_id,
                kind=kind,
                message=message,
                payload=payload,
            )

    return sink


@activity.defn
async def run_dev(req: DevActivityRequest) -> DevActivityResult:
    if not Path(req.worktree_path).is_dir():
        raise FileNotFoundError(f"worktree missing: {req.worktree_path}")

    sdk_req = _build_request(req)
    log.info(
        "dev_activity.start",
        repo=req.repo_name,
        domain=req.domain_name,
        cwd=req.worktree_path,
        ticket_id=req.ticket_id,
        max_wall_seconds=sdk_req.max_wall_seconds,
        max_turns=sdk_req.max_turns,
        max_cost_usd=sdk_req.max_cost_usd,
        prompt_version=DEV_PROMPT_VERSION,
    )

    run_id: int | None = None
    event_sink = None
    budget_remaining: float | None = None
    if req.ticket_id is not None:
        run_id = await _create_run_row(req.ticket_id)

        # Pre-flight budget check: refuse if any ceiling up the chain is
        # already exhausted. Otherwise, clamp the per-run cap down to the
        # tightest remaining budget so a single run can't blow past it.
        async with session_scope() as session:
            budget_remaining = await effective_remaining(session, req.ticket_id)

        if budget_remaining is not None and budget_remaining <= 0:
            return await _refuse_for_budget(
                req=req,
                run_id=run_id,
                sdk_req=sdk_req,
                budget_remaining=budget_remaining,
            )

        if budget_remaining is not None:
            sdk_req = DevRunRequest(
                cwd=sdk_req.cwd,
                system_prompt=sdk_req.system_prompt,
                user_prompt=sdk_req.user_prompt,
                max_wall_seconds=sdk_req.max_wall_seconds,
                max_turns=sdk_req.max_turns,
                max_cost_usd=min(sdk_req.max_cost_usd, budget_remaining),
                model=sdk_req.model,
            )

        async with session_scope() as session:
            await emit_event(
                session,
                ticket_id=req.ticket_id,
                run_id=run_id,
                kind=KIND_RUN_STARTED,
                message=f"dev agent started in {req.worktree_path}",
                payload={
                    "domain": req.domain_name,
                    "prompt_version": DEV_PROMPT_VERSION,
                    "max_turns": sdk_req.max_turns,
                    "max_cost_usd": sdk_req.max_cost_usd,
                    "budget_remaining_usd": budget_remaining,
                },
            )
        event_sink = _make_event_sink(req.ticket_id, run_id)

    dev_agent = get_dev_agent()
    result: DevRunResult = await dev_agent.run(
        sdk_req,
        heartbeat=activity.heartbeat,
        event_sink=event_sink,
    )

    tool_uses = [t.tool_name for t in result.transcript if t.kind == "tool_use" and t.tool_name]
    transcript_kinds = [t.kind for t in result.transcript]

    log.info(
        "dev_activity.finished",
        success=result.success,
        stop_reason=result.stop_reason,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
        total_cost_usd=result.total_cost_usd,
        tool_uses=tool_uses[:20],
        errors=result.errors,
    )

    if req.ticket_id is not None and run_id is not None:
        await _finalise_run_row(run_id, result)
        async with session_scope() as session:
            await emit_event(
                session,
                ticket_id=req.ticket_id,
                run_id=run_id,
                kind=KIND_RUN_FINISHED,
                message=(
                    f"dev agent finished: success={result.success}, "
                    f"turns={result.num_turns}"
                ),
                payload={
                    "success": result.success,
                    "stop_reason": result.stop_reason,
                    "num_turns": result.num_turns,
                    "duration_ms": result.duration_ms,
                    "total_cost_usd": result.total_cost_usd,
                    "errors": result.errors,
                },
            )

    summary = result.result_text or (
        "; ".join(result.errors) if result.errors else "no output"
    )
    return DevActivityResult(
        success=result.success,
        summary=summary[:2000],
        prompt_version=DEV_PROMPT_VERSION,
        stop_reason=result.stop_reason,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
        total_cost_usd=result.total_cost_usd,
        session_id=result.session_id,
        run_id=run_id,
        transcript_kinds=transcript_kinds,
        tool_uses=tool_uses,
        errors=result.errors,
    )


def build_request_from_registry(
    *,
    repo_path: str,
    worktree_path: str,
    domain_name: str,
    task: str,
    ticket_id: int | None = None,
    domains_file: Path | None = None,
) -> DevActivityRequest:
    """Helper for callers (CLI, workflow) — looks up the domain and packages
    the snapshot into a DevActivityRequest."""
    repo = Path(repo_path).expanduser().resolve()
    registry = DomainRegistry.load(repo, override_file=domains_file)
    domain = registry.get(domain_name)
    return DevActivityRequest(
        repo_path=str(repo),
        repo_name=repo.name,
        worktree_path=worktree_path,
        domain_name=domain.name,
        domain_description=domain.description,
        domain_paths=list(domain.paths),
        task=task,
        ticket_id=ticket_id,
    )
