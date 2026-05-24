"""Temporal activity that runs the developer agent inside a worktree.

Routes to the `pravi-llm` task queue (set by the workflow at execute time)
so concurrency caps on this pool actually bound LLM spend.

Retry policy: maximum_attempts=1 is enforced at the *workflow* level via the
execute_activity call — the bounded dev/test loop is the only legitimate
form of "retry" for LLM work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog
from temporalio import activity

from pravi.config import get_settings
from pravi.domains.registry import DomainRegistry
from pravi.prompts.developer import VERSION as DEV_PROMPT_VERSION
from pravi.prompts.developer import system_prompt as build_system_prompt
from pravi.sdk_runner.runner import DevRunRequest, DevRunResult, run_dev_agent

log = structlog.get_logger(__name__)


@dataclass
class DevActivityRequest:
    """All inputs to a single developer-agent run.

    The workflow snapshots the relevant domain config and passes it in here
    so the activity is fully self-contained (no I/O to load config) — this
    keeps the activity replayable and isolates it from .builder/domains.yaml
    drift mid-run.
    """

    repo_path: str
    repo_name: str
    worktree_path: str
    domain_name: str
    domain_description: str
    domain_paths: list[str]
    task: str


@dataclass
class DevActivityResult:
    success: bool
    summary: str
    prompt_version: str
    stop_reason: str | None
    num_turns: int
    duration_ms: int
    total_cost_usd: float | None
    session_id: str | None
    transcript_kinds: list[str] = field(default_factory=list)
    tool_uses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _build_request(req: DevActivityRequest) -> DevRunRequest:
    settings = get_settings()
    sp = build_system_prompt(
        repo_name=req.repo_name,
        domain_name=req.domain_name,
        domain_description=req.domain_description,
        domain_paths=req.domain_paths,
        cwd=req.worktree_path,
    )
    return DevRunRequest(
        cwd=req.worktree_path,
        system_prompt=sp,
        user_prompt=req.task,
        max_wall_seconds=settings.dev_max_wall_seconds,
        max_turns=settings.dev_max_turns,
        max_cost_usd=settings.dev_max_cost_usd,
    )


@activity.defn
async def run_dev(req: DevActivityRequest) -> DevActivityResult:
    if not Path(req.worktree_path).is_dir():
        raise FileNotFoundError(f"worktree missing: {req.worktree_path}")

    sdk_req = _build_request(req)
    log.info(
        "dev_activity.start",
        repo=req.repo_name,
        domain=req.domain_name,
        cwd=req.worktree_path,
        max_wall_seconds=sdk_req.max_wall_seconds,
        max_turns=sdk_req.max_turns,
        max_cost_usd=sdk_req.max_cost_usd,
        prompt_version=DEV_PROMPT_VERSION,
    )

    result: DevRunResult = await run_dev_agent(sdk_req, heartbeat=activity.heartbeat)

    tool_uses = [t.tool_name for t in result.transcript if t.kind == "tool_use" and t.tool_name]
    transcript_kinds = [t.kind for t in result.transcript]

    log.info(
        "dev_activity.finished",
        success=result.success,
        stop_reason=result.stop_reason,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
        total_cost_usd=result.total_cost_usd,
        tool_uses=tool_uses[:20],
        errors=result.errors,
    )

    summary = result.result_text or (
        "; ".join(result.errors) if result.errors else "no output"
    )
    return DevActivityResult(
        success=result.success,
        summary=summary[:2000],
        prompt_version=DEV_PROMPT_VERSION,
        stop_reason=result.stop_reason,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
        total_cost_usd=result.total_cost_usd,
        session_id=result.session_id,
        transcript_kinds=transcript_kinds,
        tool_uses=tool_uses,
        errors=result.errors,
    )


def build_request_from_registry(
    *,
    repo_path: str,
    worktree_path: str,
    domain_name: str,
    task: str,
    domains_file: Path | None = None,
) -> DevActivityRequest:
    """Helper for callers (CLI, workflow) — looks up the domain and packages
    the snapshot into a DevActivityRequest."""
    repo = Path(repo_path).expanduser().resolve()
    registry = DomainRegistry.load(repo, override_file=domains_file)
    domain = registry.get(domain_name)
    return DevActivityRequest(
        repo_path=str(repo),
        repo_name=repo.name,
        worktree_path=worktree_path,
        domain_name=domain.name,
        domain_description=domain.description,
        domain_paths=list(domain.paths),
        task=task,
    )
