"""Child status rollup — `_child_task_status_counts` bulk-aggregator and
the pure `_derive_parent_status` derivation that turns those counts into
a single-word status for features + epics.
"""
from __future__ import annotations

import pytest

from pravi.api.routes import _child_task_status_counts, _derive_parent_status
from pravi.db.models import Repo, Ticket, TicketKind, TicketStatus
from pravi.db.session import session_scope


async def _mk(
    session,
    *,
    repo_id: int,
    external_id: str,
    kind: TicketKind,
    parent_id: int | None = None,
    status: TicketStatus = TicketStatus.pending,
) -> Ticket:
    t = Ticket(
        repo_id=repo_id,
        external_id=external_id,
        title=external_id,
        body="",
        status=status,
        kind=kind,
        parent_id=parent_id,
    )
    session.add(t)
    await session.flush()
    return t


# ---- _derive_parent_status (pure) -----------------------------------------


def test_derive_returns_none_for_empty_counts():
    assert _derive_parent_status({}) is None


def test_active_work_wins_over_failed():
    """Active work hides nothing — a parent with one failed task but
    others still running should read as "in_progress" so it stays in
    the home page's In-flight section. Failures are surfaced via the
    chips breakdown, not by demoting the whole parent to "failed"."""
    assert _derive_parent_status({"in_progress": 2, "failed": 1}) == "in_progress"
    # Same when work hasn't reached dev yet but a sibling failed earlier
    # (e.g. retry of failed tasks queued, still pending).
    assert _derive_parent_status({"planning": 1, "failed": 1}) == "in_progress"


def test_failed_wins_only_when_nothing_else_is_active():
    """When all subtree work is settled and at least one is failed, the
    derived status IS "failed" — the user should see it in Closed +
    "needs attention"."""
    assert _derive_parent_status({"failed": 3}) == "failed"
    assert _derive_parent_status({"merged": 2, "failed": 1}) == "failed"
    assert _derive_parent_status({"pr_open": 1, "merged": 2, "failed": 1}) == "failed"


def test_pending_with_active_or_done_reads_as_in_progress():
    """A subtree where some tasks are pending and others have started
    or finished should read as "in_progress" — work has begun, more is
    queued. Avoids the same "epic disappears into Closed" pitfall as
    the failure case."""
    assert _derive_parent_status({"pending": 1, "merged": 2}) == "in_progress"
    assert _derive_parent_status({"pending": 1, "failed": 1}) == "in_progress"


def test_derive_in_progress_when_any_active():
    assert _derive_parent_status({"pending": 2, "in_progress": 1}) == "in_progress"
    assert _derive_parent_status({"planning": 1}) == "in_progress"
    assert _derive_parent_status({"plan_approved": 1, "pending": 5}) == "in_progress"


def test_derive_pending_when_all_pending():
    assert _derive_parent_status({"pending": 3}) == "pending"


def test_derive_mix_pending_with_done_is_in_progress():
    """Some tasks done, others still pending — work has clearly started."""
    assert _derive_parent_status({"pending": 1, "merged": 2}) == "in_progress"
    assert _derive_parent_status({"pending": 1, "pr_open": 1}) == "in_progress"


def test_derive_all_merged():
    assert _derive_parent_status({"merged": 3}) == "merged"


def test_derive_all_cancelled():
    assert _derive_parent_status({"cancelled": 2}) == "cancelled"


def test_derive_pr_open_with_some_merged():
    """Mix of PR-open + merged → still 'pr_open' (some PRs awaiting merge)."""
    assert _derive_parent_status({"pr_open": 1, "merged": 2}) == "pr_open"


# ---- _child_task_status_counts (DB) ---------------------------------------


async def test_feature_aggregates_direct_task_children(
    test_prefix, test_repo: Repo
):
    async with session_scope() as session:
        epic = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}e",
            kind=TicketKind.epic,
        )
        feature = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}f",
            kind=TicketKind.feature, parent_id=epic.id,
        )
        # Two pending, one in_progress, one merged
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}t1",
            kind=TicketKind.task, parent_id=feature.id,
            status=TicketStatus.pending,
        )
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}t2",
            kind=TicketKind.task, parent_id=feature.id,
            status=TicketStatus.pending,
        )
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}t3",
            kind=TicketKind.task, parent_id=feature.id,
            status=TicketStatus.in_progress,
        )
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}t4",
            kind=TicketKind.task, parent_id=feature.id,
            status=TicketStatus.merged,
        )

        counts = await _child_task_status_counts(session, [feature.id])

    assert counts[feature.id] == {"pending": 2, "in_progress": 1, "merged": 1}


async def test_epic_aggregates_grandchild_tasks_across_features(
    test_prefix, test_repo: Repo
):
    async with session_scope() as session:
        epic = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}e",
            kind=TicketKind.epic,
        )
        f1 = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}f1",
            kind=TicketKind.feature, parent_id=epic.id,
        )
        f2 = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}f2",
            kind=TicketKind.feature, parent_id=epic.id,
        )
        # f1: 1 pending + 1 failed
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}f1-t1",
            kind=TicketKind.task, parent_id=f1.id, status=TicketStatus.pending,
        )
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}f1-t2",
            kind=TicketKind.task, parent_id=f1.id, status=TicketStatus.failed,
        )
        # f2: 1 pr_open
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}f2-t1",
            kind=TicketKind.task, parent_id=f2.id, status=TicketStatus.pr_open,
        )

        counts = await _child_task_status_counts(session, [epic.id])

    # Epic sees all 3 tasks across both features.
    assert counts[epic.id] == {"pending": 1, "failed": 1, "pr_open": 1}


async def test_aggregator_handles_both_levels_simultaneously(
    test_prefix, test_repo: Repo
):
    """The same call returns counts for both a feature AND its parent
    epic when both are in `parent_ids`. The two queries don't conflict —
    feature counts come from the direct-child query, epic counts come
    from the grandchild query."""
    async with session_scope() as session:
        epic = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}e",
            kind=TicketKind.epic,
        )
        feature = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}f",
            kind=TicketKind.feature, parent_id=epic.id,
        )
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}t1",
            kind=TicketKind.task, parent_id=feature.id,
            status=TicketStatus.pending,
        )
        await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}t2",
            kind=TicketKind.task, parent_id=feature.id,
            status=TicketStatus.in_progress,
        )

        counts = await _child_task_status_counts(
            session, [epic.id, feature.id]
        )

    assert counts[feature.id] == {"pending": 1, "in_progress": 1}
    assert counts[epic.id] == {"pending": 1, "in_progress": 1}


async def test_empty_parent_list_returns_empty(test_prefix, test_repo: Repo):
    async with session_scope() as session:
        counts = await _child_task_status_counts(session, [])
    assert counts == {}


async def test_aggregator_returns_empty_for_parent_with_no_descendants(
    test_prefix, test_repo: Repo
):
    """An epic with no features (no tasks) → empty counts dict, not
    missing key. The serializer relies on the key being present so it
    can fall back to the raw status cleanly."""
    async with session_scope() as session:
        epic = await _mk(
            session, repo_id=test_repo.id, external_id=f"{test_prefix}e",
            kind=TicketKind.epic,
        )
        counts = await _child_task_status_counts(session, [epic.id])

    assert counts[epic.id] == {}


@pytest.mark.parametrize(
    "task_status,expected_derived",
    [
        (TicketStatus.pending, "pending"),
        (TicketStatus.in_progress, "in_progress"),
        (TicketStatus.failed, "failed"),
        (TicketStatus.merged, "merged"),
        (TicketStatus.pr_open, "pr_open"),
    ],
)
async def test_single_task_status_drives_feature_status(
    test_prefix, test_repo: Repo, task_status, expected_derived
):
    """For a feature with exactly one task, the derived status equals
    the task's status (modulo the failed→loudest rule)."""
    async with session_scope() as session:
        epic = await _mk(
            session, repo_id=test_repo.id,
            external_id=f"{test_prefix}e-{task_status.value}",
            kind=TicketKind.epic,
        )
        feature = await _mk(
            session, repo_id=test_repo.id,
            external_id=f"{test_prefix}f-{task_status.value}",
            kind=TicketKind.feature, parent_id=epic.id,
        )
        await _mk(
            session, repo_id=test_repo.id,
            external_id=f"{test_prefix}t-{task_status.value}",
            kind=TicketKind.task, parent_id=feature.id, status=task_status,
        )
        counts = await _child_task_status_counts(session, [feature.id])

    assert _derive_parent_status(counts[feature.id]) == expected_derived
