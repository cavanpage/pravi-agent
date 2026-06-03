"""Temporal-facing wrappers around the Sandbox Protocol.

Workflows call these activities (`provision_sandbox`, `cleanup_sandbox`)
instead of running git subprocesses directly. The configured Sandbox
backend (today: `local`) does the actual work.

The activities accept + return JSON-serializable dataclasses so Temporal
can pass them across the workflow ↔ activity boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog
from temporalio import activity

from pravi.agents.sandbox.factory import get_sandbox
from pravi.agents.sandbox.protocols import (
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
