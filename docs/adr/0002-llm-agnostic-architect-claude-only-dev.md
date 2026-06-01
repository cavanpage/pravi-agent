# ADR 0002 — LLM-agnostic architect, Claude-only dev

- **Status:** Accepted
- **Date:** 2026-05-26
- **Deciders:** @cavanpage

## Context

Pravi runs two distinct LLM workloads with very different requirements:

- **Architect** — read-only reasoning. Three modes (clarify, decompose, draft).
  Uses `Read`/`Grep`/`Glob`/`WebFetch` *at most*; never mutates the filesystem.
  Cost/latency varies wildly by mode (clarify is small and chatty; decompose +
  draft want strong reasoning).
- **Dev agent** — filesystem-mutating executor. Drives the full tool loop
  (`Read`, `Edit`, `Write`, `Bash`, `Grep`, etc.) inside a worktree, with
  prompt caching, subagents, MCP tool composition, budget enforcement, and a
  structured transcript. This is the expensive, complex part.

Two natural questions:
1. Should the **architect** be Claude-only, or pluggable across providers?
2. Should the **dev agent** be Claude-only, or pluggable across providers?

These are independent decisions because the two roles have wildly different
provider-feature requirements.

## Decision

**Architect: LLM-agnostic. Dev: Claude-only.**

- The architect is hidden behind an `Architect` Protocol in
  `agents/protocols.py`. Two implementations ship today:
  - `ClaudeArchitect` (default) — via `claude-agent-sdk` with read-only tools.
    Per-mode model overrides (`PRAVI_ARCHITECT_{CLARIFY,DECOMPOSE,DRAFT}_MODEL`).
    Streams partial messages + tool-use progress markers to the UI.
  - `LiteLLMArchitect` — via [LiteLLM](https://docs.litellm.ai/), covering
    OpenAI / Gemini / Bedrock / Azure / Mistral / Ollama in one dep. *No* tool
    use — context is pre-packed by `agents/architects/context.py` (the
    `domains.yaml` `context_files` + a domain-scoped directory tree, capped
    at ~80KB), then sent as a single user message.
- The dev agent is also behind a Protocol (`DevAgent` in
  `agents/protocols.py`) — but only `ClaudeDevAgent` exists, and we don't plan
  to add a second one.

Selection is a `get_architect()` / `get_dev_agent()` factory that reads
`PRAVI_ARCHITECT_PROVIDER` / `PRAVI_DEV_PROVIDER` from config.

## Consequences

### Wins
- **Cost flexibility on the architect.** Clarify defaults to Haiku 4.5
  (`PRAVI_ARCHITECT_CLARIFY_MODEL=claude-haiku-4-5-20251001`) — latency is the
  thing users feel here. Decompose / draft stay on stronger models because
  their output cascades into every downstream task.
- **Provider choice on the architect.** A user without an Anthropic API key
  but with OpenAI / Gemini / Bedrock / Ollama can run the architect locally
  by flipping `PRAVI_ARCHITECT_PROVIDER=litellm`.
- **Dev quality gated by the best Claude tool loop, not a lowest-common-
  denominator framework.** The SDK ships Anthropic-first features (prompt
  caching with 90%+ hit rate on iterated runs, subagents, sandbox modes,
  MCP, partial-message streaming) months before generic frameworks catch up.
- **Clean seam for future providers.** Adding e.g. `OpenAIDevAgent` is
  "implement `DevAgent.run()` against the OpenAI Responses API" — call sites
  unchanged.

### Costs (acknowledged)
- **Anthropic dependency on the hot path.** The dev agent — the most
  expensive call — is Claude-only. An Anthropic outage means dev work stops,
  even with the architect on LiteLLM.
- **LiteLLM architect can't browse.** It gets pre-packed context only. That's
  fine for clarify (the epic body is usually self-contained) and acceptable
  for decompose (the user typically knows the scope). It's noticeably worse
  for plan-drafting tasks that benefit from spelunking the codebase — which is
  why `PRAVI_ARCHITECT_PROVIDER` defaults to `claude` even though `litellm` is
  available.
- **Two architect impls to maintain.** The prompts are shared
  (`prompts/{clarify,decompose,architect}.py`) but the streaming / tool-event
  emission code lives in each implementation. Drift risk over time.
- **Dev Protocol is a fiction today.** `DevAgent` exists as a Protocol but
  with one impl — the abstraction earns its keep only when a second backend
  appears. Acceptable as a forward-compatible seam; flagging it as
  speculative architecture if the cost shows up later.

## Alternatives considered

### Pure Claude for both (no LiteLLM, no Protocols)
Simpler — one provider, no factory, no `context.py` pre-packing. Rejected
because users have legitimate reasons to want OpenAI/Gemini/Ollama for the
architect (cost, latency, data residency, no Anthropic account). The cost of
maintaining two architect impls is small next to the cost of locking out
those users.

### LiteLLM for both (architect + dev)
Tempting in principle — full provider freedom. Rejected because building a
non-Claude dev tool loop with feature parity to claude-agent-sdk (prompt
caching, structured transcript, budget enforcement, subagents, MCP, sandbox)
is months of work for marginal user benefit at current scale. If it ever
becomes critical we'd build it; right now the SDK's velocity is doing the
work for us.

### LangChain agent framework for the dev agent
Considered briefly. Rejected on the same axis as ADR 0001's LangGraph
discussion: layering a third orchestrator on top of Temporal + the Claude
SDK adds checkpoint/state confusion without solving anything the SDK
doesn't already solve.

## When to revisit

**Add a non-Claude dev backend if:**
- Anthropic outage tolerance becomes a real product requirement (e.g. an SLA
  to customers). Today we accept outages as downtime.
- Cost forces multi-provider failover for dev workloads (only true at much
  higher scale than the current POC).
- Another vendor ships a tool-loop SDK with comparable feature parity —
  prompt caching, structured transcript, subagents, MCP, partial-message
  streaming. OpenAI's Responses API is closest today; revisit if/when it
  ships subagent equivalents and matches Anthropic's caching economics.

**Drop the LiteLLM architect if:**
- After N months, telemetry shows nobody actually runs with
  `PRAVI_ARCHITECT_PROVIDER=litellm`. Then the seam is paying tax for nobody.
- We add features to the architect (e.g. follow-up tool use during clarify,
  iterative refinement loops) that only Claude can run cleanly, and keeping
  LiteLLM parity holds us back.

**Drop the `DevAgent` Protocol** if after, say, a year we still have exactly
one impl and no concrete plan for a second. The Protocol costs little to
keep, but speculative interfaces rot — better to delete and re-derive when
the second impl actually shows up.

## Related

- `src/pravi/agents/protocols.py` — `Architect`, `DevAgent` Protocols + the
  shared dataclasses (request / result / clarifications).
- `src/pravi/agents/factory.py` — `get_architect()`, `get_dev_agent()`.
- `src/pravi/agents/architects/{claude,litellm,context}.py` — the two impls
  + the pre-pack helper used by the non-tool-using LiteLLM path.
- `src/pravi/agents/dev/claude.py` — the dev agent (wraps `sdk_runner`).
- `.env.example` — `PRAVI_ARCHITECT_PROVIDER` / `_MODEL` / per-mode overrides;
  `PRAVI_DEV_PROVIDER` / `_MODEL`.
- [ADR 0001](0001-orchestration-temporal-no-langgraph.md) — the related
  "what framework runs around these agents" decision.
