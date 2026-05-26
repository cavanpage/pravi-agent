"""Architect impl backed by LiteLLM — single dep, ~all providers.

One-shot chat completion: no tool use. Context comes from
`agents.architects.context.build_context()`, packed into the user message.
The system prompt is generated with `can_browse=False` so the model is told
not to ask for more files.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog

from pravi.agents.architects.clarify_parser import parse_clarifications
from pravi.agents.architects.context import build_context
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


async def _call(
    litellm_module,
    *,
    model: str,
    messages: list,
    timeout: int,
    on_text: TextSink | None,
) -> tuple[str, float | None, list[str]]:
    """LiteLLM call with optional token-by-token streaming via on_text.

    Returns (raw_text, total_cost_usd, errors). Cost may be None when the
    provider doesn't report it (notably during streaming for some models).
    """
    raw = ""
    cost: float | None = None
    errors: list[str] = []
    async def _stream() -> None:
        nonlocal raw, cost
        stream = await litellm_module.acompletion(
            model=model, messages=messages, temperature=0.2, stream=True
        )
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content or ""
            except (AttributeError, IndexError):
                delta = ""
            if delta:
                raw += delta
                await on_text(delta)  # type: ignore[misc]
            hidden = getattr(chunk, "_hidden_params", None)
            if hidden and hidden.get("response_cost") is not None:
                cost = hidden["response_cost"]

    async def _one_shot() -> None:
        nonlocal raw, cost
        response = await litellm_module.acompletion(
            model=model, messages=messages, temperature=0.2
        )
        try:
            raw = (response.choices[0].message.content or "").strip()
        except (AttributeError, IndexError) as e:
            errors.append(f"could not parse response: {e}")
        cost = getattr(response, "_hidden_params", {}).get("response_cost") or None

    try:
        coro = _stream() if on_text is not None else _one_shot()
        await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        errors.append(f"litellm exceeded wall-clock budget of {timeout}s")
    except Exception as e:
        errors.append(f"litellm error: {type(e).__name__}: {e}")
    return raw.strip(), cost, errors


class LiteLLMArchitect:
    """Implements `agents.protocols.Architect` via `litellm.acompletion`.

    `model` follows the LiteLLM convention:
      - OpenAI: "gpt-5", "gpt-5-mini"
      - Anthropic via LiteLLM: "anthropic/claude-3-7-sonnet-latest"
      - Google: "gemini/gemini-2.5-pro"
      - Bedrock: "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
      - Ollama: "ollama/llama3.2"
    """

    def __init__(
        self,
        model: str,
        *,
        clarify_model: str | None = None,
        decompose_model: str | None = None,
        draft_model: str | None = None,
    ) -> None:
        if not model:
            raise ValueError("LiteLLMArchitect requires an explicit model")
        self.model = model
        # Per-mode overrides — None falls back to self.model.
        self.clarify_model = clarify_model or model
        self.decompose_model = decompose_model or model
        self.draft_model = draft_model or model

    async def draft_plan(
        self,
        req: ArchitectRequest,
        *,
        on_text: TextSink | None = None,
    ) -> ArchitectResult:
        # Lazy import so the litellm dep is optional at install time.
        try:
            import litellm
        except ImportError as e:
            return ArchitectResult(
                success=False,
                plan_md="",
                prompt_version=ARCHITECT_PROMPT_VERSION,
                duration_ms=0,
                num_turns=0,
                total_cost_usd=None,
                errors=[f"litellm not installed: {e}"],
            )

        cwd = Path(req.repo_path).expanduser().resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"architect cwd does not exist: {cwd}")

        packed = await asyncio.to_thread(
            build_context,
            cwd,
            list(req.domain_paths),
            list(req.domain_context_files),
        )

        sp = build_system_prompt(
            repo_name=req.repo_name,
            domain_name=req.domain_name,
            domain_description=req.domain_description,
            domain_paths=req.domain_paths,
            cwd=str(cwd),
            can_browse=False,
        )
        up = build_user_prompt(
            ticket_title=req.ticket_title,
            ticket_body=req.ticket_body,
            context_block=packed.text,
        )

        messages = [
            {"role": "system", "content": sp},
            {"role": "user", "content": up},
        ]

        start = time.monotonic()
        plan_md, total_cost_usd, errors = await _call(
            litellm,
            model=self.draft_model,
            messages=messages,
            timeout=req.max_wall_seconds,
            on_text=on_text,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        if not plan_md:
            errors.append("architect produced no plan content")
            return ArchitectResult(
                success=False,
                plan_md="",
                prompt_version=ARCHITECT_PROMPT_VERSION,
                duration_ms=duration_ms,
                num_turns=1,
                total_cost_usd=total_cost_usd,
                errors=errors,
            )

        return ArchitectResult(
            success=not errors,
            plan_md=plan_md,
            prompt_version=ARCHITECT_PROMPT_VERSION,
            duration_ms=duration_ms,
            num_turns=1,
            total_cost_usd=total_cost_usd,
            errors=errors,
        )

    async def decompose_epic(
        self,
        req: DecomposeRequest,
        *,
        on_text: TextSink | None = None,
    ) -> DecomposeResult:
        try:
            import litellm
        except ImportError as e:
            return DecomposeResult(
                success=False,
                raw_md="",
                features=[],
                prompt_version=DECOMPOSE_PROMPT_VERSION,
                duration_ms=0,
                num_turns=0,
                total_cost_usd=None,
                errors=[f"litellm not installed: {e}"],
            )

        cwd = Path(req.repo_path).expanduser().resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"decompose cwd does not exist: {cwd}")

        # Pack context from the union of all domain paths the epic might span.
        all_paths: list[str] = []
        for d in req.available_domains:
            all_paths.extend(d.paths)
        packed = await asyncio.to_thread(
            build_context,
            cwd,
            all_paths,
            list(req.domain_context_files),
        )

        sp = build_decompose_system_prompt(
            repo_name=req.repo_name,
            available_domains=req.available_domains,
            default_domain=req.default_domain,
            cwd=str(cwd),
            can_browse=False,
        )
        up = build_decompose_user_prompt(
            epic_title=req.epic_title,
            epic_body=req.epic_body,
            context_block=packed.text,
            clarifications=req.clarifications,
        )

        messages = [
            {"role": "system", "content": sp},
            {"role": "user", "content": up},
        ]

        start = time.monotonic()
        raw_md, total_cost_usd, errors = await _call(
            litellm,
            model=self.decompose_model,
            messages=messages,
            timeout=req.max_wall_seconds,
            on_text=on_text,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        features, parse_errors = parse_decomposition(raw_md) if raw_md else ([], ["empty"])
        errors.extend(parse_errors)

        return DecomposeResult(
            success=bool(features) and not errors,
            raw_md=raw_md,
            features=features,
            prompt_version=DECOMPOSE_PROMPT_VERSION,
            duration_ms=duration_ms,
            num_turns=1,
            total_cost_usd=total_cost_usd,
            errors=errors,
        )

    async def clarify_epic(
        self,
        req: ClarifyRequest,
        *,
        on_text: TextSink | None = None,
    ) -> ClarifyResult:
        try:
            import litellm
        except ImportError as e:
            return ClarifyResult(
                success=False,
                raw_md="",
                questions=[],
                prompt_version=CLARIFY_PROMPT_VERSION,
                duration_ms=0,
                num_turns=0,
                total_cost_usd=None,
                errors=[f"litellm not installed: {e}"],
            )

        cwd = Path(req.repo_path).expanduser().resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"clarify cwd does not exist: {cwd}")

        all_paths: list[str] = []
        for d in req.available_domains:
            all_paths.extend(d.paths)
        packed = await asyncio.to_thread(
            build_context,
            cwd,
            all_paths,
            list(req.domain_context_files),
        )

        sp = build_clarify_system_prompt(
            repo_name=req.repo_name,
            available_domains=req.available_domains,
            default_domain=req.default_domain,
            cwd=str(cwd),
            can_browse=False,
        )
        up = build_clarify_user_prompt(
            epic_title=req.epic_title,
            epic_body=req.epic_body,
            context_block=packed.text,
        )

        messages = [
            {"role": "system", "content": sp},
            {"role": "user", "content": up},
        ]

        start = time.monotonic()
        raw_md, total_cost_usd, errors = await _call(
            litellm,
            model=self.clarify_model,
            messages=messages,
            timeout=req.max_wall_seconds,
            on_text=on_text,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        questions, parse_errors = parse_clarifications(raw_md) if raw_md else ([], ["empty"])

        return ClarifyResult(
            success=not (errors or parse_errors),
            raw_md=raw_md,
            questions=questions,
            prompt_version=CLARIFY_PROMPT_VERSION,
            duration_ms=duration_ms,
            num_turns=1,
            total_cost_usd=total_cost_usd,
            errors=errors + parse_errors,
        )
