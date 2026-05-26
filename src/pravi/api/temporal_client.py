"""Lazy Temporal client — one per FastAPI process."""
from __future__ import annotations

from temporalio.client import Client

from pravi.config import get_settings

_client: Client | None = None


async def get_temporal_client() -> Client:
    global _client
    if _client is None:
        s = get_settings()
        _client = await Client.connect(s.temporal_host, namespace=s.temporal_namespace)
    return _client
