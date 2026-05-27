"""Architect impl backed by claude-agent-sdk (Claude with read-only tools)."""
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
    TextBlock,
    ToolUseBlock,
    query,
)

from pravi.agents.architects.clarify_parser import parse_clarifications
from pravi.agents.architects.decompose_parser import parse_decomposition
from pravi.agents.protocols import (
    ArchitectRequest,
    ArchitectResult,
    ClarifyRequest,
    ClarifyResult,
    DecomposeRequest,
    DecomposeResult,
    TextSink,
)
from pravi.prompts.architect import VERSION as ARCHITECT_PROMPT_VERSION
from pravi.prompts.architect import system_prompt as build_system_prompt
from pravi.prompts.architect import user_prompt as build_user_prompt
from pravi.prompts.clarify import VERSION as CLARIFY_PROMPT_VERSION
from pravi.prompts.clarify import system_prompt as build_clarify_system_prompt
from pravi.prompts.clarify import user_prompt as build_clarify_user_prompt
from pravi.prompts.decompose import VERSION as DECOMPOSE_PROMPT_VERSION
from pravi.prompts.decompose import system_prompt as build_decompose_system_prompt
from pravi.prompts.decompose import user_prompt as build_decompose_user_prompt

log = structlog.get_logger(__name__)

# Read-only tool subset — no Write, Edit, Bash. WebFetch is useful for
# looking up library docs when planning.
ARCHITECT_ALLOWED_TOOLS = ["Read", "Grep", "Glob", "WebFetch"]


def _extract_plan(result_text: str | None, assistant_text: list[str]) -> str:
    if result_text and result_text.strip():
        return result_text.strip()
    return "\n\n".join(t for t in assistant_text if t.strip()).strip()


class _StreamBuf:
    """Per-block running accumulator for partial AssistantMessage events.

    With `include_partial_messages=True`, claude-agent-sdk re-emits the same
    block multiple times as its text grows. We track the chars we've seen per
    block index and only emit the delta to `on_text`. `full` is the full
    concatenated text across all blocks, in the order they first appeared.
    """

    def __init__(self) -> None:
        self._seen: dict[int, int] = {}
        self._per_block: dict[int, str] = {}

    def feed(self, block_idx: int, text: str) -> str:
        prev = self._seen.get(block_idx, 0)
        if len(text) <= prev:
            return ""
        delta = text[prev:]
        self._seen[block_idx] = len(text)
        self._per_block[block_idx] = text
        return delta

    @property
    def full(self) -> str:
        # Preserve block order by sorting on the index that the SDK assigned.
        return "\n\n".join(
            self._per_block[i] for i in sorted(self._per_block) if self._per_block[i].strip()
        )


_PROGRESS_MARKER = "<!--pravi-progress:"


def _summarize_tool_input(name: str, payload: dict[str, object] | None) -> str:
    """Short, log-friendly summary of a tool call for the UI progress feed.

    The UI parses these out of `raw_md` and renders them as a list of "what
    the agent is doing right now" — so the summary needs to be tight (one
    line, ≤120 chars) and readable, not a full JSON dump.
    """
    if not payload:
        return name
    if name == "Read":
        path = str(payload.get("file_path") or "")
        return path[-100:] if path else name
    if name == "Grep":
        pattern = str(payload.get("pattern") or "")
        path = payload.get("path") or payload.get("glob") or ""
        return f"{pattern}  ({path})" if path else pattern
    if name == "Glob":
        return str(payload.get("pattern") or name)
    if name == "WebFetch":
        return str(payload.get("url") or name)
    return name


def _progress_line(name: str, payload: dict[str, object] | None) -> str:
    summary = _summarize_tool_input(name, payload).replace("\n", " ").strip()
    if len(summary) > 120:
        summary = summary[:117] + "…"
    # Comment so it survives in `raw_md` without affecting the markdown
    # render OR the YAML parser. The UI extracts these via regex.
    return f"\n{_PROGRESS_MARKER} {name}|{summary} -->\n"


class ClaudeArchitect:
    """Implements `agents.protocols.Architect` via claude-agent-sdk.

    Each mode (clarify / decompose / draft) can pin its own model — None
    falls back to the default. Wiring kept simple: pass the resolved model
    string into ClaudeAgentOptions.model when set, otherwise leave the SDK
    on its own default (claude-opus-4-7 today).
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        clarify_model: str | None = None,
        decompose_model: str | None = None,
        draft_model: str | None = None,
    ) -> None:
        self.model = model
        # Per-mode overrides — None means "use self.model".
        self.clarify_model = clarify_model or model
        self.decompose_model = decompose_model or model
        self.draft_model = draft_model or model

    async def draft_plan(
        self,
        req: ArchitectRequest,
        *,
        on_text: TextSink | None = None,
    ) -> ArchitectResult:
        cwd = Path(req.repo_path).expanduser().resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"architect cwd does not exist: {cwd}")

        sp = build_system_prompt(
            repo_name=req.repo_name,
            domain_name=req.domain_name,
            domain_description=req.domain_description,
            domain_paths=req.domain_paths,
            cwd=str(cwd),
            can_browse=True,
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
            include_partial_messages=on_text is not None,
        )
        if self.draft_model:
            options.model = self.draft_model

        buf = _StreamBuf()
        result_msg: ResultMessage | None = None
        errors: list[str] = []
        start = time.monotonic()
        seen_tool_ids: set[str] = set()

        log.info(
            "architect.draft.starting",
            cwd=str(cwd),
            model=self.draft_model,
            max_wall_seconds=req.max_wall_seconds,
            max_turns=req.max_turns,
            max_cost_usd=req.max_cost_usd,
        )

        async def _consume() -> None:
            nonlocal result_msg
            saw_first_msg = False
            msg_count = 0
            async for msg in query(prompt=up, options=options):
                msg_count += 1
                if not saw_first_msg:
                    log.info(
                        "architect.draft.first_msg",
                        kind=type(msg).__name__,
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                    )
                    saw_first_msg = True
                if isinstance(msg, AssistantMessage):
                    for i, block in enumerate(msg.content):
                        if isinstance(block, TextBlock):
                            delta = buf.feed(i, block.text)
                            if delta and on_text is not None:
                                await on_text(delta)
                        elif isinstance(block, ToolUseBlock):
                            if block.id in seen_tool_ids:
                                continue
                            seen_tool_ids.add(block.id)
                            if on_text is not None:
                                await on_text(_progress_line(block.name, block.input))
                elif isinstance(msg, ResultMessage):
                    result_msg = msg
                if msg_count % 25 == 0:
                    log.info(
                        "architect.draft.progress",
                        msg_count=msg_count,
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                    )

        try:
            await asyncio.wait_for(_consume(), timeout=req.max_wall_seconds)
        except TimeoutError:
            errors.append(
                f"architect exceeded wall-clock budget of {req.max_wall_seconds}s"
            )
            log.warning(
                "architect.claude.timeout", wall_seconds=req.max_wall_seconds
            )
        except ClaudeSDKError as e:
            errors.append(f"SDK error: {type(e).__name__}: {e}")
            log.error("architect.claude.sdk_error", error=str(e))
        except Exception as e:
            # Salvage post-result SDK errors (see decompose for context).
            if result_msg is None:
                raise
            log.warning(
                "architect.claude.draft_post_result_error",
                error=str(e),
                type=type(e).__name__,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        plan_md = _extract_plan(
            result_msg.result if result_msg else None,
            [buf.full] if buf.full else [],
        )
        log.info(
            "architect.draft.finished",
            duration_ms=duration_ms,
            saw_result=result_msg is not None,
            num_turns=result_msg.num_turns if result_msg else 0,
            total_cost_usd=result_msg.total_cost_usd if result_msg else None,
            plan_len=len(plan_md),
            errors=errors,
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

    async def decompose_epic(
        self,
        req: DecomposeRequest,
        *,
        on_text: TextSink | None = None,
    ) -> DecomposeResult:
        cwd = Path(req.repo_path).expanduser().resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"decompose cwd does not exist: {cwd}")

        sp = build_decompose_system_prompt(
            repo_name=req.repo_name,
            available_domains=req.available_domains,
            default_domain=req.default_domain,
            cwd=str(cwd),
            can_browse=True,
        )
        up = build_decompose_user_prompt(
            epic_title=req.epic_title,
            epic_body=req.epic_body,
            clarifications=req.clarifications,
        )

        options = ClaudeAgentOptions(
            system_prompt=sp,
            cwd=cwd,
            permission_mode="bypassPermissions",
            allowed_tools=ARCHITECT_ALLOWED_TOOLS,
            max_turns=req.max_turns,
            max_budget_usd=req.max_cost_usd,
            setting_sources=[],
            include_partial_messages=on_text is not None,
        )
        if self.decompose_model:
            options.model = self.decompose_model

        buf = _StreamBuf()
        result_msg: ResultMessage | None = None
        errors: list[str] = []
        start = time.monotonic()
        seen_tool_ids: set[str] = set()

        log.info(
            "architect.decompose.starting",
            cwd=str(cwd),
            model=self.decompose_model,
            max_wall_seconds=req.max_wall_seconds,
            max_turns=req.max_turns,
            max_cost_usd=req.max_cost_usd,
            clarifications=len(req.clarifications),
        )

        async def _consume() -> None:
            nonlocal result_msg
            saw_first_msg = False
            msg_count = 0
            async for msg in query(prompt=up, options=options):
                msg_count += 1
                if not saw_first_msg:
                    log.info(
                        "architect.decompose.first_msg",
                        kind=type(msg).__name__,
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                    )
                    saw_first_msg = True
                if isinstance(msg, AssistantMessage):
                    for i, block in enumerate(msg.content):
                        if isinstance(block, TextBlock):
                            delta = buf.feed(i, block.text)
                            if delta and on_text is not None:
                                await on_text(delta)
                        elif isinstance(block, ToolUseBlock):
                            if block.id in seen_tool_ids:
                                continue
                            seen_tool_ids.add(block.id)
                            if on_text is not None:
                                await on_text(_progress_line(block.name, block.input))
                elif isinstance(msg, ResultMessage):
                    result_msg = msg
                if msg_count % 25 == 0:
                    log.info(
                        "architect.decompose.progress",
                        msg_count=msg_count,
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                    )

        try:
            await asyncio.wait_for(_consume(), timeout=req.max_wall_seconds)
        except TimeoutError:
            errors.append(
                f"decompose exceeded wall-clock budget of {req.max_wall_seconds}s"
            )
            log.warning(
                "architect.claude.decompose_timeout",
                wall_seconds=req.max_wall_seconds,
            )
        except ClaudeSDKError as e:
            errors.append(f"SDK error: {type(e).__name__}: {e}")
            log.error("architect.claude.decompose_sdk_error", error=str(e))
        except Exception as e:
            # The SDK occasionally raises a generic Exception ("returned an
            # error result: success") AFTER it has already emitted the final
            # ResultMessage. If we've already captured a result we can salvage
            # the run; otherwise the error is real and bubbles up.
            if result_msg is None:
                raise
            log.warning(
                "architect.claude.decompose_post_result_error",
                error=str(e),
                type=type(e).__name__,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        raw_md = _extract_plan(
            result_msg.result if result_msg else None,
            [buf.full] if buf.full else [],
        )
        features, parse_errors = parse_decomposition(raw_md) if raw_md else ([], ["empty"])
        log.info(
            "architect.decompose.finished",
            duration_ms=duration_ms,
            saw_result=result_msg is not None,
            num_turns=result_msg.num_turns if result_msg else 0,
            total_cost_usd=result_msg.total_cost_usd if result_msg else None,
            features_parsed=len(features),
            parse_errors=parse_errors,
            errors=errors,
        )
        errors.extend(parse_errors)
        is_error = (not features) or (result_msg is not None and result_msg.is_error)

        return DecomposeResult(
            success=not is_error and not parse_errors,
            raw_md=raw_md,
            features=features,
            prompt_version=DECOMPOSE_PROMPT_VERSION,
            duration_ms=duration_ms,
            num_turns=result_msg.num_turns if result_msg else 0,
            total_cost_usd=result_msg.total_cost_usd if result_msg else None,
            errors=errors + (result_msg.errors if result_msg and result_msg.errors else []),
        )

    async def clarify_epic(
        self,
        req: ClarifyRequest,
        *,
        on_text: TextSink | None = None,
    ) -> ClarifyResult:
        cwd = Path(req.repo_path).expanduser().resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"clarify cwd does not exist: {cwd}")

        sp = build_clarify_system_prompt(
            repo_name=req.repo_name,
            available_domains=req.available_domains,
            default_domain=req.default_domain,
            cwd=str(cwd),
            can_browse=True,
        )
        up = build_clarify_user_prompt(
            epic_title=req.epic_title,
            epic_body=req.epic_body,
        )

        options = ClaudeAgentOptions(
            system_prompt=sp,
            cwd=cwd,
            permission_mode="bypassPermissions",
            allowed_tools=ARCHITECT_ALLOWED_TOOLS,
            max_turns=req.max_turns,
            max_budget_usd=req.max_cost_usd,
            setting_sources=[],
            include_partial_messages=on_text is not None,
        )
        if self.clarify_model:
            options.model = self.clarify_model

        buf = _StreamBuf()
        result_msg: ResultMessage | None = None
        errors: list[str] = []
        start = time.monotonic()
        seen_tool_ids: set[str] = set()

        log.info(
            "architect.clarify.starting",
            cwd=str(cwd),
            model=self.clarify_model,
            max_wall_seconds=req.max_wall_seconds,
            max_turns=req.max_turns,
            max_cost_usd=req.max_cost_usd,
        )

        async def _consume() -> None:
            nonlocal result_msg
            saw_first_msg = False
            msg_count = 0
            async for msg in query(prompt=up, options=options):
                msg_count += 1
                if not saw_first_msg:
                    log.info(
                        "architect.clarify.first_msg",
                        kind=type(msg).__name__,
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                    )
                    saw_first_msg = True
                if isinstance(msg, AssistantMessage):
                    for i, block in enumerate(msg.content):
                        if isinstance(block, TextBlock):
                            delta = buf.feed(i, block.text)
                            if delta and on_text is not None:
                                await on_text(delta)
                        elif isinstance(block, ToolUseBlock):
                            # Surface "what the agent is doing" as a comment
                            # marker so the UI can render a live progress feed
                            # (the YAML parser ignores anything outside the
                            # fenced block).
                            if block.id in seen_tool_ids:
                                continue
                            seen_tool_ids.add(block.id)
                            if on_text is not None:
                                await on_text(_progress_line(block.name, block.input))
                elif isinstance(msg, ResultMessage):
                    result_msg = msg
                # With streaming on, message count explodes — log less often.
                if msg_count % 25 == 0:
                    log.info(
                        "architect.clarify.progress",
                        msg_count=msg_count,
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                    )

        try:
            await asyncio.wait_for(_consume(), timeout=req.max_wall_seconds)
        except TimeoutError:
            errors.append(
                f"clarify exceeded wall-clock budget of {req.max_wall_seconds}s"
            )
            log.warning(
                "architect.claude.clarify_timeout",
                wall_seconds=req.max_wall_seconds,
            )
        except ClaudeSDKError as e:
            errors.append(f"SDK error: {type(e).__name__}: {e}")
            log.error("architect.claude.clarify_sdk_error", error=str(e))
        except Exception as e:
            # Salvage post-result SDK errors (see decompose for context).
            if result_msg is None:
                raise
            log.warning(
                "architect.claude.clarify_post_result_error",
                error=str(e),
                type=type(e).__name__,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        raw_md = _extract_plan(
            result_msg.result if result_msg else None,
            [buf.full] if buf.full else [],
        )
        questions, parse_errors = parse_clarifications(raw_md) if raw_md else ([], ["empty"])
        log.info(
            "architect.clarify.finished",
            duration_ms=duration_ms,
            saw_result=result_msg is not None,
            num_turns=result_msg.num_turns if result_msg else 0,
            total_cost_usd=result_msg.total_cost_usd if result_msg else None,
            questions_parsed=len(questions),
            parse_errors=parse_errors,
            errors=errors,
        )
        # Empty questions with no errors is a valid outcome — architect had
        # nothing to ask. That's a success.
        is_error = bool(parse_errors) or (result_msg is not None and result_msg.is_error)

        return ClarifyResult(
            success=not is_error,
            raw_md=raw_md,
            questions=questions,
            prompt_version=CLARIFY_PROMPT_VERSION,
            duration_ms=duration_ms,
            num_turns=result_msg.num_turns if result_msg else 0,
            total_cost_usd=result_msg.total_cost_usd if result_msg else None,
            errors=errors + parse_errors
            + (result_msg.errors if result_msg and result_msg.errors else []),
        )
