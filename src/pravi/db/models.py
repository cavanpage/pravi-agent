from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text, func
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

    repo: Mapped[Repo] = relationship(back_populates="tickets")
    plans: Mapped[list[Plan]] = relationship(back_populates="ticket")
    runs: Mapped[list[Run]] = relationship(back_populates="ticket")
    events: Mapped[list[Event]] = relationship(back_populates="ticket")


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
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="events")
