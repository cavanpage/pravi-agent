"""`_resolve_eligible_tasks` — the engine behind 'start all nested'.

Checks the two parent shapes:
  * feature → all direct pending tasks
  * epic → wave-aware (pending tasks in features whose prerequisite
    features are all done).

Skipped tasks come back with a human-readable reason so the UI can
display "why this didn't start".
"""
from __future__ import annotations

import pytest

from pravi.api.routes import _resolve_eligible_tasks
from pravi.db.models import (
    FeatureDependency,
    Repo,
    Ticket,
    TicketKind,
    TicketStatus,
)
from pravi.db.session import session_scope


async def _mk_ticket(
    session,
    *,
    repo_id: int,
    external_id: str,
    kind: TicketKind,
    parent_id: int | None = None,
    status: TicketStatus = TicketStatus.pending,
    title: str | None = None,
) -> Ticket:
    """Helper: insert a ticket of the given kind. Returns the row."""
    t = Ticket(
        repo_id=repo_id,
        external_id=external_id,
        title=title or external_id,
        body="",
        status=status,
        kind=kind,
        parent_id=parent_id,
    )
    session.add(t)
    await session.flush()
    return t


async def test_feature_parent_returns_all_pending_task_children(
    test_prefix, test_repo: Repo
):
    async with session_scope() as session:
        epic = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}epic",
            kind=TicketKind.epic,
        )
        feature = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f",
            kind=TicketKind.feature,
            parent_id=epic.id,
        )
        # Two pending + one already in progress
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}t1",
            kind=TicketKind.task,
            parent_id=feature.id,
            status=TicketStatus.pending,
        )
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}t2",
            kind=TicketKind.task,
            parent_id=feature.id,
            status=TicketStatus.pending,
        )
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}t3",
            kind=TicketKind.task,
            parent_id=feature.id,
            status=TicketStatus.in_progress,
        )

        report = await _resolve_eligible_tasks(session, feature)

    eligible_ids = sorted(t.external_id for t in report.eligible)
    assert eligible_ids == [f"{test_prefix}t1", f"{test_prefix}t2"]

    skipped_ids = [t.external_id for t, _ in report.ineligible]
    assert skipped_ids == [f"{test_prefix}t3"]
    assert "in_progress" in report.ineligible[0][1]


async def test_epic_parent_no_deps_starts_everything_pending(
    test_prefix, test_repo: Repo
):
    """Epic with no feature dependencies → all features are 'ready'
    (no prerequisites). All pending tasks across all features eligible."""
    async with session_scope() as session:
        epic = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}epic",
            kind=TicketKind.epic,
        )
        f1 = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f1",
            kind=TicketKind.feature,
            parent_id=epic.id,
        )
        f2 = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f2",
            kind=TicketKind.feature,
            parent_id=epic.id,
        )
        for fid_label, fid in (("f1", f1.id), ("f2", f2.id)):
            await _mk_ticket(
                session,
                repo_id=test_repo.id,
                external_id=f"{test_prefix}{fid_label}-t1",
                kind=TicketKind.task,
                parent_id=fid,
            )

        report = await _resolve_eligible_tasks(session, epic)

    eligible_ids = sorted(t.external_id for t in report.eligible)
    assert eligible_ids == [
        f"{test_prefix}f1-t1",
        f"{test_prefix}f2-t1",
    ]
    assert report.ineligible == []


async def test_epic_parent_blocks_tasks_in_dependent_features(
    test_prefix, test_repo: Repo
):
    """f2 depends on f1. f1 still has pending tasks → f2's tasks are
    NOT eligible. Reason mentions the blocker by name."""
    async with session_scope() as session:
        epic = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}epic",
            kind=TicketKind.epic,
        )
        f1 = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f1",
            kind=TicketKind.feature,
            parent_id=epic.id,
            title="API",
        )
        f2 = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f2",
            kind=TicketKind.feature,
            parent_id=epic.id,
            title="UI",
        )
        # f2 depends on f1.
        session.add(FeatureDependency(dependent_id=f2.id, prerequisite_id=f1.id))
        # f1 has a pending task (so it's NOT done).
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f1-t1",
            kind=TicketKind.task,
            parent_id=f1.id,
            status=TicketStatus.pending,
        )
        # f2 has a pending task — should be blocked.
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f2-t1",
            kind=TicketKind.task,
            parent_id=f2.id,
            status=TicketStatus.pending,
        )
        await session.flush()

        report = await _resolve_eligible_tasks(session, epic)

    # f1's task can start; f2's is blocked.
    eligible_ids = [t.external_id for t in report.eligible]
    assert eligible_ids == [f"{test_prefix}f1-t1"]

    skipped = [(t.external_id, reason) for t, reason in report.ineligible]
    assert len(skipped) == 1
    ext, reason = skipped[0]
    assert ext == f"{test_prefix}f2-t1"
    assert "API" in reason  # blocker feature title surfaced


async def test_epic_parent_unblocks_dependent_when_prereq_done(
    test_prefix, test_repo: Repo
):
    """f2 depends on f1. f1's only task is at pr_open (done from the
    dev perspective) → f2 becomes ready → its tasks are eligible."""
    async with session_scope() as session:
        epic = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}epic",
            kind=TicketKind.epic,
        )
        f1 = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f1",
            kind=TicketKind.feature,
            parent_id=epic.id,
        )
        f2 = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f2",
            kind=TicketKind.feature,
            parent_id=epic.id,
        )
        session.add(FeatureDependency(dependent_id=f2.id, prerequisite_id=f1.id))
        # f1's task is done.
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f1-t1",
            kind=TicketKind.task,
            parent_id=f1.id,
            status=TicketStatus.pr_open,
        )
        # f2's task is pending — should now be eligible.
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f2-t1",
            kind=TicketKind.task,
            parent_id=f2.id,
            status=TicketStatus.pending,
        )
        await session.flush()

        report = await _resolve_eligible_tasks(session, epic)

    eligible_ids = [t.external_id for t in report.eligible]
    # f1's task is already done → not eligible (already at pr_open).
    # f2's task is now ready → eligible.
    assert f"{test_prefix}f2-t1" in eligible_ids
    assert f"{test_prefix}f1-t1" not in eligible_ids


async def test_task_parent_yields_empty(test_prefix, test_repo: Repo):
    """Tasks aren't valid parents for batch-start — return empty."""
    async with session_scope() as session:
        task = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}solo",
            kind=TicketKind.task,
        )
        report = await _resolve_eligible_tasks(session, task)

    assert report.eligible == []
    assert report.ineligible == []


@pytest.mark.parametrize(
    "task_status,expect_eligible",
    [
        (TicketStatus.pending, True),
        (TicketStatus.planning, False),
        (TicketStatus.in_progress, False),
        (TicketStatus.pr_open, False),
        (TicketStatus.merged, False),
        (TicketStatus.failed, False),
    ],
)
async def test_only_pending_tasks_are_eligible(
    test_prefix, test_repo: Repo, task_status, expect_eligible
):
    """Anything not at status=pending stays out of the eligible list —
    starting a workflow that's already running would conflict with the
    deterministic workflow_id."""
    async with session_scope() as session:
        epic = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}epic-{task_status.value}",
            kind=TicketKind.epic,
        )
        feature = await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}f-{task_status.value}",
            kind=TicketKind.feature,
            parent_id=epic.id,
        )
        await _mk_ticket(
            session,
            repo_id=test_repo.id,
            external_id=f"{test_prefix}t-{task_status.value}",
            kind=TicketKind.task,
            parent_id=feature.id,
            status=task_status,
        )

        report = await _resolve_eligible_tasks(session, feature)

    assert (len(report.eligible) == 1) is expect_eligible
