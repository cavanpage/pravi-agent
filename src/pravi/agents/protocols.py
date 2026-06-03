"""Provider-agnostic seams for the agents layer.

Today there are two distinct LLM-driven roles in pravi:

  * The **architect** drafts a Markdown plan from a ticket. Text in, text out.
    Multiple providers are supported via the `Architect` Protocol; today
    `ClaudeArchitect` and `LiteLLMArchitect` implement it.

  * The **dev agent** executes the approved plan inside a worktree (file edits,
    bash, optional MCP servers). Today only `ClaudeDevAgent` implements
    `DevAgent` — reproducing claude-agent-sdk's tool loop for other providers
    is a much larger effort and out of scope. The Protocol is here so we have
    a clean place to swap in alternates when there's a concrete reason.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# ---- Architect ---------------------------------------------------------------


@dataclass
class ArchitectRequest:
    repo_path: str
    repo_name: str
    domain_name: str
    domain_description: str
    domain_paths: list[str]
    ticket_title: str
    ticket_body: str
    # The Domain config snapshot for context-packing (non-Claude architects).
    # Optional so existing call sites that don't yet pass it keep working.
    domain_context_files: list[str] = field(default_factory=list)
    # Per-run budgets — defaults come from get_settings() in the factory.
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


@dataclass
class DomainBrief:
    """Lightweight domain descriptor passed to the decomposition architect.

    Only the fields it needs to pick a domain per feature — keeps the prompt
    compact and provider-independent.
    """

    name: str
    description: str
    paths: list[str]


@dataclass
class DecomposedTask:
    title: str
    description: str = ""
    # Persona + stack — see ADR 0004. The decompose architect picks
    # from active personas; unknown / coming_soon slugs are kept but
    # the dev agent falls back to generic at run time.
    persona: str | None = None
    stack: str | None = None


@dataclass
class DecomposedFeature:
    title: str
    description: str = ""
    domain: str | None = None  # Optional override; otherwise inherits from epic
    tasks: list[DecomposedTask] = field(default_factory=list)
    # Titles of sibling features (same epic) this feature depends on. The
    # decompose-approve route resolves these to FeatureDependency rows.
    # Unresolvable titles → 400 at approve time.
    depends_on: list[str] = field(default_factory=list)
    # Persona + stack — see ADR 0004. Inherited by tasks under this
    # feature unless they override.
    persona: str | None = None
    stack: str | None = None


@dataclass
class ClarificationQA:
    """A question the architect asked and the user's (possibly empty) answer.

    Empty `answer` means the user skipped — the decomposer is expected to
    proceed with an explicit assumption rather than block.
    """

    question: str
    answer: str = ""
    why: str = ""  # optional architect rationale carried through for context


@dataclass
class ClarifyRequest:
    repo_path: str
    repo_name: str
    epic_title: str
    epic_body: str
    available_domains: list[DomainBrief]
    default_domain: str | None
    domain_context_files: list[str] = field(default_factory=list)
    max_wall_seconds: int = 300
    max_turns: int = 20
    max_cost_usd: float = 0.5


@dataclass
class ClarificationQuestion:
    text: str
    why: str = ""
    # Optional preset answers. Empty list means "free-text question". When
    # present, the UI renders radio buttons; the user can still type a write-
    # in answer if none of the presets fit. The architect proposes these
    # when there's a small, well-defined choice set worth offering.
    options: list[str] = field(default_factory=list)


@dataclass
class ClarifyResult:
    success: bool
    raw_md: str
    questions: list[ClarificationQuestion]
    prompt_version: str
    duration_ms: int
    num_turns: int
    total_cost_usd: float | None
    errors: list[str] = field(default_factory=list)


@dataclass
class DecomposeRequest:
    repo_path: str
    repo_name: str
    epic_title: str
    epic_body: str
    available_domains: list[DomainBrief]
    default_domain: str | None  # Epic's domain if any; otherwise none
    domain_context_files: list[str] = field(default_factory=list)
    # Clarifications carried into the decomposer prompt. Empty answers are OK
    # — the architect should flag them as explicit assumptions.
    clarifications: list[ClarificationQA] = field(default_factory=list)
    max_wall_seconds: int = 600
    max_turns: int = 30
    max_cost_usd: float = 2.0


@dataclass
class DecomposeResult:
    """Output of `decompose_epic`.

    `raw_md` is the full architect response (preserved so the UI can render
    it for editing). `features` is the parsed structured tree — empty if
    parsing failed (see `errors`).
    """

    success: bool
    raw_md: str
    features: list[DecomposedFeature]
    prompt_version: str
    duration_ms: int
    num_turns: int
    total_cost_usd: float | None
    errors: list[str] = field(default_factory=list)


class Architect(Protocol):
    """Plan-drafting agent. Read-only with respect to the repo.

    Three modes:
      - `draft_plan` — plan for a single ticket (a task, usually).
      - `clarify_epic` — propose 2–5 targeted questions about an epic before
        decomposition. Cheap; runs without producing a structure.
      - `decompose_epic` — break an epic into a structured tree of features
        and tasks the user can review + approve in the UI. Accepts optional
        `clarifications` from the clarify step to ground the result.

    Each method accepts an optional `on_text` sink that's fired with each
    incremental text delta the model produces. Implementations that don't
    support streaming may call it once with the final blob — the SSE
    endpoints just append, so behavior degrades gracefully.
    """

    async def draft_plan(
        self,
        req: ArchitectRequest,
        *,
        on_text: TextSink | None = None,
    ) -> ArchitectResult: ...

    async def clarify_epic(
        self,
        req: ClarifyRequest,
        *,
        on_text: TextSink | None = None,
    ) -> ClarifyResult: ...

    async def decompose_epic(
        self,
        req: DecomposeRequest,
        *,
        on_text: TextSink | None = None,
    ) -> DecomposeResult: ...


# ---- Dev agent ---------------------------------------------------------------


@dataclass
class DevRunRequest:
    cwd: str
    system_prompt: str
    user_prompt: str
    max_wall_seconds: int
    max_turns: int
    max_cost_usd: float
    model: str | None = None


@dataclass
class TranscriptEntry:
    kind: str  # "assistant_text" | "tool_use" | "tool_result" | "system" | "result"
    text: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output_summary: str | None = None
    payload: dict[str, Any] | None = None


@dataclass
class DevRunResult:
    success: bool
    stop_reason: str | None
    num_turns: int
    duration_ms: int
    duration_api_ms: int
    total_cost_usd: float | None
    session_id: str | None
    result_text: str | None
    transcript: list[TranscriptEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# Event sink signature: kind, message, payload (None if no structured data).
# Returns an Awaitable so the sink can write to Postgres / NOTIFY async.
EventSink = Callable[[str, str, dict[str, Any] | None], Awaitable[None]]


# Text-stream sink for architect calls — fired on each incremental chunk.
# The caller accumulates and renders; the final structured result is still
# returned by the architect method.
TextSink = Callable[[str], Awaitable[None]]


class DevAgent(Protocol):
    """Mutates files inside `req.cwd`. Implementations own their own tool loop."""

    async def run(
        self,
        req: DevRunRequest,
        *,
        heartbeat: Callable[[], None] | None = None,
        event_sink: EventSink | None = None,
    ) -> DevRunResult: ...
