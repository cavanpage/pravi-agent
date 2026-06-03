"""Sandbox abstraction: where the dev agent's working directory lives.

See `docs/adr/0003-sandbox-seam-no-local-mounts.md` for the full design.

Today: one impl (`LocalWorktreeSandbox`) that lazily clones into
`clone_base/<owner>__<name>` and `git worktree add`s per ticket — identical
behavior to the pre-seam code, just behind the Protocol.

Future: `DockerSandbox`, `CloudflareSandbox`, etc — each implements the
same Protocol; the factory picks via `PRAVI_SANDBOX_BACKEND`.
"""

from pravi.agents.sandbox.protocols import (
    Sandbox,
    SandboxHandle,
    SandboxProvisionRequest,
)

__all__ = [
    "Sandbox",
    "SandboxHandle",
    "SandboxProvisionRequest",
]
