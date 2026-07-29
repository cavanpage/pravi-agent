"""Sandbox Protocol — see ADR 0003."""
from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class SandboxExecRequest:
    """One command run inside a provisioned sandbox.

    `command` is argv — never a shell string. Implementations must not
    invoke a shell: no injection surface, no per-backend shell drift.
    `cwd_rel` is relative to the handle's working dir; "" means its root.
    `env` is merged *over* the inherited environment (so PATH survives and
    `npx` still resolves).
    """

    command: list[str]
    cwd_rel: str = ""
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 900
    # Captured output is truncated head+tail past this, so a runaway build
    # log can't blow through Temporal's payload limit. Callers that parse
    # the output (the e2e report) raise it and check `truncated`.
    max_output_bytes: int = 1_000_000


@dataclass
class SandboxExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int = 0
    truncated: bool = False


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

    async def exec(
        self, handle: SandboxHandle, req: SandboxExecRequest
    ) -> SandboxExecResult:
        """Run a command inside the sandbox.

        Never raises on a non-zero exit — the caller reads `exit_code`.
        Raises only when the sandbox itself is unreachable. Added in ADR
        0007 so the e2e leg can install deps and run Playwright wherever
        the code physically lives, without the workflow learning paths.
        """
        ...

    async def head_sha(self, handle: SandboxHandle) -> str | None:
        """Full 40-char SHA at the branch tip, or None if not resolvable.

        The preview leg matches Cloudflare deployments on this, so a given
        build is unambiguously the commit we just pushed.
        """
        ...

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
