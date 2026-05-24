from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog
from temporalio import activity

from pravi.config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class WorktreeRequest:
    repo_path: str
    ticket_id: str
    branch: str
    base_ref: str = "main"


@dataclass
class WorktreeInfo:
    path: str
    branch: str


async def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


@activity.defn
async def create_worktree(req: WorktreeRequest) -> WorktreeInfo:
    repo = Path(req.repo_path).expanduser().resolve()
    if not (repo / ".git").exists():
        raise RuntimeError(f"not a git repo: {repo}")

    base = get_settings().worktree_base_resolved
    base.mkdir(parents=True, exist_ok=True)
    target = base / req.ticket_id

    # Idempotent: if it already exists, reuse it
    if target.exists():
        log.info("worktree.exists", path=str(target))
        return WorktreeInfo(path=str(target), branch=req.branch)

    # Create branch from base_ref and add worktree at target
    code, out, err = await _run(
        ["git", "worktree", "add", "-b", req.branch, str(target), req.base_ref],
        cwd=repo,
    )
    if code != 0:
        raise RuntimeError(f"git worktree add failed: {err.strip() or out.strip()}")
    log.info("worktree.created", path=str(target), branch=req.branch)
    return WorktreeInfo(path=str(target), branch=req.branch)


@dataclass
class CleanupRequest:
    repo_path: str
    worktree_path: str


@activity.defn
async def remove_worktree(req: CleanupRequest) -> None:
    repo = Path(req.repo_path).expanduser().resolve()
    target = Path(req.worktree_path)
    if not target.exists():
        log.info("worktree.cleanup.missing", path=str(target))
        return

    code, out, err = await _run(
        ["git", "worktree", "remove", "--force", str(target)],
        cwd=repo,
    )
    if code != 0:
        # Last-resort cleanup so we don't leak directories
        log.warning("worktree.remove_failed", path=str(target), err=err)
        shutil.rmtree(target, ignore_errors=True)
        await _run(["git", "worktree", "prune"], cwd=repo)
    log.info("worktree.removed", path=str(target))


@dataclass
class RunCommandRequest:
    cwd: str
    command: list[str]
    timeout_seconds: int = 600


@dataclass
class RunCommandResult:
    exit_code: int
    stdout: str
    stderr: str


@activity.defn
async def run_command(req: RunCommandRequest) -> RunCommandResult:
    """Run a shell command in a worktree and capture output."""
    try:
        code, out, err = await asyncio.wait_for(
            _run(req.command, cwd=Path(req.cwd)),
            timeout=req.timeout_seconds,
        )
    except TimeoutError as e:
        raise RuntimeError(f"command timed out after {req.timeout_seconds}s: {req.command}") from e
    log.info(
        "command.finished",
        cwd=req.cwd,
        cmd=" ".join(req.command),
        exit_code=code,
        stdout_tail=out[-500:],
    )
    return RunCommandResult(exit_code=code, stdout=out, stderr=err)
