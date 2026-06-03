"""Sandbox factory — picks the backend from `PRAVI_SANDBOX_BACKEND`."""
from __future__ import annotations

from pravi.agents.sandbox.local import LocalWorktreeSandbox
from pravi.agents.sandbox.protocols import Sandbox
from pravi.config import get_settings


def get_sandbox() -> Sandbox:
    s = get_settings()
    backend = s.sandbox_backend
    if backend == "local":
        return LocalWorktreeSandbox()
    # Future: "docker", "cloudflare", "e2b", … each implements Sandbox.
    raise ValueError(
        f"unknown PRAVI_SANDBOX_BACKEND={backend!r}; valid: 'local'"
    )
