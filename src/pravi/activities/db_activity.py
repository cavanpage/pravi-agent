"""DB-facing Temporal activities. All Postgres I/O the workflow needs.

Workflows never touch the DB directly — they hold IDs and call into these.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from pravi.db.models import Plan, Repo, Ticket, TicketStatus
from pravi.db.session import session_scope

log = structlog.get_logger(__name__)


@dataclass
class AncestorRef:
    """A parent or grandparent ticket — used to merge into the architect prompt."""

    external_id: str
    title: str
    body: str
    kind: str  # epic | feature | task


@dataclass
class TicketRef:
    """Lightweight snapshot of a Ticket — what the workflow needs to run.

    `ancestral_body_md` is the task body with parent + grandparent context
    prepended; the architect uses it instead of `body` for hierarchical
    tickets. For standalone (parentless) tickets it equals `body`.
    """

    ticket_id: int
    repo_id: int
    # Optional now (ADR 0003): for the sandbox seam, the workflow passes
    # `repo_id` to `provision_sandbox` and lets the sandbox resolve where
    # the work happens. Kept on the snapshot for diagnostics + legacy
    # CLI paths that still consume a path directly.
    repo_local_path: str | None
    repo_name: str
    external_id: str
    title: str
    body: str
    domain_name: str | None
    kind: str = "task"
    parent_id: int | None = None
    ancestors: list[AncestorRef] = field(default_factory=list)
    ancestral_body_md: str = ""
    # Persona + stack — see ADR 0004. Both nullable; the dev agent's
    # prompt builder falls back to generic on null.
    persona: str | None = None
    stack: str | None = None


@dataclass
class PlanData:
    plan_id: int
    ticket_id: int
    domain_name: str
    content_md: str


async def _load_ancestors(session: AsyncSession, ticket: Ticket) -> list[AncestorRef]:
    """Walk parent chain from `ticket` upward to root; return root-first."""
    chain: list[AncestorRef] = []
    seen: set[int] = {ticket.id}
    cursor = ticket
    while cursor.parent_id is not None:
        if cursor.parent_id in seen:
            log.warning("ticket.cycle_detected", ticket_id=ticket.id)
            break
        seen.add(cursor.parent_id)
        parent = await session.get(Ticket, cursor.parent_id)
        if parent is None:
            break
        chain.append(
            AncestorRef(
                external_id=parent.external_id,
                title=parent.title,
                body=parent.body or "",
                kind=str(parent.kind),
            )
        )
        cursor = parent
    chain.reverse()  # root (epic) first, immediate parent last
    return chain


def build_ancestral_body(
    ancestors: list[AncestorRef],
    self_kind: str,
    self_title: str,
    self_body: str,
) -> str:
    """Concatenate ancestor bodies above the ticket's own body for the architect.

    Format is plain Markdown — each level a heading, then the body. Empty
    bodies are noted to keep section structure obvious.
    """
    if not ancestors:
        return self_body or ""
    parts: list[str] = []
    for a in ancestors:
        parts.append(f"# {a.kind.capitalize()}: {a.title}")
        parts.append((a.body or "_(no description)_").strip())
        parts.append("")
    parts.append(f"# {self_kind.capitalize()}: {self_title}")
    parts.append((self_body or "_(no description)_").strip())
    return "\n".join(parts).strip()


@activity.defn
async def load_ticket(ticket_id: int) -> TicketRef:
    async with session_scope() as session:
        stmt = (
            select(Ticket, Repo)
            .join(Repo, Ticket.repo_id == Repo.id)
            .where(Ticket.id == ticket_id)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            raise ValueError(f"ticket {ticket_id} not found")
        ticket, repo = row
        ancestors = await _load_ancestors(session, ticket)
        merged = build_ancestral_body(
            ancestors,
            str(ticket.kind),
            ticket.title,
            ticket.body or "",
        )
        return TicketRef(
            ticket_id=ticket.id,
            repo_id=repo.id,
            repo_local_path=repo.local_path,
            repo_name=repo.name,
            external_id=ticket.external_id,
            title=ticket.title,
            body=ticket.body or "",
            domain_name=ticket.domain_name,
            kind=str(ticket.kind),
            parent_id=ticket.parent_id,
            ancestors=ancestors,
            ancestral_body_md=merged,
            persona=ticket.persona,
            stack=ticket.stack,
        )


@activity.defn
async def load_plan(plan_id: int) -> PlanData:
    async with session_scope() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            raise ValueError(f"plan {plan_id} not found")
        return PlanData(
            plan_id=plan.id,
            ticket_id=plan.ticket_id,
            domain_name=plan.domain_name,
            content_md=plan.content_md,
        )


@dataclass
class SynthesizePlanRequest:
    ticket_id: int
    domain_name: str
    approver: str = "auto"


@activity.defn
async def synthesize_plan_from_body(req: SynthesizePlanRequest) -> PlanData:
    """Materialize a Plan row from a ticket's body — no architect call.

    Used by the batch-start flow at feature/epic level: tasks created via
    decompose already carry their per-task description (the architect's
    output at decompose time), so re-running the architect at task level
    is duplicate work. The dev agent reads the body directly and starts
    implementing. Human review happens at PR time, not plan-approve time.

    Idempotent: returns the existing Plan if one's already been written
    for this ticket. Domains.yaml snapshot is captured for the audit
    record, same as the regular approve flow.
    """
    from pravi.domains.registry import DomainRegistry

    async with session_scope() as session:
        ticket = await session.get(Ticket, req.ticket_id)
        if ticket is None:
            raise ValueError(f"ticket {req.ticket_id} not found")
        body_md = ticket.body or "(no body)"

        # Reuse the first existing Plan for this ticket if one exists —
        # makes the activity idempotent under workflow replay.
        existing = (
            await session.execute(
                select(Plan).where(Plan.ticket_id == req.ticket_id).order_by(Plan.id)
            )
        ).scalars().first()
        if existing is not None:
            return PlanData(
                plan_id=existing.id,
                ticket_id=existing.ticket_id,
                domain_name=existing.domain_name,
                content_md=existing.content_md,
            )

        # Domain snapshot — same shape as the regular approve route writes.
        from pathlib import Path as _P

        repo = await session.get(type(ticket).repo.property.mapper.class_, ticket.repo_id)  # type: ignore[arg-type]
        domain_snapshot: dict = {}
        if repo is not None and repo.local_path:
            try:
                registry = DomainRegistry.load(_P(repo.local_path))
                d = registry.get(req.domain_name)
                domain_snapshot = {
                    "name": d.name,
                    "description": d.description,
                    "paths": list(d.paths),
                    "test": d.test,
                    "build": d.build,
                }
            except Exception:
                # Best-effort — a missing/unloadable domains.yaml shouldn't
                # block the dev run from starting.
                domain_snapshot = {"name": req.domain_name}

        content_md = (
            "# Plan (auto-synthesized from task body)\n\n"
            "_This task was started without a separate architect plan-draft "
            "step — review happens at PR time. The task body below is what "
            "the dev agent works from._\n\n"
            "---\n\n"
            f"{body_md}\n"
        )
        plan = Plan(
            ticket_id=req.ticket_id,
            domain_name=req.domain_name,
            domain_snapshot=domain_snapshot,
            content_md=content_md,
            approved_at=datetime.now(UTC),
            approved_by=req.approver,
        )
        session.add(plan)
        await session.flush()
        log.info(
            "plan.synthesized",
            ticket_id=req.ticket_id,
            plan_id=plan.id,
            domain=req.domain_name,
        )
        return PlanData(
            plan_id=plan.id,
            ticket_id=plan.ticket_id,
            domain_name=plan.domain_name,
            content_md=plan.content_md,
        )


@dataclass
class TicketStatusUpdate:
    ticket_id: int
    status: str
    workflow_id: str | None = None


@activity.defn
async def update_ticket_status(req: TicketStatusUpdate) -> None:
    """Idempotent status writeback so the DB reflects workflow progress."""
    async with session_scope() as session:
        ticket = await session.get(Ticket, req.ticket_id)
        if ticket is None:
            raise ValueError(f"ticket {req.ticket_id} not found")
        try:
            ticket.status = TicketStatus(req.status)
        except ValueError as e:
            raise ValueError(f"invalid ticket status {req.status!r}") from e
        if req.workflow_id is not None:
            ticket.workflow_id = req.workflow_id
        log.info(
            "ticket.status_updated",
            ticket_id=req.ticket_id,
            status=req.status,
            workflow_id=req.workflow_id,
        )
