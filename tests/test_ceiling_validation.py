"""Server-side cost ceiling validation — `_reject_if_ceiling_exceeds_parent`.

These tests pre-seed a parent ticket (with its own ceiling + some spent
amount via a Run + run_finished Event) and then call the helper to
confirm the right cases raise and the right cases pass through. The
runtime budget rollup is the source of truth — these tests run the real
rollup walk against real DB rows.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from pravi.api.routes import _reject_if_ceiling_exceeds_parent
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


async def _seed_epic_with_ceiling_and_spend(
    *, repo_id: int, external_id: str, ceiling: float, spent: float
) -> int:
    """Insert an epic with `ceiling_usd` + one child task whose
    `run_finished` event payload spends `spent` dollars. Returns the
    epic's id. Mirrors the production rollup setup: spend is computed
    by descending into task runs, not by writing onto the epic
    directly."""
    async with session_scope() as session:
        epic = Ticket(
            repo_id=repo_id,
            external_id=external_id,
            title="epic",
            body="",
            status=TicketStatus.pending,
            kind=TicketKind.epic,
            cost_ceiling_usd=ceiling,
        )
        session.add(epic)
        await session.flush()

        # Spend lives on a child task's run, not the epic itself —
        # rollup descends via descendant_task_ids().
        feature = Ticket(
            repo_id=repo_id,
            external_id=f"{external_id}-f",
            title="feature",
            body="",
            status=TicketStatus.pending,
            kind=TicketKind.feature,
            parent_id=epic.id,
        )
        session.add(feature)
        await session.flush()

        task = Ticket(
            repo_id=repo_id,
            external_id=f"{external_id}-t",
            title="task",
            body="",
            status=TicketStatus.pending,
            kind=TicketKind.task,
            parent_id=feature.id,
        )
        session.add(task)
        await session.flush()

        run = Run(
            ticket_id=task.id,
            kind=RunKind.developer,
            status=RunStatus.succeeded,
        )
        session.add(run)
        await session.flush()

        session.add(
            Event(
                ticket_id=task.id,
                run_id=run.id,
                kind=KIND_RUN_FINISHED,
                message="done",
                payload={"total_cost_usd": spent},
            )
        )
        await session.flush()
        return epic.id


async def test_allows_proposed_under_parent_remaining(test_prefix, test_repo: Repo):
    """Parent ceiling $10, $7 spent → remaining $3. Proposed $2 must
    pass through (no raise)."""
    epic_id = await _seed_epic_with_ceiling_and_spend(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}epic-under",
        ceiling=10.0,
        spent=7.0,
    )
    async with session_scope() as session:
        # No raise.
        await _reject_if_ceiling_exceeds_parent(session, epic_id, 2.0)


async def test_rejects_proposed_over_parent_remaining(test_prefix, test_repo: Repo):
    """Parent ceiling $10, $7 spent → remaining $3. Proposed $5 must
    raise 400 with a useful detail message."""
    epic_id = await _seed_epic_with_ceiling_and_spend(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}epic-over",
        ceiling=10.0,
        spent=7.0,
    )
    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await _reject_if_ceiling_exceeds_parent(session, epic_id, 5.0)
        assert exc_info.value.status_code == 400
        # The error message names the remaining cap so the UI can render
        # a useful "parent has $X.XX remaining" hint.
        assert "$3.00" in exc_info.value.detail
        # And identifies which level binds (epic/feature/self).
        detail_lower = exc_info.value.detail.lower()
        assert any(token in detail_lower for token in ("self", "epic", "feature"))


async def test_skips_when_no_parent_id(test_prefix, test_repo: Repo):
    """Top-level epics have no parent to constrain them. Calling with
    None as parent_id should never raise, even for absurd amounts."""
    async with session_scope() as session:
        await _reject_if_ceiling_exceeds_parent(session, None, 9_999_999.0)


async def test_skips_when_proposed_is_none(test_prefix, test_repo: Repo):
    """proposed=None means 'inherit from parent' — always allowed,
    never validated against the parent's remaining. (The runtime
    rollup will enforce the parent's cap on the child's own runs.)"""
    epic_id = await _seed_epic_with_ceiling_and_spend(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}epic-null",
        ceiling=10.0,
        spent=7.0,
    )
    async with session_scope() as session:
        await _reject_if_ceiling_exceeds_parent(session, epic_id, None)


async def test_boundary_exactly_equals_remaining_allowed(
    test_prefix, test_repo: Repo
):
    """Boundary: proposed == remaining is allowed (today's code uses
    `>` not `>=`). Test makes the boundary contract explicit so a
    refactor doesn't silently tighten it to `>=`."""
    epic_id = await _seed_epic_with_ceiling_and_spend(
        repo_id=test_repo.id,
        external_id=f"{test_prefix}epic-bound",
        ceiling=10.0,
        spent=7.0,
    )
    async with session_scope() as session:
        # $3 == remaining. Should NOT raise.
        await _reject_if_ceiling_exceeds_parent(session, epic_id, 3.0)


async def test_parent_with_unlimited_ceiling_allows_anything(
    test_prefix, test_repo: Repo
):
    """No ceiling on the parent → effective_remaining is None → no
    constraint to violate. Proposed value can be anything."""
    async with session_scope() as session:
        epic = Ticket(
            repo_id=test_repo.id,
            external_id=f"{test_prefix}epic-unbound",
            title="unbound epic",
            body="",
            status=TicketStatus.pending,
            kind=TicketKind.epic,
            # cost_ceiling_usd=None → unlimited at this level
        )
        session.add(epic)
        await session.flush()
        epic_id = epic.id

    async with session_scope() as session:
        await _reject_if_ceiling_exceeds_parent(session, epic_id, 1_000_000.0)
