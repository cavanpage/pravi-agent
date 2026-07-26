# Agents and sandbox architecture

Pravi has two distinct LLM-driven roles and a pluggable sandbox layer that
hosts the work. This document maps how they fit together: the Protocols that
define the seams, the providers behind them, the budget guardrails the dev
runner enforces, and the sandbox lifecycle around it all.

If you're reading this to figure out *where to add an alternate LLM
provider* or *how to plug in a remote sandbox*, the short answer is "implement
the relevant Protocol and add a branch to its factory." The long answer is
below.

> Out of scope here: the GitHub/Cloudflare integration surface — repo
> creation from starter templates (`src/pravi/templates/`,
> `src/pravi/api/auth_routes.py`) and Cloudflare Pages provisioning
> (`src/pravi/services/cloudflare.py`, `src/pravi/api/cloudflare_routes.py`).
> See the [new repos & Cloudflare Pages guide](../user-guide/new-repo-and-cloudflare.md).

## The two roles

> _See also: [ADR 0002 — LLM-agnostic architect, Claude-only dev](../adr/0002-llm-agnostic-architect-claude-only-dev.md)._

| Role          | Mutates files? | LLM providers | Tool loop                  |
| ------------- | -------------- | ------------- | -------------------------- |
| **Architect** | No             | Claude only   | Read-only subset           |
| **Dev agent** | Yes            | Claude only   | `claude-agent-sdk` owns it |

Both roles run on claude-agent-sdk today. A LiteLLM architect existed and
was dropped per ADR 0002's amendment (2026-07-17) — unused, and its
parity tax wasn't worth the hedge. Cost tiering happens *within* Claude:
each architect mode can pin its own model
(`PRAVI_ARCHITECT_{CLARIFY,DECOMPOSE,DRAFT}_MODEL`), so cheap modes run on
Haiku/Sonnet while heavy ones keep the default. The Protocols make it
*possible* to add providers later; the absence of a second impl is
deliberate.

## Protocols — `src/pravi/agents/protocols.py`

Both roles are typed as `typing.Protocol`s so the rest of the codebase only
depends on the interface.

### `Architect`

```python
class Architect(Protocol):
    async def draft_plan(self, req: ArchitectRequest, *, on_text=None) -> ArchitectResult: ...
    async def clarify_epic(self, req: ClarifyRequest, *, on_text=None) -> ClarifyResult: ...
    async def decompose_epic(self, req: DecomposeRequest, *, on_text=None) -> DecomposeResult: ...
```

Three modes, all read-only:

- **`draft_plan`** — produces a Markdown plan for a single ticket. Output is
  free-form Markdown with the section structure pinned by the system prompt
  (`## Summary`, `## Approach`, `## Changes`, `## Tests`, `## Risks / Out of
  scope`).
- **`clarify_epic`** — asks 2–5 targeted questions about an epic *before*
  decomposition. Output is a fenced ```yaml block of `ClarificationQuestion`s
  the UI renders as editable fields. An empty `questions` list is a valid
  outcome ("nothing to ask, proceed").
- **`decompose_epic`** — breaks an epic into a structured tree of
  `DecomposedFeature` → `DecomposedTask` rows the user can approve in one
  click. Accepts the answered `clarifications` from the previous step so the
  result is grounded.

Each mode accepts an optional `on_text: TextSink` — an `async (str) -> None`
sink the impl calls with incremental text deltas. The SSE endpoints in the
API layer just append; backends that don't support streaming may call it once
with the final blob and behave correctly.

All three request types carry per-run budgets — `max_wall_seconds`,
`max_turns`, `max_cost_usd`. Defaults come from `get_settings()` in the
factory and are intentionally tight (a clarify is 0.5 USD / 5 min, a draft is
1 USD / 5 min, a decompose is 2 USD / 10 min).

### `DevAgent`

```python
class DevAgent(Protocol):
    async def run(
        self,
        req: DevRunRequest,
        *,
        heartbeat: Callable[[], None] | None = None,
        event_sink: EventSink | None = None,
    ) -> DevRunResult: ...
```

One method. The dev agent mutates files inside `req.cwd` (the sandbox-
provisioned worktree) and reports back a transcript plus usage. `heartbeat`
is called after each streamed message — Temporal activities pass
`temporalio.activity.heartbeat` so a hung agent gets killed by the heartbeat
timeout. `event_sink` is awaited for each transcript entry so the API can
push live events out to the UI via Postgres `NOTIFY`.

## Factory dispatch — `src/pravi/agents/factory.py`

The factory turns `PRAVI_*_PROVIDER` settings into concrete instances. Both
factories return the Protocol type, so callers never touch a concrete class:

```python
def get_architect() -> Architect:
    s = get_settings()
    if s.architect_provider == "claude":
        from pravi.agents.architects.claude import ClaudeArchitect
        return ClaudeArchitect(model=s.architect_model, ...)
    raise ValueError(...)
```

One implementation detail worth noting:

- **Per-mode model overrides**: the architect accepts
  `clarify_model` / `decompose_model` / `draft_model` so cheap modes (clarify
  is a tiny prompt, ~one round trip) can use a smaller model than expensive
  ones (decompose can chew through context). Each falls back to the base
  `model` when unset.

`get_dev_agent()` only knows about `"claude"`; passing anything else raises.
When a second dev provider arrives, add another branch.

## The architect implementations — `src/pravi/agents/architects/`

```
architects/
├── claude.py             # ClaudeArchitect — tool loop via claude-agent-sdk
├── clarify_parser.py     # ```yaml → list[ClarificationQuestion]
└── decompose_parser.py   # ```yaml → list[DecomposedFeature]
```

### `claude.py` — `ClaudeArchitect`

Uses `claude-agent-sdk.query()` with `permission_mode="bypassPermissions"`
and a read-only tool allowlist:

```python
ARCHITECT_ALLOWED_TOOLS = ["Read", "Grep", "Glob", "WebFetch"]
```

No `Write`, `Edit`, or `Bash` — the architect physically can't mutate the
repo. `WebFetch` is in the set so the model can look up library docs when
planning.

Each mode (clarify / decompose / draft) follows the same shape:

1. Build a system prompt (from `pravi.prompts.*`) with `can_browse=True`.
2. Build a user prompt from the request.
3. Stream the SDK's `query()` output through a `_StreamBuf` that emits text
   deltas to `on_text` and surfaces tool-use calls as
   `<!--pravi-progress: <tool>|<summary> -->` comment markers (the UI parses
   these to render a live "what the agent is doing" feed; the markdown
   render and the YAML parser both ignore the comments).
4. Wrap the consumer loop in `asyncio.wait_for(_consume(), timeout=
   req.max_wall_seconds)` — a second-layer guardrail on top of the SDK's
   own `max_budget_usd` / `max_turns`.
5. On `ResultMessage`, capture turns/cost/errors and return the typed
   result.

One quirk: the SDK occasionally raises a generic `Exception` *after* it has
already emitted a `ResultMessage`. The clarify / decompose / draft handlers
all salvage the run in that case — if `result_msg` is populated, the error
is logged at warning level and the run completes; otherwise it bubbles up.

> There used to be two more files here — `litellm.py` (one-shot
> completions via LiteLLM) and `context.py` (the pre-packed repo context
> non-browsing models needed). Both were removed with ADR 0002's
> amendment; `domain.context_files` survives in `domains.yaml` as
> curation metadata (see
> [ADR 0005](../adr/0005-no-rag-tool-use-and-explicit-context.md)).

### `clarify_parser.py` and `decompose_parser.py`

Both extract a single ```yaml fenced block from the architect's raw markdown
and parse it with `yaml.safe_load`. Both return
`(parsed_list, errors)` — never raise, never crash:

- **`parse_clarifications`** — expects `{questions: [{text, why?, options?}]}`.
  An empty / missing `questions` is a valid "nothing to ask" outcome and
  returns `([], [])`.
- **`parse_decomposition`** — expects `{features: [{title, description?,
  domain?, depends_on?, persona?, stack?, tasks: [{title, description?,
  persona?, stack?}]}]}`. Each task must have a title; each feature must have
  at least one task. The parser also resolves and validates intra-list
  `depends_on` titles and rejects dependency cycles via a Kahn-style
  iteration.

Both parsers are tolerant by design: the raw markdown is preserved upstream
so the UI can show it for editing when the structure is malformed.

## The dev agent — `src/pravi/agents/dev/claude.py`

`ClaudeDevAgent` is a thin shim around `pravi.sdk_runner.runner.run_dev_agent`:

```python
class ClaudeDevAgent(DevAgent):
    def __init__(self, model: str | None = None) -> None:
        self.model = model

    async def run(self, req, *, heartbeat=None, event_sink=None):
        if req.model is None and self.model is not None:
            req = replace(req, model=self.model)   # paraphrased
        return await run_dev_agent(req, heartbeat=heartbeat, event_sink=event_sink)
```

The heavy lifting lives in `sdk_runner.runner` — the executor exists as a
separate module so the Protocol shim doesn't drag in transcript-capture
logic.

## The dev runner — `src/pravi/sdk_runner/runner.py`

`run_dev_agent()` is the single point where the dev tool loop actually
executes. It owns four responsibilities, and *only* those:

1. **Translate the request into a `ClaudeAgentOptions`** — `system_prompt`,
   `cwd`, `permission_mode="bypassPermissions"`, `max_turns`, the SDK's own
   `max_budget_usd`. `setting_sources=[]` means the SDK does **not** read
   host user/project Claude settings; the dev agent is sandboxed to the
   worktree and shouldn't accidentally pick up local prefs.
2. **Stream messages** — iterate `query(prompt=req.user_prompt,
   options=options)` and translate each `AssistantMessage` / `UserMessage` /
   `SystemMessage` / `ResultMessage` into a `TranscriptEntry`.
3. **Heartbeat on every message** — `heartbeat()` fires after each streamed
   message. In a Temporal activity, that's `temporalio.activity.heartbeat`,
   so the activity proves liveness frequently. Heartbeat *errors* propagate
   (cancel signals come through this path); heartbeat success is silent.
4. **Enforce a wall-clock timeout** — the whole consumer is wrapped in
   `asyncio.wait_for(_consume(), timeout=req.max_wall_seconds)`. This is a
   second layer over the SDK's `max_budget_usd` / `max_turns` so a stuck
   tool call still terminates.

### Budget guardrails

There are *three* independent ways an agent run can be stopped, by design:

| Guardrail            | Set on                       | Triggers when…                                         |
| -------------------- | ---------------------------- | ------------------------------------------------------ |
| `max_turns`          | `ClaudeAgentOptions`         | The SDK has done N tool-use round-trips                |
| `max_budget_usd`     | `ClaudeAgentOptions`         | The SDK's cumulative API cost crosses the budget       |
| `max_wall_seconds`   | `asyncio.wait_for` wrapper   | Real time has passed, even if the SDK is still working |

Belt and braces — the SDK budgets are accurate but only count *its* notion
of progress. The wall-clock wrapper catches the case where the SDK gets
stuck or a tool call hangs.

### Live event mirroring

`event_sink: EventSink | None` is awaited once per `TranscriptEntry`. The
sink builds a one-line human-readable message and a structured payload (the
tool name + input for `tool_use`, the truncated tool output for
`tool_result`, etc.). The Temporal `dev_activity` wires this to a Postgres
`NOTIFY` channel keyed by ticket id — the API's SSE endpoint forwards it
to the UI panel.

Sink errors are caught and logged at warning level: **telemetry must never
break the agent**. A flaky DB connection can't kill an in-progress run.

## Versioned prompts — `src/pravi/prompts/`

```
prompts/
├── architect.py    # draft_plan — VERSION = "architect/v2"
├── clarify.py      # clarify_epic — VERSION = "architect/clarify/v2"
├── decompose.py    # decompose_epic — VERSION = "architect/decompose/v2"
└── developer.py    # dev agent — VERSION = "dev/v2"
```

Each module exports a `VERSION` string and `system_prompt(...)` /
`user_prompt(...)` builders. Conventions:

- **Version bumps are intentional.** Bump `VERSION` whenever you change
  semantics — section ordering, output format, persona logic. Run rows
  store the prompt version that produced them, so we can correlate
  behavioral changes back to the prompt commit.
- **System prompts take a `can_browse: bool`.** When `True`, the architect
  is told it may Read/Grep/Glob; when `False`, it's told context has been
  pre-packed and not to ask for more. `ClaudeArchitect` always passes
  `True`; the `False` path is currently unused (it existed for the removed
  LiteLLM architect and is kept for any future non-browsing provider).
- **Output format is pinned in the system prompt.** The clarify and
  decompose prompts demand a single fenced ```yaml block; the matching
  parser regex (`r"```ya?ml\s*\n(.*?)\n```"`) is the contract.
- **`developer.py` is parameterized by (persona, stack)** — see
  [ADR 0004 — Agent personas](../adr/0004-agent-personas.md). Active
  personas append a "persona modifier" paragraph plus Claude Skills hints
  built from the persona's baseline plus the stack's additional skills.
  Unknown / coming-soon slugs fall back to the generic prompt.

## The sandbox seam — `src/pravi/agents/sandbox/`

> _See also: [ADR 0003 — Sandbox seam, no local mounts](../adr/0003-sandbox-seam-no-local-mounts.md)._

The sandbox owns the working-directory lifecycle: where the worktree lives,
how it's provisioned, how the branch gets pushed, how it's torn down.
Activities consume a `SandboxHandle`; they never touch the underlying paths.

```
sandbox/
├── protocols.py   # Sandbox Protocol + SandboxHandle / SandboxProvisionRequest
├── factory.py     # get_sandbox() — reads PRAVI_SANDBOX_BACKEND
└── local.py       # LocalWorktreeSandbox — git worktree on the host
```

### `Sandbox` Protocol

```python
class Sandbox(Protocol):
    async def provision(self, req: SandboxProvisionRequest) -> SandboxHandle: ...
    async def commits_ahead(self, handle: SandboxHandle, base_ref: str) -> int: ...
    async def push_branch(self, handle, *, token, owner, name) -> tuple[bool, str]: ...
    async def cleanup(self, handle, *, delete_branch: bool = False) -> None: ...
```

Four methods, one impl today (`LocalWorktreeSandbox`).

The `SandboxHandle` is the lingua franca:

- `sandbox_id` — impl-specific identifier used for cleanup (today equals
  `cwd`; for Docker it'd be a container id).
- `cwd` — the filesystem path the dev agent runs against. Today a real host
  path; for future remote backends, an SDK-readable mountpoint with the same
  shape.
- `branch`, `origin_url`, `backend` — branch name, git remote (for parsing
  owner/name in the push step), and an informational backend tag for logs +
  UI hints.

All fields are JSON-serializable so workflows can pass the handle between
activities across the Temporal boundary.

### `LocalWorktreeSandbox`

The only impl today. Behavior:

- **`provision`** — resolves the Repo row to a main clone path (lazy-cloning
  to `clone_base/<owner>__<name>` on first use if needed; legacy rows with
  `local_path` set are reused as-is), then `git worktree add -b <branch>
  <worktree_base>/<ticket-external-id> <base_ref>`. Idempotent: re-running
  on an existing worktree returns the same handle without re-adding.
- **`commits_ahead`** — `git rev-list --count <base_ref>..HEAD` inside the
  worktree.
- **`push_branch`** — picks SSH (uses `ssh-agent`, no token) or HTTPS (uses
  `https://x-access-token:<token>@github.com/<owner>/<name>.git`) based on
  the saved `origin_url`. The token is scrubbed from any returned error
  message.
- **`cleanup`** — `git worktree remove --force`. Falls back to
  `shutil.rmtree` if the git command fails. Idempotent — calling on an
  already-removed worktree just logs.

### Factory dispatch

```python
def get_sandbox() -> Sandbox:
    s = get_settings()
    if s.sandbox_backend == "local":
        return LocalWorktreeSandbox()
    # Future: "docker", "cloudflare", "e2b", … each implements Sandbox.
    raise ValueError(...)
```

Same pattern as the agent factories: one branch per backend, picked by env
var. Adding a remote backend is "implement the four methods + add a branch".

### What's not in the seam (yet)

The Protocol is intentionally minimal — provision / push / cleanup, plus a
single read query (`commits_ahead`) the PR-opening activity needs. Anything
else (file copy, in-sandbox shell, snapshot, …) is currently handled by the
dev agent's own tool loop running inside `handle.cwd`. The Claude SDK can't
`cd` into a remote container, so a future `DockerSandbox` will probably need
to move the dev-agent execution *inside* the sandbox — but the Temporal
activity boundary stays where it is. See ADR 0003's "When to revisit"
section for the longer plan.

## How it all fits together

A single ticket run touches the layers in this order:

```
Workflow                 sandbox_activity → Sandbox.provision()
   │                                            ↓
   │                                       SandboxHandle
   │                                            ↓
   │                     dev_activity → get_dev_agent().run(cwd=handle.cwd, ...)
   │                                            ↓
   │                                       sdk_runner.run_dev_agent
   │                                            ↓                         ↓
   │                                       claude-agent-sdk          event_sink
   │                                            ↓                         ↓
   │                                       (Edits in worktree)       Postgres NOTIFY → SSE → UI
   │                                            ↓
   │                     pr_activity → Sandbox.commits_ahead + .push_branch
   │                                            ↓
   │                     sandbox_activity → Sandbox.cleanup()
```

Architects sit one level up — they run during the *planning* phase
(clarify → decompose → draft) before any dev activity is scheduled. They
read the repo but never get a `SandboxHandle`; their `cwd` is just the
main repo clone passed in via the `ArchitectRequest`.

## Adding a new provider

**A new architect provider** (e.g. raw Anthropic SDK, Azure OpenAI):

1. Add `src/pravi/agents/architects/<name>.py` implementing the three
   `Architect` methods. Reuse `clarify_parser` / `decompose_parser` for
   YAML extraction; reuse `context.build_context()` if your backend can't
   browse the repo.
2. Add a branch to `agents/factory.get_architect()` keyed on a new
   `architect_provider` setting value. Lazy-import inside the branch.
3. Bump the relevant prompt `VERSION` only if you're changing the prompt
   semantics — *adding a backend that uses the existing prompts* doesn't
   warrant a version bump.

**A new dev provider**: implement `DevAgent`, add a branch to
`get_dev_agent()`. The hard part is the tool loop — `sdk_runner.run_dev_agent`
is Claude-specific; a non-Claude impl would need its own equivalent
runner that preserves the heartbeat + budget contract.

**A new sandbox backend** (Docker, Cloudflare, e2b, …): implement the four
`Sandbox` methods, add a branch to `get_sandbox()`. Keep `SandboxHandle`
JSON-serializable. See ADR 0003 for the design constraints and the open
question about moving dev-agent execution inside the sandbox.
