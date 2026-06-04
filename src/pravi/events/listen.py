"""Dedicated asyncpg LISTEN connection per subscriber.

Postgres requires a connection that has issued LISTEN — it can't be pooled
or shared with normal queries. Each SSE client gets its own connection;
this is fine at MVP scale (a handful of concurrent viewers). Revisit if
the API ever serves dozens of concurrent live-run subscribers.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import structlog

from pravi.config import get_settings
from pravi.events.emit import channel_for_ticket

log = structlog.get_logger(__name__)


def _asyncpg_dsn() -> str:
    """SQLAlchemy uses postgresql+asyncpg://; asyncpg.connect wants postgresql://."""
    url = get_settings().db_url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@asynccontextmanager
async def listen_events_many(
    ticket_ids: list[int],
) -> AsyncIterator[asyncio.Queue[int]]:
    """LISTEN on every per-ticket NOTIFY channel for the given IDs and
    yield a single queue that multiplexes them.

    Used by the subtree activity stream (epic/feature live feed) — one
    asyncpg connection, many `LISTEN` statements, one shared queue.

    Empty `ticket_ids` is valid: the queue yields nothing but the context
    manager still cleans up. Lets the caller open the stream before any
    task workflows have started, then re-subscribe when children appear.
    """
    conn = await asyncpg.connect(_asyncpg_dsn())
    queue: asyncio.Queue[int] = asyncio.Queue()

    def on_notify(_connection, _pid, _channel, payload: str) -> None:
        try:
            queue.put_nowait(int(payload))
        except ValueError:
            log.warning("listen.bad_payload", channel=_channel, payload=payload)

    channels = [channel_for_ticket(tid) for tid in ticket_ids]
    for ch in channels:
        await conn.add_listener(ch, on_notify)
    try:
        yield queue
    finally:
        try:
            for ch in channels:
                await conn.remove_listener(ch, on_notify)
        finally:
            await conn.close()


@asynccontextmanager
async def listen_events(ticket_id: int) -> AsyncIterator[asyncio.Queue[int]]:
    """Yield a queue that fills with event IDs as NOTIFY messages arrive.

    Usage::

        async with listen_events(ticket_id) as queue:
            while True:
                event_id = await queue.get()
                ...

    The queue is unbounded — bursts won't drop events, but a slow consumer
    will accumulate memory. In practice the dev agent emits 1-3 events/sec,
    so this is a non-issue.
    """
    conn = await asyncpg.connect(_asyncpg_dsn())
    queue: asyncio.Queue[int] = asyncio.Queue()
    channel = channel_for_ticket(ticket_id)

    def on_notify(_connection, _pid, _channel, payload: str) -> None:
        try:
            queue.put_nowait(int(payload))
        except ValueError:
            log.warning("listen.bad_payload", channel=_channel, payload=payload)

    await conn.add_listener(channel, on_notify)
    try:
        yield queue
    finally:
        try:
            await conn.remove_listener(channel, on_notify)
        finally:
            await conn.close()
