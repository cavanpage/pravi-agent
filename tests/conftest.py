"""Shared pytest fixtures for DB-integration tests.

These tests hit the same Postgres pravi normally runs against — there's
no separate test DB. We isolate by **unique prefix per test**: every
ticket gets `external_id = f"{TEST_PREFIX}{...}"`, and the
`db_cleanup` autouse fixture nukes everything with that prefix (and any
Repo whose name starts with it) after the test runs.

The pattern keeps tests independent without needing transactional
rollback (which would conflict with pravi's `session_scope()` opening
its own transaction internally).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from pravi.db import session as db_session
from pravi.db.models import (
    AgentDraft,
    Clarification,
    Event,
    FeatureDependency,
    Plan,
    Repo,
    Run,
    Ticket,
)
from pravi.db.session import session_scope


@pytest_asyncio.fixture(autouse=True)
async def _reset_engine_per_test():
    """SQLAlchemy + asyncpg's connection pool binds to the event loop it
    was first used on. pytest-asyncio creates a fresh loop per test
    (function-scoped), so the cached engine would try to use a dead
    pool on every test after the first → 'another operation is in
    progress'. Reset before each test so a fresh engine is built on
    *this* test's loop."""
    if db_session._engine is not None:
        await db_session._engine.dispose()
    db_session._engine = None
    db_session._sessionmaker = None
    yield
    if db_session._engine is not None:
        await db_session._engine.dispose()
    db_session._engine = None
    db_session._sessionmaker = None


@pytest.fixture
def test_prefix() -> str:
    """Unique per-test prefix used as the external_id prefix for any
    Tickets created during the test, and as the name prefix for any
    Repos. Lets `db_cleanup` reliably target only this test's rows."""
    return f"test-{uuid.uuid4().hex[:8]}-"


@pytest_asyncio.fixture(autouse=True)
async def db_cleanup(test_prefix):
    """Delete in dependency order: Events → Runs → Plans → AgentDrafts →
    Clarifications → FeatureDependencies → Tickets → Repos. Runs even on
    test failure (autouse). Bounded to rows matching `test_prefix` so
    development data sitting in the same DB stays untouched."""
    yield

    async with session_scope() as session:
        # Find repos created by this test (by name prefix).
        rids_q = await session.execute(
            select(Repo.id).where(Repo.name.like(f"{test_prefix}%"))
        )
        repo_ids = [rid for (rid,) in rids_q.all()]

        # Find ALL tickets in those test repos — catches both the rows
        # the test inserted directly (prefix-matched) AND the rows the
        # materializer auto-generated with `f-xxx` / `t-yyy` IDs.
        if repo_ids:
            tids_q = await session.execute(
                select(Ticket.id).where(Ticket.repo_id.in_(repo_ids))
            )
        else:
            tids_q = await session.execute(
                select(Ticket.id).where(Ticket.external_id.like(f"{test_prefix}%"))
            )
        ticket_ids = [tid for (tid,) in tids_q.all()]

        if ticket_ids:
            await session.execute(delete(Event).where(Event.ticket_id.in_(ticket_ids)))
            await session.execute(delete(Run).where(Run.ticket_id.in_(ticket_ids)))
            await session.execute(delete(Plan).where(Plan.ticket_id.in_(ticket_ids)))
            await session.execute(
                delete(AgentDraft).where(AgentDraft.ticket_id.in_(ticket_ids))
            )
            await session.execute(
                delete(Clarification).where(Clarification.ticket_id.in_(ticket_ids))
            )
            await session.execute(
                delete(FeatureDependency).where(
                    (FeatureDependency.dependent_id.in_(ticket_ids))
                    | (FeatureDependency.prerequisite_id.in_(ticket_ids))
                )
            )
            await session.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))

        if repo_ids:
            await session.execute(delete(Repo).where(Repo.id.in_(repo_ids)))


@pytest_asyncio.fixture
async def test_repo(test_prefix, tmp_path) -> Repo:
    """Insert a throwaway Repo row, return it with attributes accessible
    outside the session. Also seeds a real on-disk directory at
    `tmp_path` with a minimal `.builder/domains.yaml` so any code that
    calls `DomainRegistry.load()` against this repo doesn't 400. Cleaned
    up by `db_cleanup` + pytest's tmp_path teardown."""
    # Minimal domains.yaml — one domain matching the whole tree so
    # decompose/plan flows that look up a default domain have one.
    (tmp_path / ".builder").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".builder" / "domains.yaml").write_text(
        "domains:\n"
        "  - name: shared\n"
        "    description: test domain\n"
        "    paths:\n"
        "      - '.'\n"
    )
    # `git ls-files` is used by context.py; make this a real (empty) git
    # repo so commands that touch git don't blow up on tests that don't
    # need it. Cheap.
    import subprocess

    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )

    async with session_scope() as session:
        repo = Repo(
            name=f"{test_prefix}repo",
            local_path=str(tmp_path),
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id
        local_path = repo.local_path
        name = repo.name

    repo.id = repo_id
    repo.name = name
    repo.local_path = local_path
    return repo
