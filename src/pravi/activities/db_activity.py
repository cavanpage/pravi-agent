"""DB-facing Temporal activities. All Postgres I/O the workflow needs.

Workflows never touch the DB directly — they hold IDs and call into these.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from temporalio import activity

from pravi.db.models import Plan, Repo, Ticket, TicketStatus
from pravi.db.session import session_scope

log = structlog.get_logger(__name__)


@dataclass
class TicketRef:
    """Lightweight snapshot of a Ticket — what the workflow needs to run."""

    ticket_id: int
    repo_id: int
    repo_local_path: str
    repo_name: str
    external_id: str
    title: str
    body: str
    domain_name: str | None


@dataclass
class PlanData:
    plan_id: int
    ticket_id: int
    domain_name: str
    content_md: str


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
        return TicketRef(
            ticket_id=ticket.id,
            repo_id=repo.id,
            repo_local_path=repo.local_path,
            repo_name=repo.name,
            external_id=ticket.external_id,
            title=ticket.title,
            body=ticket.body or "",
            domain_name=ticket.domain_name,
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
