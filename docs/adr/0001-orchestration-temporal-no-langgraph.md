# ADR 0001 — Orchestration: Temporal, no LangGraph

- **Status:** Accepted
- **Date:** 2026-05-26
- **Deciders:** @cavanpage

## Context

Pravi orchestrates Claude agents across a multi-stage lifecycle (clarify →
decompose → plan → dev → PR), with human gates at the plan and merge steps and
potentially long pauses in between. Three orchestration options were on the
table:

1. **Temporal alone** — durable workflows, signals/queries, two task queues for
   budget concurrency. Agents are plain Python inside activities.
2. **Temporal + LangGraph** — Temporal for the outer workflow, LangGraph for
   intra-agent state machines (branching, retries, checkpoints).
3. **No workflow engine** — FastAPI background tasks + a DB-backed state machine
   + an `asyncio.Semaphore` for the LLM concurrency cap.

The dev agent itself runs on `claude-agent-sdk`, which already owns the inner
tool loop (Read/Grep/Bash, prompt caching, budgets, subagents). We're not
building that part.

## Decision

**Keep Temporal. Do not introduce LangGraph.**

- **Temporal** carries the outer lifecycle (FeatureWorkflow, signals for plan
  approval, the LLM/features task-queue split, search attributes).
- **claude-agent-sdk** carries the inner agent loop. No second framework on top.
- **`services/`** (clarification, agent_draft, github) carries the *non-Temporal*
  background work — clarify, decompose draft, plan draft. These are tab-resilient
  via `asyncio.create_task` + DB persistence, not Temporal workflows. Adding them
  to Temporal would buy nothing they don't already get from DB rows + polling.

## Consequences

### Wins
- One agent-loop framework, not two. The SDK is purpose-built for Claude tool
  use; LangGraph would mostly proxy back to it.
- Temporal pays off where it matters: surviving worker restarts mid-dev-run,
  long human-gated pauses, search-attribute filtering in the UI, capped LLM
  concurrency via task queues.
- No double state store. LangGraph checkpoints + Temporal event history would
  be two ways to "resume from" — confusing under load.

### Costs (acknowledged)
- Temporal is genuinely heavy for current scope: compose adds `temporal`,
  `temporal-postgres`, `temporal-ui`; we run two worker processes; deterministic
  replay constrains workflow code (`workflow.now()`, no real Date, no random in
  workflow code). For a single-user local POC, it *is* overkill today.
- New contributors must learn Temporal semantics before touching `workflows/`.
- The clarify / decompose / plan-draft services duplicate the "background job
  with persisted state" pattern outside Temporal — there is now a second
  background-runner idiom in the codebase. Acceptable because Temporal's per-run
  overhead doesn't fit cheap LLM kickoffs, but worth knowing.

## Alternatives considered

### LangGraph (in addition to Temporal)
Rejected. LangGraph's value is *expressing an agent's decision graph* — but the
SDK's tool loop already does that, and the outer flow is Temporal's job. Adding
LangGraph layers a third orchestrator with its own checkpoint store on top of
two existing ones. Reconsider only if we need agent patterns the SDK can't
express cleanly (debate loops, planner-executor with explicit backtracking,
non-linear multi-agent consultation).

### No workflow engine (FastAPI + DB + asyncio)
Considered seriously. Honest assessment: for the *current* scope (single-user,
single-process, no scheduled retries, no multi-day sagas) this would work and
remove real ops surface. We kept Temporal as future-proofing — see "When to
revisit" below.

## When to revisit

**Drop Temporal if all of these become true:**
- Pravi stays single-user / single-tenant for the foreseeable future.
- We add no scheduled retries, no multi-day sagas, no cross-service workflows.
- Workflow code stops touching anything Temporal-specific (the only signals
  we use today are `approve_plan` and `cancel`, both replaceable by DB-backed
  state transitions polled from the API).
- The team feels the ops cost (two worker processes + compose stack) more
  acutely than the durability benefits.

Migration sketch (if we do): replace `FeatureWorkflow` with a status column on
`Ticket` (already mostly there) + a `lifecycle_runner` service mirroring the
existing `services/clarification.py` pattern. The plan-approve signal becomes
a DB write that the runner polls. The two task queues become two
`asyncio.Semaphore`s. Workers fold into the FastAPI process. Search-attribute
filtering moves to Postgres indexes on the Ticket table (we already have
most of this via `/api/runs`).

**Add LangGraph only if:**
- We introduce a non-linear multi-agent pattern (e.g. architect-vs-reviewer
  debate) that we've tried and failed to express via SDK subagents.
- We want LangSmith tracing badly enough to pay for a third orchestrator.

Neither is on the roadmap.

## Related

- `src/pravi/workflows/` — the actual Temporal workflows.
- `src/pravi/services/{clarification,agent_draft}.py` — the non-Temporal
  background pattern that handles cheap agent kickoffs.
- README.md — public-facing summary; this ADR is the deep version.
