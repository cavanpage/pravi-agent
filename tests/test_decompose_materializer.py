"""End-to-end test: decompose_approve materializes Feature + Task rows
with the right persona + stack inheritance (ADR 0004).

Catches: someone reorders `t.persona or f.persona` to `f.persona or
t.persona` (would break explicit task-level override); the materializer
forgets to write `stack` to a task row; a reorder of `Ticket(...)` kwargs
breaks construction.
"""
from __future__ import annotations

from textwrap import dedent

import pytest
from sqlalchemy import select

from pravi.api.routes import decompose_approve
from pravi.api.schemas import DecomposeApproveRequest
from pravi.db.models import Repo, Ticket, TicketKind, TicketStatus
from pravi.db.session import session_scope


@pytest.fixture
async def epic_for_decompose(test_prefix, test_repo: Repo) -> Ticket:
    """Insert a placeholder epic that the materializer will hang
    children off. `db_cleanup` (autouse) removes it after."""
    async with session_scope() as session:
        epic = Ticket(
            repo_id=test_repo.id,
            external_id=f"{test_prefix}epic",
            title="A test epic for materializer",
            body="",
            status=TicketStatus.pending,
            kind=TicketKind.epic,
        )
        session.add(epic)
        await session.flush()
        # Detach for the caller.
        epic_id = epic.id
        ext_id = epic.external_id

    epic.id = epic_id
    epic.external_id = ext_id
    return epic


async def test_materializer_inherits_and_overrides_persona(
    test_prefix, epic_for_decompose
):
    md = dedent(f"""
        ## Summary

        Adding a /users surface with tests.

        ```yaml
        features:
          - title: "{test_prefix}API"
            description: "/users feature"
            persona: "backend"
            stack: "python-fastapi"
            tasks:
              - title: "{test_prefix}Add /users endpoint"
                description: "Wire the route + service"
                # no persona → inherits 'backend' from feature
              - title: "{test_prefix}Tests for /users"
                description: "Unit + integration"
                persona: "tester"
                # stack not set on task → inherits 'python-fastapi'
        ```
    """).strip()

    res = await decompose_approve(
        epic_for_decompose.external_id,
        DecomposeApproveRequest(raw_md=md, approver="test"),
    )

    assert len(res.feature_external_ids) == 1
    assert len(res.task_external_ids) == 2

    async with session_scope() as session:
        feature = (
            await session.execute(
                select(Ticket).where(Ticket.external_id == res.feature_external_ids[0])
            )
        ).scalar_one()
        tasks_q = await session.execute(
            select(Ticket)
            .where(Ticket.external_id.in_(res.task_external_ids))
            .order_by(Ticket.title)
        )
        tasks = list(tasks_q.scalars().all())

    # Feature got both fields from YAML directly.
    assert feature.persona == "backend"
    assert feature.stack == "python-fastapi"

    # Tasks sorted by title — "Add /users endpoint" first alphabetically.
    add_task, test_task = tasks
    assert "Add /users" in add_task.title
    assert "Tests for /users" in test_task.title

    # The "Add" task had no explicit persona → inherits feature's.
    assert add_task.persona == "backend"
    # The "Tests" task overrode persona but not stack.
    assert test_task.persona == "tester"

    # Both tasks inherit stack from feature (neither set it explicitly).
    assert add_task.stack == "python-fastapi"
    assert test_task.stack == "python-fastapi"


async def test_materializer_with_no_persona_writes_null(
    test_prefix, epic_for_decompose
):
    """Pre-ADR-0004 YAML (no persona fields) still works — rows land
    with NULL persona/stack, which resolves to catalog defaults at
    runtime."""
    md = dedent(f"""
        ## Summary

        Plain decomposition with no persona tagging.

        ```yaml
        features:
          - title: "{test_prefix}Feature"
            description: "no persona"
            tasks:
              - title: "{test_prefix}Task"
                description: "no persona"
        ```
    """).strip()

    res = await decompose_approve(
        epic_for_decompose.external_id,
        DecomposeApproveRequest(raw_md=md, approver="test"),
    )

    async with session_scope() as session:
        feature = (
            await session.execute(
                select(Ticket).where(Ticket.external_id == res.feature_external_ids[0])
            )
        ).scalar_one()
        task = (
            await session.execute(
                select(Ticket).where(Ticket.external_id == res.task_external_ids[0])
            )
        ).scalar_one()

    assert feature.persona is None
    assert feature.stack is None
    assert task.persona is None
    assert task.stack is None
