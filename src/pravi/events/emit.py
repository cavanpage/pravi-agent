"""Write an Event row + fire a NOTIFY on the per-ticket channel.

Both happen inside the same transaction so the notification fires exactly
when the row becomes visible. Subscribers receive the event ID and fetch
the row by primary key — that way a NOTIFY payload stays tiny (well under
Postgres' 8KB limit) and the full event survives reconnects.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pravi.db.models import Event

# Lifecycle sentinels — the SSE handler closes the stream on RUN_FINISHED.
KIND_RUN_STARTED = "run_started"
KIND_RUN_FINISHED = "run_finished"


def channel_for_ticket(ticket_id: int) -> str:
    """Per-ticket NOTIFY channel. Must match what the listener subscribes to."""
    return f"pravi_ticket_{ticket_id}"


async def emit_event(
    session: AsyncSession,
    *,
    ticket_id: int,
    kind: str,
    message: str,
    run_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Insert an Event row and issue NOTIFY in the same transaction.

    Returns the new event id. Caller is responsible for the session's
    commit boundary (typically ``session_scope()`` handles it).
    """
    event = Event(
        ticket_id=ticket_id,
        run_id=run_id,
        kind=kind,
        message=message,
        payload=payload,
    )
    session.add(event)
    await session.flush()  # populate event.id before NOTIFY references it

    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": channel_for_ticket(ticket_id), "payload": str(event.id)},
    )
    return event.id
