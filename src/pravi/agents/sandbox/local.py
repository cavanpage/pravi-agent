"""LocalWorktreeSandbox — runs the dev agent in a `git worktree` on the host.

This is pravi's only Sandbox impl today. Behavior is identical to the
pre-seam `git_activity.create_worktree` / `pr_activity._push_via_https`
flow; the difference is that the lifecycle is owned by the Protocol so
future remote backends can drop in via config.

Lazy-clone path:
  - If the Repo row has GitHub coordinates AND its `local_path` is empty
    OR points at a path that no longer exists, the sandbox clones to
    `clone_base/<owner>__<name>` and updates the row.
  - If `local_path` already points at a working git checkout, it's used
    as-is (legacy tickets created before the sandbox refactor).
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import structlog

from pravi.agents.sandbox.protocols import (
    Sandbox,
    SandboxHandle,
    SandboxProvisionRequest,
)
from pravi.config import get_settings
from pravi.db.models import Repo
from pravi.db.session import session_scope
from pravi.services import github as gh

log = structlog.get_logger(__name__)


async def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    return (
        proc.returncode or 0,
        out_b.decode("utf-8", "replace"),
        err_b.decode("utf-8", "replace"),
    )


async def _resolve_local_clone(repo_id: int) -> tuple[Path, str | None]:
    """Find (or lazily make) the main local clone for a Repo. Returns the
    clone path and the origin URL (or None if missing).

    Lazy-clone when the Repo has GitHub coordinates but no usable local
    path. Existing `local_path`-only rows (legacy) pass through untouched.
    """
    async with session_scope() as session:
        repo = await session.get(Repo, repo_id)
        if repo is None:
            raise ValueError(f"Repo {repo_id} not found")
        local_path = repo.local_path or ""
        github_owner = repo.github_owner
        github_name = repo.github_name

    candidate = Path(local_path).expanduser() if local_path else None
    if candidate is not None and (candidate / ".git").is_dir():
        origin = await _get_origin_url(candidate)
        return candidate, origin

    # Need to clone. Without GitHub coords there's nothing we can do.
    if not github_owner or not github_name:
        raise RuntimeError(
            f"Repo {repo_id}: no usable local checkout and no GitHub "
            "coordinates — cannot provision a working directory"
        )

    conn = await gh.get_active_connection()
    if conn is None:
        raise RuntimeError(
            "lazy clone needs an active GitHub connection — click "
            "'Connect GitHub' in the web UI"
        )
    clone_url = f"https://github.com/{github_owner}/{github_name}.git"
    base_dir = get_settings().clone_base_resolved
    target = await gh.ensure_repo_cloned(
        owner=github_owner,
        name=github_name,
        clone_url=clone_url,
        access_token=conn.access_token,
        base_dir=base_dir,
    )

    # Persist the resolved path so subsequent runs don't repeat the clone
    # check.
    async with session_scope() as session:
        fresh = await session.get(Repo, repo_id)
        if fresh is not None and not fresh.local_path:
            fresh.local_path = str(target)

    return target, await _get_origin_url(target)


async def _get_origin_url(repo_root: Path) -> str | None:
    code, out, _ = await _run(["git", "remote", "get-url", "origin"], cwd=repo_root)
    if code != 0:
        return None
    return out.strip() or None


class LocalWorktreeSandbox(Sandbox):
    """One worktree per ticket on the host filesystem.

    `sandbox_id` is the worktree path itself — convenient because cleanup
    needs the same value. For non-local backends, sandbox_id would be a
    container id / remote sandbox id and `cwd` a bind-mount path.
    """

    async def provision(
        self, req: SandboxProvisionRequest
    ) -> SandboxHandle:
        repo_root, origin = await _resolve_local_clone(req.repo_id)

        base = get_settings().worktree_base_resolved
        base.mkdir(parents=True, exist_ok=True)
        target = base / req.ticket_external_id

        if target.exists():
            log.info("sandbox.local.worktree_exists", path=str(target))
            return SandboxHandle(
                sandbox_id=str(target),
                cwd=str(target),
                branch=req.branch,
                origin_url=origin,
                backend="local",
            )

        code, out, err = await _run(
            ["git", "worktree", "add", "-b", req.branch, str(target), req.base_ref],
            cwd=repo_root,
        )
        if code != 0:
            raise RuntimeError(
                f"git worktree add failed: {err.strip() or out.strip()}"
            )
        log.info("sandbox.local.worktree_created", path=str(target), branch=req.branch)
        return SandboxHandle(
            sandbox_id=str(target),
            cwd=str(target),
            branch=req.branch,
            origin_url=origin,
            backend="local",
        )

    async def commits_ahead(
        self, handle: SandboxHandle, base_ref: str
    ) -> int:
        code, out, _ = await _run(
            ["git", "rev-list", "--count", f"{base_ref}..HEAD"], cwd=Path(handle.cwd)
        )
        if code != 0:
            return 0
        try:
            return int(out.strip() or "0")
        except ValueError:
            return 0

    async def push_branch(
        self,
        handle: SandboxHandle,
        *,
        token: str,
        owner: str,
        name: str,
    ) -> tuple[bool, str]:
        is_ssh = (handle.origin_url or "").lower().startswith(("git@", "ssh://"))
        worktree = Path(handle.cwd)
        if is_ssh:
            # Trust the user's ssh-agent; no token needed.
            code, out, err = await _run(
                ["git", "push", "--set-upstream", "origin", f"{handle.branch}:{handle.branch}"],
                cwd=worktree,
            )
            if code != 0:
                return False, (err or out)[:500]
            return True, (out or err).strip()[:500]

        url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
        code, out, err = await _run(
            ["git", "push", "--set-upstream", url, f"{handle.branch}:{handle.branch}"],
            cwd=worktree,
        )
        if code != 0:
            scrubbed = (err or out).replace(token, "***")
            return False, scrubbed[:500]
        return True, (out or err).strip()[:500]

    async def cleanup(
        self,
        handle: SandboxHandle,
        *,
        delete_branch: bool = False,
    ) -> None:
        target = Path(handle.cwd)
        # The main clone is wherever the worktree was registered — git knows
        # via `worktree remove`, which only needs the worktree path.
        if not target.exists():
            log.info("sandbox.local.cleanup_missing", path=str(target))
        else:
            code, _, err = await _run(
                ["git", "worktree", "remove", "--force", str(target)],
                # `git worktree remove` works from inside the worktree too,
                # which lets us skip threading the main clone path through.
                cwd=target,
            )
            if code != 0:
                log.warning(
                    "sandbox.local.worktree_remove_failed",
                    path=str(target),
                    err=err.strip(),
                )
                shutil.rmtree(target, ignore_errors=True)
            else:
                log.info("sandbox.local.worktree_removed", path=str(target))

        if delete_branch:
            # Branches live on the main clone, not the worktree — but
            # without the worktree we don't know the main clone. Skip if
            # we can't infer it from the handle's saved origin (we don't
            # track main-clone path on the handle today).
            log.info(
                "sandbox.local.branch_delete_skipped",
                reason="branch deletion not implemented post-handle",
                branch=handle.branch,
            )
