"""Per-persona / per-stack spend aggregation — see ADR 0004.

Aggregates `run_finished` event costs by the *task ticket's* persona and
stack. Persona on a feature or epic is informational; what burns money is
the dev run, which only happens on tasks — so we group by `tickets.persona`
for the tickets that have runs against them.

NULL persona resolves to `other`; NULL stack to `unknown`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pravi.db.models import Event, Run, Ticket
from pravi.events import KIND_RUN_FINISHED


@dataclass
class PersonaSpend:
    persona: str  # slug — `other` when null on the ticket
    spent_usd: float
    run_count: int
    ticket_count: int  # distinct tickets that contributed


@dataclass
class StackSpend:
    stack: str  # slug — `unknown` when null
    spent_usd: float
    run_count: int
    ticket_count: int


def _window_start(window: str | None, now: datetime) -> datetime | None:
    """Parse a relative window like `7d` / `30d` / `all`. Returns the
    cutoff `event.at` timestamp, or None for `all`/missing."""
    if not window or window == "all":
        return None
    if window.endswith("d") and window[:-1].isdigit():
        days = int(window[:-1])
        if days > 0:
            return now - timedelta(days=days)
    return None


async def aggregate_by_persona(
    session: AsyncSession,
    *,
    window: str | None = None,
    repo_id: int | None = None,
    now: datetime | None = None,
) -> list[PersonaSpend]:
    """Sum cost grouped by ticket persona. Pure read; no side effects.

    `window` accepts `7d`, `30d`, `all`, or None (= all-time). `repo_id`
    optionally scopes to one repo so the dashboard can show "spend on
    repo X this week" without aggregating across the user's whole tree.
    """
    now = now or datetime.now().astimezone()
    cutoff = _window_start(window, now)

    # Pull the join we need; do the grouping in Python so the JSON path
    # stays portable across PG versions (same pattern as `total_spend`
    # in rollup.py).
    conditions = [Event.kind == KIND_RUN_FINISHED]
    if cutoff is not None:
        conditions.append(Event.at >= cutoff)
    if repo_id is not None:
        conditions.append(Ticket.repo_id == repo_id)

    rows = (
        await session.execute(
            select(Ticket.persona, Ticket.id, Event.payload)
            .join(Run, Run.id == Event.run_id)
            .join(Ticket, Ticket.id == Run.ticket_id)
            .where(and_(*conditions))
        )
    ).all()

    by_persona: dict[str, dict[str, float | set[int]]] = {}
    for persona_slug, ticket_id, payload in rows:
        cost_raw = (payload or {}).get("total_cost_usd")
        if not isinstance(cost_raw, (int, float)) or math.isnan(float(cost_raw)):
            continue
        slug = persona_slug or "other"
        bucket = by_persona.setdefault(
            slug,
            {"spent_usd": 0.0, "run_count": 0, "ticket_ids": set()},
        )
        bucket["spent_usd"] = float(bucket["spent_usd"]) + float(cost_raw)
        bucket["run_count"] = int(bucket["run_count"]) + 1
        tids = bucket["ticket_ids"]
        assert isinstance(tids, set)
        tids.add(ticket_id)

    out: list[PersonaSpend] = []
    for slug, b in by_persona.items():
        tids = b["ticket_ids"]
        assert isinstance(tids, set)
        out.append(
            PersonaSpend(
                persona=slug,
                spent_usd=round(float(b["spent_usd"]), 6),
                run_count=int(b["run_count"]),
                ticket_count=len(tids),
            )
        )
    # Highest-spending personas first — matches the dashboard's reading
    # order.
    out.sort(key=lambda x: x.spent_usd, reverse=True)
    return out


async def aggregate_by_stack(
    session: AsyncSession,
    *,
    window: str | None = None,
    repo_id: int | None = None,
    now: datetime | None = None,
) -> list[StackSpend]:
    """Same as `aggregate_by_persona` but grouped on `tickets.stack`."""
    now = now or datetime.now().astimezone()
    cutoff = _window_start(window, now)

    conditions = [Event.kind == KIND_RUN_FINISHED]
    if cutoff is not None:
        conditions.append(Event.at >= cutoff)
    if repo_id is not None:
        conditions.append(Ticket.repo_id == repo_id)

    rows = (
        await session.execute(
            select(Ticket.stack, Ticket.id, Event.payload)
            .join(Run, Run.id == Event.run_id)
            .join(Ticket, Ticket.id == Run.ticket_id)
            .where(and_(*conditions))
        )
    ).all()

    by_stack: dict[str, dict[str, float | set[int]]] = {}
    for stack_slug, ticket_id, payload in rows:
        cost_raw = (payload or {}).get("total_cost_usd")
        if not isinstance(cost_raw, (int, float)) or math.isnan(float(cost_raw)):
            continue
        slug = stack_slug or "unknown"
        bucket = by_stack.setdefault(
            slug,
            {"spent_usd": 0.0, "run_count": 0, "ticket_ids": set()},
        )
        bucket["spent_usd"] = float(bucket["spent_usd"]) + float(cost_raw)
        bucket["run_count"] = int(bucket["run_count"]) + 1
        tids = bucket["ticket_ids"]
        assert isinstance(tids, set)
        tids.add(ticket_id)

    out: list[StackSpend] = []
    for slug, b in by_stack.items():
        tids = b["ticket_ids"]
        assert isinstance(tids, set)
        out.append(
            StackSpend(
                stack=slug,
                spent_usd=round(float(b["spent_usd"]), 6),
                run_count=int(b["run_count"]),
                ticket_count=len(tids),
            )
        )
    out.sort(key=lambda x: x.spent_usd, reverse=True)
    return out
