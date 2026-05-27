"""Background runners for the architect's decompose + plan-draft calls.

Both follow the same pattern as `services.clarification`: persist the call
to a DB row, run it as a fire-and-forget asyncio task, stream raw_md +
tool-use progress markers into the row, finalize on completion. The UI
polls / fetches; closing the tab does not interrupt anything.

One table (`agent_drafts`) backs both modes; `kind` discriminates.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select

from pravi.agents.factory import get_architect
from pravi.agents.protocols import (
    ArchitectRequest,
    ClarificationQA,
    DecomposeRequest,
    DomainBrief,
)
from pravi.config import get_settings
from pravi.db.models import (
    AgentDraft,
    AgentDraftKind,
    AgentDraftStatus,
    Repo,
    Ticket,
    TicketKind,
)
from pravi.db.session import session_scope
from pravi.domains.registry import DomainRegistry

log = structlog.get_logger(__name__)

# Throttle DB writes during streaming. Each text delta would otherwise spam
# Postgres; ~600ms feels live without burning row updates.
_FLUSH_INTERVAL_SECONDS = 0.6


# ---- kickoff API ------------------------------------------------------------


async def kickoff_decompose(
    ticket_id: int, clarifications: list[ClarificationQA] | None = None
) -> int:
    """Start a backgrounded decompose draft. Returns the new draft id."""
    async with session_scope() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError(f"ticket {ticket_id} not found")
        if str(ticket.kind) != TicketKind.epic.value:
            raise ValueError(
                f"decompose only runs on epics (got kind={ticket.kind})"
            )
        row = AgentDraft(
            ticket_id=ticket_id,
            kind=AgentDraftKind.decompose,
            status=AgentDraftStatus.pending,
        )
        session.add(row)
        await session.flush()
        draft_id = row.id

    asyncio.create_task(_run_decompose(draft_id, clarifications or []))
    log.info("agent_draft.kickoff", kind="decompose", ticket_id=ticket_id, draft_id=draft_id)
    return draft_id


async def kickoff_plan_draft(ticket_id: int, domain_name: str | None = None) -> int:
    """Start a backgrounded plan draft. Returns the new draft id."""
    async with session_scope() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError(f"ticket {ticket_id} not found")
        row = AgentDraft(
            ticket_id=ticket_id,
            kind=AgentDraftKind.plan,
            status=AgentDraftStatus.pending,
        )
        session.add(row)
        await session.flush()
        draft_id = row.id

    asyncio.create_task(_run_plan_draft(draft_id, domain_name))
    log.info("agent_draft.kickoff", kind="plan", ticket_id=ticket_id, draft_id=draft_id)
    return draft_id


async def get_latest(ticket_id: int, kind: AgentDraftKind) -> AgentDraft | None:
    """Most recent draft for this ticket+kind — what the UI displays."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(AgentDraft)
                .where(AgentDraft.ticket_id == ticket_id, AgentDraft.kind == kind)
                .order_by(AgentDraft.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            session.expunge(row)
        return row


# ---- shared helpers ---------------------------------------------------------


async def _set_running(draft_id: int) -> None:
    async with session_scope() as session:
        row = await session.get(AgentDraft, draft_id)
        if row is None:
            return
        row.status = AgentDraftStatus.running
        row.started_at = datetime.now(UTC)


async def _flush_progress(draft_id: int, raw_md: str) -> None:
    async with session_scope() as session:
        row = await session.get(AgentDraft, draft_id)
        if row is None:
            return
        row.raw_md = raw_md


async def _finalize(
    draft_id: int,
    *,
    status: AgentDraftStatus,
    raw_md: str,
    payload: dict,
    prompt_version: str | None,
    num_turns: int | None,
    duration_ms: int | None,
    total_cost_usd: float | None,
    error: str | None,
) -> None:
    async with session_scope() as session:
        row = await session.get(AgentDraft, draft_id)
        if row is None:
            return
        row.status = status
        row.raw_md = raw_md
        row.payload = payload
        row.prompt_version = prompt_version
        row.num_turns = num_turns
        row.duration_ms = duration_ms
        row.total_cost_usd = total_cost_usd
        row.error = error
        row.completed_at = datetime.now(UTC)


# ---- decompose runner -------------------------------------------------------


async def _build_decompose_request(
    ticket_id: int, clarifications: list[ClarificationQA]
) -> DecomposeRequest:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Ticket, Repo)
                .join(Repo, Ticket.repo_id == Repo.id)
                .where(Ticket.id == ticket_id)
            )
        ).one_or_none()
        if row is None:
            raise ValueError(f"ticket {ticket_id} not found")
        ticket, repo = row
        title = ticket.title
        body = ticket.body or ""
        domain_name = ticket.domain_name
        repo_path = repo.local_path
        repo_name = repo.name

    registry = DomainRegistry.load(Path(repo_path))
    available = [
        DomainBrief(name=d.name, description=d.description, paths=list(d.paths))
        for d in registry.domains
    ]
    context_files: list[str] = []
    for d in registry.domains:
        context_files.extend(d.context_files)

    settings = get_settings()
    return DecomposeRequest(
        repo_path=repo_path,
        repo_name=repo_name,
        epic_title=title,
        epic_body=body,
        available_domains=available,
        default_domain=domain_name,
        domain_context_files=context_files,
        clarifications=clarifications,
        max_wall_seconds=max(settings.architect_max_wall_seconds, 600),
        max_turns=settings.architect_max_turns,
        max_cost_usd=max(settings.architect_max_cost_usd, 2.0),
    )


async def _run_decompose(
    draft_id: int, clarifications: list[ClarificationQA]
) -> None:
    try:
        await _set_running(draft_id)
        async with session_scope() as session:
            draft = await session.get(AgentDraft, draft_id)
            if draft is None:
                return
            ticket_id = draft.ticket_id
        req = await _build_decompose_request(ticket_id, clarifications)

        raw_buf = ""
        last_flush = time.monotonic()

        async def on_text(delta: str) -> None:
            nonlocal raw_buf, last_flush
            raw_buf += delta
            if time.monotonic() - last_flush >= _FLUSH_INTERVAL_SECONDS:
                await _flush_progress(draft_id, raw_buf)
                last_flush = time.monotonic()

        result = await get_architect().decompose_epic(req, on_text=on_text)

        payload = {
            "features": [
                {
                    "title": f.title,
                    "description": f.description,
                    "domain": f.domain,
                    "depends_on": list(f.depends_on),
                    "tasks": [
                        {"title": t.title, "description": t.description}
                        for t in f.tasks
                    ],
                }
                for f in result.features
            ],
        }
        await _finalize(
            draft_id,
            status=(
                AgentDraftStatus.done if result.success else AgentDraftStatus.failed
            ),
            raw_md=result.raw_md or raw_buf,
            payload=payload,
            prompt_version=result.prompt_version,
            num_turns=result.num_turns,
            duration_ms=result.duration_ms,
            total_cost_usd=result.total_cost_usd,
            error=(
                "; ".join(result.errors)
                if result.errors and not result.success
                else None
            ),
        )
        log.info(
            "agent_draft.decompose.finished",
            draft_id=draft_id,
            success=result.success,
            features=len(result.features),
            duration_ms=result.duration_ms,
            cost=result.total_cost_usd,
        )
    except Exception as e:
        log.exception("agent_draft.decompose.fatal", draft_id=draft_id, error=str(e))
        await _finalize(
            draft_id,
            status=AgentDraftStatus.failed,
            raw_md="",
            payload={},
            prompt_version=None,
            num_turns=None,
            duration_ms=None,
            total_cost_usd=None,
            error=f"{type(e).__name__}: {e}",
        )


# ---- plan draft runner ------------------------------------------------------


async def _build_plan_request(
    ticket_id: int, domain_name: str | None
) -> tuple[ArchitectRequest, str]:
    """Build the architect request + resolved domain name."""
    # Avoid a cycle — db_activity imports from this module's siblings.
    from pravi.activities.db_activity import _load_ancestors, build_ancestral_body

    async with session_scope() as session:
        row = (
            await session.execute(
                select(Ticket, Repo)
                .join(Repo, Ticket.repo_id == Repo.id)
                .where(Ticket.id == ticket_id)
            )
        ).one_or_none()
        if row is None:
            raise ValueError(f"ticket {ticket_id} not found")
        ticket, repo = row
        ancestors = await _load_ancestors(session, ticket)
        merged_body = build_ancestral_body(
            ancestors, str(ticket.kind), ticket.title, ticket.body or ""
        )

    chosen_domain = domain_name or ticket.domain_name
    if not chosen_domain:
        raise ValueError("ticket has no domain and none was specified")
    registry = DomainRegistry.load(Path(repo.local_path))
    chosen = registry.get(chosen_domain)

    settings = get_settings()
    return (
        ArchitectRequest(
            repo_path=repo.local_path,
            repo_name=repo.name,
            domain_name=chosen.name,
            domain_description=chosen.description,
            domain_paths=list(chosen.paths),
            ticket_title=ticket.title,
            ticket_body=merged_body,
            domain_context_files=list(chosen.context_files),
            max_wall_seconds=settings.architect_max_wall_seconds,
            max_turns=settings.architect_max_turns,
            max_cost_usd=settings.architect_max_cost_usd,
        ),
        chosen.name,
    )


async def _run_plan_draft(draft_id: int, domain_name: str | None) -> None:
    try:
        await _set_running(draft_id)
        async with session_scope() as session:
            draft = await session.get(AgentDraft, draft_id)
            if draft is None:
                return
            ticket_id = draft.ticket_id
        req, resolved_domain = await _build_plan_request(ticket_id, domain_name)

        raw_buf = ""
        last_flush = time.monotonic()

        async def on_text(delta: str) -> None:
            nonlocal raw_buf, last_flush
            raw_buf += delta
            if time.monotonic() - last_flush >= _FLUSH_INTERVAL_SECONDS:
                await _flush_progress(draft_id, raw_buf)
                last_flush = time.monotonic()

        result = await get_architect().draft_plan(req, on_text=on_text)

        await _finalize(
            draft_id,
            status=(
                AgentDraftStatus.done if result.success else AgentDraftStatus.failed
            ),
            raw_md=result.plan_md or raw_buf,
            payload={"plan_md": result.plan_md, "domain_name": resolved_domain},
            prompt_version=result.prompt_version,
            num_turns=result.num_turns,
            duration_ms=result.duration_ms,
            total_cost_usd=result.total_cost_usd,
            error=(
                "; ".join(result.errors)
                if result.errors and not result.success
                else None
            ),
        )
        log.info(
            "agent_draft.plan.finished",
            draft_id=draft_id,
            success=result.success,
            duration_ms=result.duration_ms,
            cost=result.total_cost_usd,
        )
    except Exception as e:
        log.exception("agent_draft.plan.fatal", draft_id=draft_id, error=str(e))
        await _finalize(
            draft_id,
            status=AgentDraftStatus.failed,
            raw_md="",
            payload={},
            prompt_version=None,
            num_turns=None,
            duration_ms=None,
            total_cost_usd=None,
            error=f"{type(e).__name__}: {e}",
        )
