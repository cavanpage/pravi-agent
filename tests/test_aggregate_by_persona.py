"""Per-persona spend aggregation — see ADR 0004 FinOps slice.

These tests construct Ticket + Run + Event rows directly and check the
rollup math. Catches: null persona splitting per-ticket instead of
bucketing under `other`; set semantics regressing into list semantics
(would double-count tickets that ran twice); window/repo filters
silently dropped.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pravi.budget import aggregate_by_persona, aggregate_by_stack
from pravi.db.models import (
    Event,
    Repo,
    Run,
    RunKind,
    RunStatus,
    Ticket,
    TicketKind,
    TicketStatus,
)
from pravi.db.session import session_scope
from pravi.events import KIND_RUN_FINISHED


async def _insert_ticket_with_run_finished(
    *,
    repo_id: int,
    external_id: str,
    persona: str | None,
    stack: str | None,
    cost_usd: float,
    event_at: datetime | None = None,
) -> int:
    """Insert one ticket + one Run + one run_finished Event. Returns the
    ticket id (in case tests want to attach more runs to it)."""
    async with session_scope() as session:
        ticket = Ticket(
            repo_id=repo_id,
            external_id=external_id,
            title=external_id,
            body="",
            status=TicketStatus.pending,
            kind=TicketKind.task,
            persona=persona,
            stack=stack,
        )
        session.add(ticket)
        await session.flush()

        run = Run(
            ticket_id=ticket.id,
            kind=RunKind.developer,
            status=RunStatus.succeeded,
        )
        session.add(run)
        await session.flush()

        ev = Event(
            ticket_id=ticket.id,
            run_id=run.id,
            kind=KIND_RUN_FINISHED,
            message="finished",
            payload={"total_cost_usd": cost_usd},
        )
        if event_at is not None:
            ev.at = event_at
        session.add(ev)
        await session.flush()
        return ticket.id


async def _attach_run_finished(
    *, ticket_id: int, cost_usd: float, event_at: datetime | None = None
) -> None:
    """Add another Run + run_finished Event to an existing ticket. Used
    to test that ticket_count is set-cardinality, not run count."""
    async with session_scope() as session:
        run = Run(
            ticket_id=ticket_id,
            kind=RunKind.developer,
            status=RunStatus.succeeded,
        )
        session.add(run)
        await session.flush()
        ev = Event(
            ticket_id=ticket_id,
            run_id=run.id,
            kind=KIND_RUN_FINISHED,
            message="finished",
            payload={"total_cost_usd": cost_usd},
        )
        if event_at is not None:
            ev.at = event_at
        session.add(ev)


async def test_aggregates_by_persona_with_null_to_other(test_prefix, test_repo: Repo):
    """Three runs: two on `backend` tickets, one on a null-persona
    ticket. The null one should aggregate under `other`."""
    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}t1",
        persona="backend",
        stack="python-fastapi",
        cost_usd=1.00,
    )
    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}t2",
        persona=None,  # → `other`
        stack=None,
        cost_usd=0.50,
    )
    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}t3",
        persona="backend",
        stack="python-fastapi",
        cost_usd=0.25,
    )

    async with session_scope() as session:
        rows = await aggregate_by_persona(session, repo_id=test_repo.id)

    by_slug = {r.persona: r for r in rows}
    assert set(by_slug) == {"backend", "other"}
    assert by_slug["backend"].spent_usd == pytest.approx(1.25)
    assert by_slug["backend"].run_count == 2
    assert by_slug["backend"].ticket_count == 2
    assert by_slug["other"].spent_usd == pytest.approx(0.50)
    assert by_slug["other"].ticket_count == 1
    # Sorted desc by spend.
    assert rows[0].persona == "backend"


async def test_ticket_count_uses_set_semantics(test_prefix, test_repo: Repo):
    """One ticket with 3 runs → run_count=3, ticket_count=1. If this
    drops to run_count=ticket_count the dashboard would mis-report
    'three backend tickets touched this week'."""
    tid = await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}repeated",
        persona="backend",
        stack=None,
        cost_usd=0.10,
    )
    await _attach_run_finished(ticket_id=tid, cost_usd=0.20)
    await _attach_run_finished(ticket_id=tid, cost_usd=0.30)

    async with session_scope() as session:
        rows = await aggregate_by_persona(session, repo_id=test_repo.id)

    assert len(rows) == 1
    row = rows[0]
    assert row.persona == "backend"
    assert row.run_count == 3
    assert row.ticket_count == 1
    assert row.spent_usd == pytest.approx(0.60)


async def test_window_filter_drops_old_runs(test_prefix, test_repo: Repo):
    """`window="7d"` includes today's run, excludes a 30-day-old one."""
    now = datetime.now(UTC)
    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}recent",
        persona="backend",
        stack=None,
        cost_usd=1.00,
        event_at=now,
    )
    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}old",
        persona="backend",
        stack=None,
        cost_usd=99.00,
        event_at=now - timedelta(days=30),
    )

    async with session_scope() as session:
        # `now=now` keeps the test deterministic against clock drift.
        rows = await aggregate_by_persona(
            session, window="7d", repo_id=test_repo.id, now=now
        )

    assert len(rows) == 1
    assert rows[0].spent_usd == pytest.approx(1.00)
    assert rows[0].run_count == 1


async def test_repo_id_filter_scopes_to_one_repo(test_prefix, test_repo: Repo):
    """A run against test_repo + a run against an unrelated repo →
    `repo_id=test_repo.id` returns only the first. Without this guard
    the dashboard's per-repo view would cross-contaminate."""
    # Make a second repo + a run against it.
    async with session_scope() as session:
        other = Repo(
            name=f"{test_prefix}other",
            local_path=f"/tmp/{test_prefix}other",
        )
        session.add(other)
        await session.flush()
        other_id = other.id

    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}ours",
        persona="backend",
        stack=None,
        cost_usd=1.00,
    )
    await _insert_ticket_with_run_finished(
        repo_id=other_id,
        external_id=f"{test_prefix}theirs",
        persona="backend",
        stack=None,
        cost_usd=99.00,
    )

    async with session_scope() as session:
        rows = await aggregate_by_persona(session, repo_id=test_repo.id)

    assert len(rows) == 1
    assert rows[0].spent_usd == pytest.approx(1.00)


async def test_aggregate_by_stack_null_bucket_is_unknown(
    test_prefix, test_repo: Repo
):
    """Symmetric: null stack → `unknown` bucket. Quick sanity check that
    the stack rollup uses the same null-coercion as persona's `other`."""
    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}with-stack",
        persona=None,
        stack="python-fastapi",
        cost_usd=0.50,
    )
    await _insert_ticket_with_run_finished(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}no-stack",
        persona=None,
        stack=None,
        cost_usd=0.25,
    )

    async with session_scope() as session:
        rows = await aggregate_by_stack(session, repo_id=test_repo.id)

    by_slug = {r.stack: r for r in rows}
    assert set(by_slug) == {"python-fastapi", "unknown"}
    assert by_slug["unknown"].spent_usd == pytest.approx(0.25)
    assert by_slug["python-fastapi"].spent_usd == pytest.approx(0.50)
