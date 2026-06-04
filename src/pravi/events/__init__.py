"""Live + durable event stream for agent runs.

Architecture:
  - Writers (the dev activity, mainly) call ``emit_event`` for every notable
    occurrence: turn, tool_use, tool_result, usage update, lifecycle. Each
    call writes an Event row AND issues a Postgres NOTIFY on a per-ticket
    channel, in the same transaction. Notification fires on commit.

  - Readers (the FastAPI SSE endpoint) open a *dedicated* asyncpg connection
    via ``listen_events``, replay recent rows from the events table, then
    receive new event IDs over the LISTEN channel and fetch each row.

Why LISTEN/NOTIFY rather than Temporal heartbeats or polling:
  - Push semantics, ~5-30ms end-to-end on local Postgres → sub-second UI.
  - No new infra (reuses the Postgres we already run).
  - The events table is the source of truth — disconnects don't lose data;
    a reconnecting client just replays from its last-seen id.

Channel naming: ``pravi_ticket_{ticket_id}`` (per-ticket fan-out).
"""
from pravi.events.emit import (
    KIND_RUN_FINISHED,
    KIND_RUN_STARTED,
    channel_for_ticket,
    emit_event,
)
from pravi.events.listen import listen_events, listen_events_many

__all__ = [
    "KIND_RUN_FINISHED",
    "KIND_RUN_STARTED",
    "channel_for_ticket",
    "emit_event",
    "listen_events",
    "listen_events_many",
]
