"""Temporal-facing wrappers around the Sandbox Protocol.

Workflows call these activities (`provision_sandbox`, `cleanup_sandbox`)
instead of running git subprocesses directly. The configured Sandbox
backend (today: `local`) does the actual work.

The activities accept + return JSON-serializable dataclasses so Temporal
can pass them across the workflow ↔ activity boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from temporalio import activity

from pravi.agents.sandbox.factory import get_sandbox
from pravi.agents.sandbox.protocols import (
    SandboxExecRequest,
    SandboxExecResult,
    SandboxHandle,
    SandboxProvisionRequest,
)

log = structlog.get_logger(__name__)


@dataclass
class ProvisionRequest:
    """Workflow-facing input — identifies the work but not the backend."""

    repo_id: int
    ticket_external_id: str
    branch: str
    base_ref: str


@dataclass
class CleanupRequest:
    handle: SandboxHandle
    delete_branch: bool = False


@activity.defn
async def provision_sandbox(req: ProvisionRequest) -> SandboxHandle:
    """Set up a working environment for the dev agent. The returned
    `SandboxHandle` is opaque to the workflow — pass it back into
    `cleanup_sandbox` and `push_and_open_pr` unchanged."""
    sandbox = get_sandbox()
    handle = await sandbox.provision(
        SandboxProvisionRequest(
            repo_id=req.repo_id,
            ticket_external_id=req.ticket_external_id,
            branch=req.branch,
            base_ref=req.base_ref,
        )
    )
    log.info(
        "sandbox.provisioned",
        backend=handle.backend,
        ticket=req.ticket_external_id,
        sandbox_id=handle.sandbox_id,
    )
    return handle


@activity.defn
async def cleanup_sandbox(req: CleanupRequest) -> None:
    sandbox = get_sandbox()
    await sandbox.cleanup(req.handle, delete_branch=req.delete_branch)


@dataclass
class ExecRequest:
    """Workflow-facing exec input. Flattened rather than embedding a
    `SandboxExecRequest` so the Temporal payload stays a plain struct."""

    handle: SandboxHandle
    command: list[str]
    cwd_rel: str = ""
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 900
    max_output_bytes: int = 1_000_000


@activity.defn
async def sandbox_exec(req: ExecRequest) -> SandboxExecResult:
    """Run a command inside a provisioned sandbox (ADR 0007).

    Non-zero exits are data, not errors — the caller inspects `exit_code`.
    """
    sandbox = get_sandbox()
    result = await sandbox.exec(
        req.handle,
        SandboxExecRequest(
            command=list(req.command),
            cwd_rel=req.cwd_rel,
            env=dict(req.env),
            timeout_seconds=req.timeout_seconds,
            max_output_bytes=req.max_output_bytes,
        ),
    )
    log.info(
        "sandbox.exec",
        sandbox_id=req.handle.sandbox_id,
        command=req.command[:4],
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_ms=result.duration_ms,
    )
    return result


@activity.defn
async def sandbox_head_sha(handle: SandboxHandle) -> str | None:
    sandbox = get_sandbox()
    return await sandbox.head_sha(handle)
