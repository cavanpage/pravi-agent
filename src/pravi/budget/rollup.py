"""Spend aggregation + ceiling resolution across the ticket hierarchy."""
from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pravi.config import get_settings
from pravi.db.models import Event, Run, Ticket, TicketKind
from pravi.events import KIND_RUN_FINISHED


@dataclass
class BudgetBreakdown:
    """One ticket's contribution to the rollup chain.

    Used so the UI can show "constrained by epic 'Q3 platform' — $14.20 of
    $20 spent" instead of an opaque "budget exhausted".
    """

    ticket_id: int
    external_id: str
    kind: str
    title: str
    own_ceiling_usd: float | None
    spent_usd: float
    remaining_usd: float | None  # None when ceiling is unlimited


@dataclass
class BudgetRollup:
    """Full picture for one ticket: own state + each ancestor that constrains it."""

    ticket_id: int
    external_id: str
    kind: str
    own_ceiling_usd: float | None
    own_spent_usd: float
    # Effective remaining = min over self + every ancestor that has a ceiling.
    # None = unlimited everywhere up the chain.
    effective_remaining_usd: float | None
    # Which level's ceiling is currently tightest. Useful for UI labels.
    # One of: "self" | "feature" | "epic" | "env_default" | "unlimited"
    constraint_source: str
    chain: list[BudgetBreakdown]


async def descendant_task_ids(session: AsyncSession, ticket_id: int) -> list[int]:
    """All task ticket IDs in the subtree rooted at this ticket.

    Tasks return themselves (length 1). Features return their child tasks.
    Epics return tasks belonging to any of their features. Uses a tight
    two-level walk because the hierarchy is fixed at depth 3.
    """
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return []

    if ticket.kind == TicketKind.task:
        return [ticket.id]

    # Get direct children.
    children = (
        await session.execute(select(Ticket).where(Ticket.parent_id == ticket.id))
    ).scalars().all()

    if ticket.kind == TicketKind.feature:
        return [c.id for c in children if c.kind == TicketKind.task]

    # Epic: walk down two levels.
    feature_ids = [c.id for c in children if c.kind == TicketKind.feature]
    if not feature_ids:
        return []
    grand = (
        await session.execute(
            select(Ticket.id).where(
                Ticket.parent_id.in_(feature_ids),
                Ticket.kind == TicketKind.task,
            )
        )
    ).scalars().all()
    return list(grand)


async def total_spend(session: AsyncSession, ticket_id: int) -> float:
    """Sum of `total_cost_usd` from every run_finished event in the subtree.

    Returns 0.0 for tickets with no runs yet. Missing/null cost values count
    as zero — the SDK occasionally returns None for prompt-cache-only turns.
    """
    task_ids = await descendant_task_ids(session, ticket_id)
    if not task_ids:
        return 0.0

    # SUM((payload->>'total_cost_usd')::float) — done in Python after a fetch
    # to keep the JSON path agnostic across postgres versions. Result set is
    # small (one row per finished run).
    rows = (
        await session.execute(
            select(Event.payload)
            .join(Run, Run.id == Event.run_id)
            .where(
                and_(
                    Run.ticket_id.in_(task_ids),
                    Event.kind == KIND_RUN_FINISHED,
                )
            )
        )
    ).scalars().all()

    total = 0.0
    for payload in rows:
        cost = (payload or {}).get("total_cost_usd")
        if isinstance(cost, (int, float)) and not math.isnan(cost):
            total += float(cost)
    return total


async def _ancestors(session: AsyncSession, ticket: Ticket) -> list[Ticket]:
    """Walk up parent_id chain. Returns nearest-first (feature, then epic)."""
    out: list[Ticket] = []
    current = ticket
    # Hard cap to defend against accidental cycles in bad data.
    for _ in range(8):
        if current.parent_id is None:
            break
        parent = await session.get(Ticket, current.parent_id)
        if parent is None:
            break
        out.append(parent)
        current = parent
    return out


async def cost_rollup(session: AsyncSession, ticket: Ticket) -> BudgetRollup:
    """Build the full rollup for one ticket. Pure read; no side effects."""
    own_spent = await total_spend(session, ticket.id)
    ancestors = await _ancestors(session, ticket)
    settings = get_settings()

    chain: list[BudgetBreakdown] = []
    # Self breakdown first.
    chain.append(
        BudgetBreakdown(
            ticket_id=ticket.id,
            external_id=ticket.external_id,
            kind=str(ticket.kind),
            title=ticket.title,
            own_ceiling_usd=ticket.cost_ceiling_usd,
            spent_usd=own_spent,
            remaining_usd=(
                ticket.cost_ceiling_usd - own_spent
                if ticket.cost_ceiling_usd is not None
                else None
            ),
        )
    )
    # Then each ancestor with its own subtree spend.
    for anc in ancestors:
        anc_spent = await total_spend(session, anc.id)
        chain.append(
            BudgetBreakdown(
                ticket_id=anc.id,
                external_id=anc.external_id,
                kind=str(anc.kind),
                title=anc.title,
                own_ceiling_usd=anc.cost_ceiling_usd,
                spent_usd=anc_spent,
                remaining_usd=(
                    anc.cost_ceiling_usd - anc_spent
                    if anc.cost_ceiling_usd is not None
                    else None
                ),
            )
        )

    # Pick the tightest binding constraint (smallest remaining among
    # whichever levels actually have a ceiling). Fall back to env default
    # only when nothing in the chain sets one.
    constraint_source = "unlimited"
    effective_remaining: float | None = None
    for b in chain:
        if b.remaining_usd is None:
            continue
        if effective_remaining is None or b.remaining_usd < effective_remaining:
            effective_remaining = b.remaining_usd
            # "self" only when it's literally the ticket itself, not its kind name.
            constraint_source = "self" if b.ticket_id == ticket.id else b.kind

    if effective_remaining is None and settings.ticket_cost_ceiling_usd is not None:
        # No ceiling anywhere up the chain — apply env default to self.
        effective_remaining = settings.ticket_cost_ceiling_usd - own_spent
        constraint_source = "env_default"

    return BudgetRollup(
        ticket_id=ticket.id,
        external_id=ticket.external_id,
        kind=str(ticket.kind),
        own_ceiling_usd=ticket.cost_ceiling_usd,
        own_spent_usd=own_spent,
        effective_remaining_usd=effective_remaining,
        constraint_source=constraint_source,
        chain=chain,
    )


async def effective_remaining(session: AsyncSession, ticket_id: int) -> float | None:
    """Convenience wrapper for dev_activity's pre-flight check.

    Returns the smallest remaining budget across self + ancestors. None
    means no ceiling applies anywhere — the run is unconstrained.
    """
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return None
    rollup = await cost_rollup(session, ticket)
    return rollup.effective_remaining_usd
