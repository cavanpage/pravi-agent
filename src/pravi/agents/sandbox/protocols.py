"""Sandbox Protocol — see ADR 0003."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SandboxProvisionRequest:
    """Identity of a unit of work the sandbox needs to set up.

    `repo_id` is the DB Repo row id — the sandbox impl resolves it to
    wherever its working dir actually lives (local clone path, container
    bind mount, remote sandbox volume, …). Activities pass this struct
    over the Temporal boundary, so it must stay JSON-serializable.
    """

    repo_id: int
    ticket_external_id: str
    branch: str
    base_ref: str


@dataclass
class SandboxHandle:
    """Opaque-ish handle to a provisioned working environment.

    `cwd` is the filesystem path the dev agent runs against. Today this is
    a real path on the host; future remote backends may bind-mount a
    container path or surface an SDK-readable mountpoint with the same
    shape. `sandbox_id` is the impl-specific identifier used for cleanup
    (today it equals `cwd`; for Docker it'd be a container id, etc).

    `origin_url` is the source git remote (for parsing owner/name in the
    push step). `backend` is informational — useful for logs + UI hints.
    """

    sandbox_id: str
    cwd: str
    branch: str
    origin_url: str | None
    backend: str  # "local" today; "docker" / "cloudflare" / … later


class Sandbox(Protocol):
    """Provisions, drives git operations on, and tears down a per-ticket
    working environment. One impl per backend.

    The Temporal `sandbox_activity` calls these methods. Workflows pass the
    returned `SandboxHandle` between activities — they never see the
    underlying paths or sandbox internals.
    """

    async def provision(
        self, req: SandboxProvisionRequest
    ) -> SandboxHandle: ...

    async def commits_ahead(
        self, handle: SandboxHandle, base_ref: str
    ) -> int:
        """How many commits `branch` is ahead of `base_ref`. 0 if none."""
        ...

    async def push_branch(
        self,
        handle: SandboxHandle,
        *,
        token: str,
        owner: str,
        name: str,
    ) -> tuple[bool, str]:
        """Push the branch upstream. Returns (ok, message_or_error).
        Implementations must scrub any token from the returned message."""
        ...

    async def cleanup(
        self,
        handle: SandboxHandle,
        *,
        delete_branch: bool = False,
    ) -> None:
        """Tear down the working environment. Idempotent — calling on an
        already-torn-down handle is a no-op."""
        ...
