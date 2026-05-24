"""Architect agent — drafts a plan for a ticket. Read-only.

Runs in the CLI process (NOT inside a Temporal activity) — the architect is
human-interactive and outside the deterministic workflow. The CLI persists
the resulting Plan to Postgres and sends a Temporal signal to the waiting
FeatureWorkflow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    query,
)

from pravi.prompts.architect import VERSION as ARCHITECT_PROMPT_VERSION
from pravi.prompts.architect import system_prompt as build_system_prompt
from pravi.prompts.architect import user_prompt as build_user_prompt

log = structlog.get_logger(__name__)


@dataclass
class ArchitectRequest:
    repo_path: str
    repo_name: str
    domain_name: str
    domain_description: str
    domain_paths: list[str]
    ticket_title: str
    ticket_body: str
    max_wall_seconds: int = 300
    max_turns: int = 30
    max_cost_usd: float = 1.0


@dataclass
class ArchitectResult:
    success: bool
    plan_md: str
    prompt_version: str
    duration_ms: int
    num_turns: int
    total_cost_usd: float | None
    errors: list[str] = field(default_factory=list)


# Tools the architect is allowed to use — read-only access for context.
# Notably: no Write, Edit, NotebookEdit, Bash (read commands are useful, but
# safer to deny entirely than risk a stray rm). Read/Grep/Glob cover most
# planning needs; WebFetch can read public docs if needed.
ARCHITECT_ALLOWED_TOOLS = ["Read", "Grep", "Glob", "WebFetch"]


def _extract_plan(result_text: str | None, transcript_text: list[str]) -> str:
    """Prefer the ResultMessage.result; fall back to concatenated assistant text."""
    if result_text and result_text.strip():
        return result_text.strip()
    return "\n\n".join(t for t in transcript_text if t.strip()).strip()


async def draft_plan(req: ArchitectRequest) -> ArchitectResult:
    cwd = Path(req.repo_path).expanduser().resolve()
    if not cwd.is_dir():
        raise FileNotFoundError(f"architect cwd does not exist: {cwd}")

    sp = build_system_prompt(
        repo_name=req.repo_name,
        domain_name=req.domain_name,
        domain_description=req.domain_description,
        domain_paths=req.domain_paths,
        cwd=str(cwd),
    )
    up = build_user_prompt(
        ticket_title=req.ticket_title,
        ticket_body=req.ticket_body,
    )

    options = ClaudeAgentOptions(
        system_prompt=sp,
        cwd=cwd,
        permission_mode="bypassPermissions",
        allowed_tools=ARCHITECT_ALLOWED_TOOLS,
        max_turns=req.max_turns,
        max_budget_usd=req.max_cost_usd,
        setting_sources=[],
    )

    assistant_texts: list[str] = []
    result_msg: ResultMessage | None = None
    errors: list[str] = []
    start = time.monotonic()

    try:
        async for msg in query(prompt=up, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        assistant_texts.append(block.text)
            elif isinstance(msg, ResultMessage):
                result_msg = msg
    except ClaudeSDKError as e:
        errors.append(f"SDK error: {type(e).__name__}: {e}")
        log.error("architect.sdk_error", error=str(e))

    duration_ms = int((time.monotonic() - start) * 1000)
    plan_md = _extract_plan(
        result_msg.result if result_msg else None,
        assistant_texts,
    )

    if not plan_md:
        errors.append("architect produced no plan content")
        return ArchitectResult(
            success=False,
            plan_md="",
            prompt_version=ARCHITECT_PROMPT_VERSION,
            duration_ms=duration_ms,
            num_turns=result_msg.num_turns if result_msg else 0,
            total_cost_usd=result_msg.total_cost_usd if result_msg else None,
            errors=errors,
        )

    is_error = bool(errors) or (result_msg is not None and result_msg.is_error)
    return ArchitectResult(
        success=not is_error,
        plan_md=plan_md,
        prompt_version=ARCHITECT_PROMPT_VERSION,
        duration_ms=duration_ms,
        num_turns=result_msg.num_turns if result_msg else 0,
        total_cost_usd=result_msg.total_cost_usd if result_msg else None,
        errors=errors + (result_msg.errors if result_msg and result_msg.errors else []),
    )
