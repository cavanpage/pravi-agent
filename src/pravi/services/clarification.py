"""Background runner for the architect's clarify call.

Why a service / background task instead of a request-scoped call:

- Clarify takes 10–120s. Tying it to a single browser session means closing
  the tab cancels the call (or worse, leaves no trace of what was paid for).
- The user explicitly asked for "kick off as soon as the epic is submitted,
  keep going even if the user navigates away". This service is that.

The task writes incremental text + final structured result into the
`clarifications` table. The UI polls / fetches that row.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select

from pravi.agents.architects.clarify_parser import parse_clarifications
from pravi.agents.factory import get_architect
from pravi.agents.protocols import ClarifyRequest, DomainBrief
from pravi.config import get_settings
from pravi.db.models import Clarification, ClarifyStatus, Repo, Ticket, TicketKind
from pravi.db.session import session_scope
from pravi.domains.registry import DomainRegistry

log = structlog.get_logger(__name__)

# Throttle DB writes during streaming — every chunk would hammer Postgres.
_FLUSH_INTERVAL_SECONDS = 0.6


async def kickoff_clarification(ticket_id: int) -> int:
    """Insert a Clarification(status=pending) and schedule the background
    task. Returns the new clarification id immediately so the caller can
    redirect / surface it. Safe to call multiple times for the same ticket
    — each call creates a new row; the UI displays the latest.
    """
    async with session_scope() as session:
        # Sanity check the ticket is an epic before we burn tokens.
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError(f"ticket {ticket_id} not found")
        if str(ticket.kind) != TicketKind.epic.value:
            raise ValueError(
                f"clarify only runs on epics (got kind={ticket.kind})"
            )
        row = Clarification(ticket_id=ticket_id, status=ClarifyStatus.pending)
        session.add(row)
        await session.flush()
        clar_id = row.id

    # Fire-and-forget — survives the request that created it.
    asyncio.create_task(_run(clar_id))
    log.info("clarification.kickoff", ticket_id=ticket_id, clarification_id=clar_id)
    return clar_id


async def _build_request(ticket_id: int) -> ClarifyRequest:
    """Snapshot domain config + ticket into a ClarifyRequest. Done inside
    the background task (not in the API request) so we don't hold an
    HTTP request open while reading domains.yaml."""
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

    repo_root = Path(repo_path)
    registry = DomainRegistry.load(repo_root)
    available = [
        DomainBrief(name=d.name, description=d.description, paths=list(d.paths))
        for d in registry.domains
    ]
    context_files: list[str] = []
    for d in registry.domains:
        context_files.extend(d.context_files)

    settings = get_settings()
    return ClarifyRequest(
        repo_path=repo_path,
        repo_name=repo_name,
        epic_title=title,
        epic_body=body,
        available_domains=available,
        default_domain=domain_name,
        domain_context_files=context_files,
        max_wall_seconds=settings.architect_max_wall_seconds,
        max_turns=settings.architect_max_turns,
        max_cost_usd=min(settings.architect_max_cost_usd, 0.5),
    )


async def _set_status(
    clar_id: int,
    status: ClarifyStatus,
    *,
    started: bool = False,
) -> None:
    async with session_scope() as session:
        row = await session.get(Clarification, clar_id)
        if row is None:
            return
        row.status = status
        if started:
            row.started_at = datetime.now(UTC)


async def _flush_progress(clar_id: int, raw_md: str) -> None:
    async with session_scope() as session:
        row = await session.get(Clarification, clar_id)
        if row is None:
            return
        row.raw_md = raw_md


async def _finalize(
    clar_id: int,
    *,
    status: ClarifyStatus,
    raw_md: str,
    questions: list[dict],
    prompt_version: str | None,
    num_turns: int | None,
    duration_ms: int | None,
    total_cost_usd: float | None,
    error: str | None,
) -> None:
    async with session_scope() as session:
        row = await session.get(Clarification, clar_id)
        if row is None:
            return
        row.status = status
        row.raw_md = raw_md
        row.questions = questions
        row.prompt_version = prompt_version
        row.num_turns = num_turns
        row.duration_ms = duration_ms
        row.total_cost_usd = total_cost_usd
        row.error = error
        row.completed_at = datetime.now(UTC)


async def _run(clar_id: int) -> None:
    """Long-running task: ask the architect, write incremental progress,
    finalize. Never raises — failures are written to the row's `error`."""
    try:
        await _set_status(clar_id, ClarifyStatus.running, started=True)

        # Load ticket / build the architect request inside the task so we
        # never hold the originating request handler open.
        async with session_scope() as session:
            clar = await session.get(Clarification, clar_id)
            if clar is None:
                return
            ticket_id = clar.ticket_id
        req = await _build_request(ticket_id)

        raw_buf = ""
        last_flush = time.monotonic()

        async def on_text(delta: str) -> None:
            nonlocal raw_buf, last_flush
            raw_buf += delta
            if time.monotonic() - last_flush >= _FLUSH_INTERVAL_SECONDS:
                await _flush_progress(clar_id, raw_buf)
                last_flush = time.monotonic()

        result = await get_architect().clarify_epic(req, on_text=on_text)

        # If the parser couldn't extract questions from the running text
        # (which is a parser-vs-LLM thing) re-run the parser on the final
        # text so we don't drop a valid result.
        questions = result.questions or parse_clarifications(result.raw_md)[0]
        await _finalize(
            clar_id,
            status=ClarifyStatus.done if result.success else ClarifyStatus.failed,
            raw_md=result.raw_md or raw_buf,
            questions=[
                {"text": q.text, "why": q.why, "options": list(q.options or [])}
                for q in questions
            ],
            prompt_version=result.prompt_version,
            num_turns=result.num_turns,
            duration_ms=result.duration_ms,
            total_cost_usd=result.total_cost_usd,
            error="; ".join(result.errors) if result.errors and not result.success else None,
        )
        log.info(
            "clarification.finished",
            clarification_id=clar_id,
            ticket_id=ticket_id,
            success=result.success,
            questions=len(questions),
            duration_ms=result.duration_ms,
            cost=result.total_cost_usd,
        )
    except Exception as e:
        log.exception("clarification.fatal", clarification_id=clar_id, error=str(e))
        await _finalize(
            clar_id,
            status=ClarifyStatus.failed,
            raw_md="",
            questions=[],
            prompt_version=None,
            num_turns=None,
            duration_ms=None,
            total_cost_usd=None,
            error=f"{type(e).__name__}: {e}",
        )


async def get_latest_for_ticket(ticket_id: int) -> Clarification | None:
    """Latest clarification row for a ticket — what the UI displays."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Clarification)
                .where(Clarification.ticket_id == ticket_id)
                .order_by(Clarification.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            session.expunge(row)
        return row
