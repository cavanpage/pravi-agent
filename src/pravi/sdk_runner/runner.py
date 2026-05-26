"""Thin wrapper around claude-agent-sdk for use inside Temporal activities.

Responsibilities (and only these):
  - Translate a (system_prompt, user_prompt, cwd, budget) request into an SDK call.
  - Stream messages and emit a Temporal heartbeat on each one.
  - Capture a structured transcript + final usage stats.
  - Enforce a wall-clock timeout independent of the SDK's own budget.

This module deliberately does NOT know about Temporal workflows, GitHub, or
domain registries — those concerns belong in the activity that calls it.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from pravi.agents.protocols import DevRunRequest, DevRunResult, EventSink, TranscriptEntry

log = structlog.get_logger(__name__)


def _summarize_tool_result(block: ToolResultBlock) -> str:
    content = block.content
    if isinstance(content, str):
        return content[:500]
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            text = getattr(c, "text", None)
            if text:
                parts.append(text)
        return ("\n".join(parts))[:500]
    return str(content)[:500]


async def run_dev_agent(
    req: DevRunRequest,
    *,
    heartbeat: callable | None = None,
    event_sink: EventSink | None = None,
) -> DevRunResult:
    """Run a single one-shot developer agent against ``req.cwd``.

    ``heartbeat`` is called with no args after each streamed message — pass
    ``temporalio.activity.heartbeat`` when invoking from a Temporal activity.
    Pass ``None`` for non-Temporal contexts (e.g. CLI dry runs).

    ``event_sink`` is awaited once per transcript entry with
    ``(kind, message, payload)`` — used by the activity to push live events
    to the per-ticket Postgres NOTIFY channel for the UI panel. Sink errors
    are logged and swallowed so a flaky DB connection can't kill an
    in-progress agent run.
    """
    cwd = Path(req.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise FileNotFoundError(f"dev agent cwd does not exist: {cwd}")

    options = ClaudeAgentOptions(
        system_prompt=req.system_prompt,
        cwd=cwd,
        permission_mode="bypassPermissions",
        max_turns=req.max_turns,
        max_budget_usd=req.max_cost_usd,
        # Don't read host user/project settings — the dev agent is sandboxed
        # to the worktree and shouldn't accidentally pick up local prefs.
        setting_sources=[],
    )
    if req.model:
        options.model = req.model

    transcript: list[TranscriptEntry] = []
    result_msg: ResultMessage | None = None
    start = time.monotonic()
    errors: list[str] = []

    async def _push(entry: TranscriptEntry) -> None:
        """Append to in-memory transcript AND mirror to the live event sink."""
        transcript.append(entry)
        if event_sink is None:
            return
        # Compose a one-line human-readable message and a structured payload.
        if entry.kind == "assistant_text":
            message = (entry.text or "")[:240]
            payload = None
        elif entry.kind == "tool_use":
            message = entry.tool_name or "tool"
            payload = {"tool": entry.tool_name, "input": entry.tool_input}
        elif entry.kind == "tool_result":
            message = (entry.tool_output_summary or "")[:240]
            payload = {"tool_use_id": entry.tool_name}
        elif entry.kind == "system":
            message = entry.text or "system"
            payload = entry.payload
        elif entry.kind == "result":
            message = (entry.text or "result")[:240]
            payload = entry.payload
        else:
            message = entry.kind
            payload = entry.payload
        try:
            await event_sink(entry.kind, message, payload)
        except Exception as e:
            # Telemetry must never break the agent — log and continue.
            log.warning("event_sink.failed", error=str(e), kind=entry.kind)

    async def _consume() -> None:
        nonlocal result_msg
        async for msg in query(prompt=req.user_prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        await _push(TranscriptEntry(kind="assistant_text", text=block.text))
                    elif isinstance(block, ToolUseBlock):
                        await _push(
                            TranscriptEntry(
                                kind="tool_use",
                                tool_name=block.name,
                                tool_input=block.input,
                            )
                        )
            elif isinstance(msg, UserMessage):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        await _push(
                            TranscriptEntry(
                                kind="tool_result",
                                tool_name=getattr(block, "tool_use_id", None),
                                tool_output_summary=_summarize_tool_result(block),
                            )
                        )
            elif isinstance(msg, SystemMessage):
                await _push(
                    TranscriptEntry(
                        kind="system",
                        text=getattr(msg, "subtype", None),
                        payload=getattr(msg, "data", None),
                    )
                )
            elif isinstance(msg, ResultMessage):
                result_msg = msg
                await _push(
                    TranscriptEntry(
                        kind="result",
                        text=msg.result,
                        payload={
                            "stop_reason": msg.stop_reason,
                            "num_turns": msg.num_turns,
                            "total_cost_usd": msg.total_cost_usd,
                        },
                    )
                )

            if heartbeat is not None:
                try:
                    heartbeat()
                except Exception:
                    # Heartbeat errors (e.g. activity cancelled) propagate via
                    # the surrounding asyncio.wait_for; don't swallow them.
                    raise

    try:
        await asyncio.wait_for(_consume(), timeout=req.max_wall_seconds)
    except TimeoutError:
        errors.append(f"dev agent exceeded wall-clock budget of {req.max_wall_seconds}s")
        log.warning(
            "dev_agent.timeout", cwd=str(cwd), wall_seconds=req.max_wall_seconds
        )
    except ClaudeSDKError as e:
        errors.append(f"SDK error: {type(e).__name__}: {e}")
        log.error("dev_agent.sdk_error", cwd=str(cwd), error=str(e))

    duration_ms = int((time.monotonic() - start) * 1000)

    if result_msg is None:
        return DevRunResult(
            success=False,
            stop_reason=None,
            num_turns=0,
            duration_ms=duration_ms,
            duration_api_ms=0,
            total_cost_usd=None,
            session_id=None,
            result_text=None,
            transcript=transcript,
            errors=errors or ["dev agent did not produce a ResultMessage"],
        )

    return DevRunResult(
        success=not result_msg.is_error and not errors,
        stop_reason=result_msg.stop_reason,
        num_turns=result_msg.num_turns,
        duration_ms=result_msg.duration_ms or duration_ms,
        duration_api_ms=result_msg.duration_api_ms or 0,
        total_cost_usd=result_msg.total_cost_usd,
        session_id=result_msg.session_id,
        result_text=result_msg.result,
        transcript=transcript,
        errors=errors + (result_msg.errors or []),
    )
