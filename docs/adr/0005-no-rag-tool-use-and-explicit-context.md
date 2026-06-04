# ADR 0005 — No vector-similarity RAG; deterministic retrieval patterns only

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** @cavanpage

## Context

Pravi's agents need external context to do their jobs:

- The **architect** reads the repo + the GitHub issue (if imported) +
  ancestor ticket bodies to clarify, decompose, and draft plans.
- The **dev agent** reads source + tests to implement the plan inside a
  worktree.

A natural question for any LLM application: *should we build a RAG layer?*

### First — disambiguating "RAG"

The term gets used two ways:

- **Strict / common-parlance RAG** — chunk the corpus, embed each chunk
  with a learned model, store vectors in a dedicated index, and at
  query time retrieve top-k by cosine similarity. *This* is what most
  people mean when they say RAG.
- **Literal "retrieval-augmented generation"** — *any* pattern where
  external content is fetched at (or near) generation time and
  injected into the prompt. By this lens, almost every LLM app is
  doing some flavour of retrieval.

Pravi has none of the first but *does* have several patterns of the
second. Calling these out so the line stays clear:

1. **Agentic retrieval** via `claude-agent-sdk`. The architect runs
   with `allowed_tools=["Read", "Grep", "Glob", "WebFetch"]` and the
   dev agent gets the full filesystem tool loop. *The model itself*
   decides what to read at query time.
   [agents/architects/claude.py](../../src/pravi/agents/architects/claude.py).
2. **`WebFetch` tool** — the architect can pull URL contents into its
   own loop. This is the most RAG-shaped pattern in the codebase
   (per-query fetch + augment), just driven by the model rather than
   a similarity score.
3. **GitHub issue import** — at /issues convert time, pravi calls the
   GitHub Issues API to fetch a chosen issue, copies the title + body
   into the new ticket, and persists it. One-shot, user-mediated,
   deterministic. Counts as retrieval-augmentation in the literal
   sense; not in the dense-vector sense.
4. **Hierarchical ancestry merge** — `build_ancestral_body()` in
   [activities/db_activity.py](../../src/pravi/activities/db_activity.py)
   reads parent + grandparent ticket bodies from Postgres at architect
   call time and prepends them into the prompt.
5. **Pre-packed `context_files`** for the LiteLLM architect path
   ([agents/architects/context.py](../../src/pravi/agents/architects/context.py))
   — concatenates user-curated files from `domain.context_files` plus
   a 2-deep `git ls-files` tree. Capped at ~80 KB.

None of these involve embeddings, vector storage, or similarity
search.

## Decision

**No vector-similarity RAG. Keep the deterministic retrieval patterns
above (tool-use, WebFetch, issue import, ancestry merge, explicit
`context_files`) and don't add a vector index / embedding model / dense
similarity search on top.**

Translated to dependencies: no pgvector / chromadb / qdrant / faiss /
voyage / OpenAI embeddings / re-indexing-on-commit. The retrieval
patterns we *do* have all have one thing in common — what gets
retrieved is **deterministic** given the inputs (a URL, an issue
number, a parent ticket id, a `context_files` list). Embedding-based
RAG is the opposite: same inputs can produce different retrieved
chunks as the index / embedding model evolves.

For Claude-backed paths: rely on the SDK's tool loop. The architect's
`Read`/`Grep`/`Glob`/`WebFetch` calls are recorded as a transcript on
every run; we can audit "did it look at the right files?" without any
new infrastructure.

For non-Claude paths (LiteLLM architect): rely on
`domain.context_files` + the directory tree pre-pack + the issue body
when imported. The cost is on the user to curate the
`context_files` list; the payoff is predictable prompts and zero
re-indexing.

## Consequences

### Wins
- **No new dependency surface.** No pgvector / chromadb / qdrant / faiss
  in `pyproject.toml`. No embedding-model API key. No background indexer.
  No re-indexing-on-commit cron. The "what could go wrong" surface stays
  small.
- **Deterministic context.** Same inputs → same prompts. RAG
  introduces a moving target — embeddings drift on model updates,
  similarity ranking can flip on tiny corpus changes. Hard to debug "why
  did this run get different context than that one?".
- **Auditable retrieval.** The SDK's tool-use transcript shows exactly
  which files Claude read and why (the model narrates its reasoning).
  RAG retrieves silently — you'd need a separate trace of "what got
  pulled into the prompt".
- **Plays well with the sandbox seam.** When pravi eventually runs the
  dev agent inside a remote sandbox (ADR 0003), tool-use just keeps
  working — the SDK's `Read` operates against the sandbox's filesystem.
  RAG would mean shipping the vector DB to the sandbox too, or making a
  network call out per query.
- **Repos are GitHub-identified** (ADR 0003). Building a per-repo index
  would mean keeping the index in sync with the GitHub state, which is
  a real ops problem we don't have.

### Costs (acknowledged)
- **The LiteLLM path is *weaker*.** Non-Claude architects can't browse;
  they see only `context_files` + a tree. For repos where the user
  hasn't carefully curated `context_files`, the LiteLLM architect is
  flying blind. This is partly why `PRAVI_ARCHITECT_PROVIDER` defaults
  to `claude` (see ADR 0002).
- **Tool-use has latency.** Each `Read`/`Grep` call is a round-trip
  inside the agent loop. A well-built RAG could front-load context in
  one shot. We're betting that Claude's tool loop is fast enough +
  prompt caching makes the per-turn cost cheap.
- **Big repos may overshoot wall-clock.** Tool-use scales with the
  number of files the model wants to read; a giant monorepo could time
  out the architect's budget. Mitigated by the per-domain `paths`
  scoping in `domains.yaml`, which fences the agent into one slice.
- **No semantic retrieval.** If a user asks the architect "find code
  similar to this snippet" — tool-use can grep for literals but can't
  do semantic similarity. Today's architect doesn't ask that kind of
  question; if it ever does, we'd need RAG.

## Alternatives considered

### Per-repo pgvector index + OpenAI embeddings
Considered (it's the default playbook). Rejected because:
- Adds two new external dependencies (pgvector + embeddings API).
- Needs a re-index job on every commit, or at least on first dev run —
  another background-task pattern next to the sandbox + agent_drafts +
  clarification jobs.
- The Claude SDK's tool loop is already doing the equivalent job at
  query time, with prompt caching paying down the cost.
- Vector quality for code chunks is genuinely mediocre vs. natural
  language — chunk boundaries break identifiers, comments dominate
  embeddings, semantically similar code with different syntax doesn't
  retrieve cleanly. The recall floor is lower than people assume.

### "Just-in-time" RAG via an MCP server
Cleaner architecturally — the SDK exposes MCP tools, an MCP server
could host `vector_search(query)` and pravi calls into it on demand.
Rejected for now because we'd still need the indexer infrastructure
behind it; the seam doesn't reduce the ops cost.

### Auto-pack arbitrary files under `domain.paths`
Pre-pack *all* in-domain files into the LiteLLM prompt rather than
asking the user for `context_files`. Rejected because it makes prompt
size unpredictable and easy to blow past provider context windows on
bigger repos. The explicit-curation contract keeps prompts bounded.

### Repo-level summary index (cheap "RAG-lite")
At domain registration, have the architect generate a short paragraph
summarizing each file under `paths`. Stuff the summaries (not the
files) into every prompt. Rejected today because the summaries decay
on every commit — we'd be back to the re-index cron. Worth
reconsidering if the LiteLLM architect path becomes load-bearing.

## When to revisit

**Add a RAG layer if any of these become true:**
- We have a feature the architect needs that *requires* semantic
  similarity over the corpus (e.g. "find code like this", "explain
  why this is duplicated") — none today.
- Telemetry shows the Claude architect repeatedly *fails to find*
  context that's clearly findable in the repo. Mitigation order
  matters: try better `domains.yaml` scoping → bump architect
  `max_turns` → only then RAG.
- The LiteLLM architect becomes the primary path (users on
  Gemini/Bedrock/Ollama outnumber Claude users) and `context_files`
  curation becomes a real burden.
- We add a "search across all my pravi-tracked repos" feature — that's
  a cross-repo retrieval problem tool-use can't solve cleanly.

**If we add RAG:** start with an MCP server (`vector_search`) so the
existing SDK tool-loop is the consumer. Index per-repo, scoped to
`domain.paths`. Re-index on PR merge (we already detect this in the
PR activity). Use the cheapest credible embedding model — quality
floors for code embeddings make expensive models wasted spend.

**If we add it and find we don't need it:** delete the indexer, drop
the dep, and revert to this ADR. The seam in the SDK + MCP keeps
deletion cheap.

## Related

- ADR [0002 — LLM-agnostic architect, Claude-only dev](0002-llm-agnostic-architect-claude-only-dev.md)
  — explains why the LiteLLM architect tolerates a weaker context
  story than the Claude one.
- ADR [0003 — Sandbox seam](0003-sandbox-seam-no-local-mounts.md) —
  tool-use crossing the sandbox boundary is "just file I/O"; RAG
  would need its own crossing.
- `src/pravi/agents/architects/claude.py` — the SDK tool loop.
- `src/pravi/agents/architects/context.py` — the LiteLLM pre-pack
  helper.
- `src/pravi/activities/db_activity.py::build_ancestral_body` — the
  hierarchical context-merge pattern.
- `examples/blissful-infra-domains.yaml` — what a curated
  `context_files` list looks like in practice.
