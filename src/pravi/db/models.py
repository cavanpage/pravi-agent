from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TicketStatus(StrEnum):
    pending = "pending"
    planning = "planning"
    plan_approved = "plan_approved"
    in_progress = "in_progress"
    pr_open = "pr_open"
    merged = "merged"
    failed = "failed"
    cancelled = "cancelled"


class TicketKind(StrEnum):
    """Three-level hierarchy: Epic > Feature > Task.

    - **Epic**: top-level container (no parent). Holds high-level intent.
    - **Feature**: child of an Epic. Groups related tasks.
    - **Task**: leaf; the unit that runs a FeatureWorkflow. Parent is a
      Feature (or None for standalone tasks).

    Only tasks execute today; epics + features are organizational. (Auto-
    decomposition workflow for epics lands in a follow-up slice.)
    """

    epic = "epic"
    feature = "feature"
    task = "task"


class Repo(Base, TimestampMixin):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    github_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    tickets: Mapped[list[Ticket]] = relationship(back_populates="repo")


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[TicketStatus] = mapped_column(
        String(32), default=TicketStatus.pending, nullable=False
    )
    workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(nullable=True)
    # If imported from a GitHub issue: the original issue URL. Surfaced in
    # the UI as a "from GitHub #N" chip linking back to the source.
    github_issue_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Hierarchy. `kind` defaults to "task" so existing rows are unaffected by
    # the migration. `parent_id` is nullable: epics never have one, features
    # always do, tasks may (standalone) or may not.
    kind: Mapped[TicketKind] = mapped_column(
        String(16), default=TicketKind.task, nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    # Cumulative spend cap (USD). Null = inherit from parent → env default →
    # unlimited. Enforced pre-flight before each dev run; the SDK's per-run
    # cap is also clamped to the remaining budget so a single run can't
    # blow through the ceiling.
    cost_ceiling_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    repo: Mapped[Repo] = relationship(back_populates="tickets")
    plans: Mapped[list[Plan]] = relationship(back_populates="ticket")
    runs: Mapped[list[Run]] = relationship(back_populates="ticket")
    events: Mapped[list[Event]] = relationship(back_populates="ticket")

    parent: Mapped[Ticket | None] = relationship(
        "Ticket",
        remote_side="Ticket.id",
        back_populates="children",
        foreign_keys=[parent_id],
    )
    children: Mapped[list[Ticket]] = relationship(
        "Ticket",
        back_populates="parent",
        foreign_keys=[parent_id],
    )

    __table_args__ = (
        Index("ix_tickets_parent_id", "parent_id"),
        Index("ix_tickets_repo_id_kind", "repo_id", "kind"),
    )


class GitHubConnection(Base, TimestampMixin):
    """Persisted OAuth token for talking to GitHub on the user's behalf.

    Single-user local dev: we keep the latest non-revoked row and treat it
    as "the connection". Logout sets `revoked_at`. A re-auth inserts a fresh
    row rather than mutating the existing one, so we have an audit trail
    if anything weird happens (you got a 401, you re-auth, you see two
    rows — the new one is active).
    """

    __tablename__ = "github_connections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    # GitHub returns these as a space-separated string in the OAuth response.
    scopes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    github_user_login: Mapped[str] = mapped_column(String(255), nullable=False)
    github_user_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Latest-non-revoked lookup is the hot path.
        Index("ix_github_connections_revoked_at_id", "revoked_at", "id"),
    )


class ClarifyStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class Clarification(Base, TimestampMixin):
    """Persistent record of one architect 'clarify' call for an epic.

    Stored in DB (rather than streamed-and-forgotten) so the user can close
    the tab, navigate away, or even reload the page while the architect is
    thinking. The latest row per ticket is what the UI displays; re-clarifying
    inserts a fresh row.

    `raw_md` is updated incrementally as the architect streams tokens — the
    UI partial-parses it for progressive display while polling.
    """

    __tablename__ = "clarifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ClarifyStatus] = mapped_column(
        String(16), default=ClarifyStatus.pending, nullable=False
    )
    raw_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Parsed question list once status=done. `[{text, why}]`.
    questions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    num_turns: Mapped[int | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # "Latest clarification for ticket" query — descending id is server's
        # tiebreaker since `created_at` resolution is sub-second.
        Index("ix_clarifications_ticket_id_id", "ticket_id", "id"),
    )


class AgentDraftKind(StrEnum):
    """Which agent kickoff this draft represents. Used as a discriminator so
    one table can persist every long-running architect call (decompose,
    plan-draft, …) — keeps the persistence + streaming + UI-polling pattern
    uniform across modes."""

    decompose = "decompose"
    plan = "plan"


class AgentDraftStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class AgentDraft(Base, TimestampMixin):
    """Persistent record of one architect 'draft' call (decompose or plan).

    Same lifecycle contract as Clarification — the row is created
    immediately, a background task writes `raw_md` progressively while the
    architect streams, then finalizes with parsed `payload` (e.g. the
    feature/task tree for decompose) on completion. Survives tab close.

    The latest row per (ticket_id, kind) is what the UI displays. A
    re-draft inserts a new row; old ones stay for audit.
    """

    __tablename__ = "agent_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[AgentDraftKind] = mapped_column(String(16), nullable=False)
    status: Mapped[AgentDraftStatus] = mapped_column(
        String(16), default=AgentDraftStatus.pending, nullable=False
    )
    # Streamed architect output (markdown + tool-use comment markers).
    raw_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Parsed result shape — varies by kind:
    #   decompose: {"features": [{title, description, domain, depends_on,
    #                              tasks: [{title, description}]}, ...]}
    #   plan:      {"plan_md": "...", "domain_name": "..."}
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    num_turns: Mapped[int | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # "Latest draft for ticket+kind" — covers the polling query.
        Index("ix_agent_drafts_ticket_kind_id", "ticket_id", "kind", "id"),
    )


class FeatureDependency(Base):
    """`dependent_id` depends on `prerequisite_id` — both must be features
    under the same epic. Enforced at the application layer (the schema only
    enforces they're tickets); cycles are rejected at insert time.

    Used by the roadmap view: a topological sort groups features into
    "waves" where each wave's features can be worked on in parallel and
    later waves require earlier ones to be done.
    """

    __tablename__ = "feature_dependencies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dependent_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    prerequisite_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("dependent_id", "prerequisite_id", name="uq_feature_dep"),
        CheckConstraint(
            "dependent_id <> prerequisite_id", name="ck_feature_dep_no_self"
        ),
        Index("ix_feature_deps_dependent", "dependent_id"),
        Index("ix_feature_deps_prerequisite", "prerequisite_id"),
    )


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    domain_name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="plans")


class RunKind(StrEnum):
    architect = "architect"
    developer = "developer"
    reviewer = "reviewer"
    tester = "tester"


class RunStatus(StrEnum):
    started = "started"
    succeeded = "succeeded"
    failed = "failed"
    budget_exhausted = "budget_exhausted"


class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    kind: Mapped[RunKind] = mapped_column(String(32), nullable=False)
    status: Mapped[RunStatus] = mapped_column(String(32), nullable=False)
    iteration: Mapped[int] = mapped_column(default=0, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int] = mapped_column(default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="runs")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    # Null for events not tied to a specific agent run (lifecycle events, etc.).
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="events")

    __table_args__ = (
        # Replay queries hit this — "give me events for this ticket since id X".
        Index("ix_events_ticket_id_id", "ticket_id", "id"),
        # Per-run timeline view in the UI.
        Index("ix_events_run_id_id", "run_id", "id"),
    )
