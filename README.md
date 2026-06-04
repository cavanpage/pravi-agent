# Pravi Agent

Agentic feature builder for domain-driven repos, powered by Claude.

**Status:** Working POC. Connect a GitHub repo, browse its issues, decompose
an epic into a dependency-aware feature/task tree, let the architect draft
plans (or import work straight from an issue), then have a Claude dev agent
build it in an isolated worktree and open a draft PR — all from a React UI.

## What it is

`pravi` turns an idea (or a GitHub issue) into shipped code:

1. **Plan** — the architect agent (Claude, read-only) clarifies, decomposes an
   epic into features + tasks, and drafts per-task implementation plans.
2. **Build** — a Claude dev agent (claude-agent-sdk) executes the approved plan
   inside a per-ticket git worktree, runs domain tests, and pushes a draft PR.
3. **Review** — a human reviews the plan up front and the PR at the end.

Principles:

- **Humans gate** the *plan* and the *final PR merge*. Everything between —
  develop, review, test, analyze — is autonomous.
- **Domain-driven**: each dev agent is scoped to a single domain declared in
  the target repo's `.builder/domains.yaml`.
- **Nothing is lost to a closed tab**: every agent kickoff (clarify, decompose,
  plan draft) runs as a backgrounded, DB-persisted job with live progress that
  the UI polls — closing the tab or navigating away never cancels a run.
- **LLM-agnostic architect**: defaults to Claude via claude-agent-sdk, but the
  architect can run on any LiteLLM-supported provider. The dev agent is
  Claude-only (it needs the full tool loop).

## Capabilities

**Hierarchy & planning**
- Epic → Feature → Task tree. Epics auto-decompose into features + tasks via the
  architect; features can declare dependencies, rendered as a topological
  **roadmap** (parallelizable "waves").
- **Clarify** step: on epic creation a background job gathers targeted
  questions (some multiple-choice) before decomposition. Runs on a fast model
  (Haiku by default) and streams its activity live.
- Cost **budget ceilings** per ticket, inherited down the Epic → Feature → Task
  chain and enforced before each dev run (and surfaced as remaining-budget
  hints + validation in the UI).

**GitHub integration** (OAuth, in-UI)
- Connect GitHub from the header; search your repos and pick one when creating a
  ticket (lazily cloned on first use).
- Browse a repo's **issues** at `/issues` and convert any of them into an epic,
  feature, or task — pravi comments + labels (`pravi-imported`) back on the
  source issue for traceability.
- On a successful dev run, pravi pushes the branch and opens a **draft PR**.

**Personas and stacks** (two-axis specialization)
- Each task carries a **persona** (`architect`, `frontend`, `backend`, `tester`,
  `tech_writer`, `other`) and a **stack** slug (`python-fastapi`,
  `typescript-react`, `go-stdlib`, …). The decompose architect assigns both at
  task-creation time; the dev agent's system prompt is conditioned on the pair.
- Persona catalog in `src/pravi/personas/catalog.py` — active personas declare a
  `system_prompt_modifier` (e.g. the tester is forbidden from editing source
  outside `tests/`); a further 13 personas are listed as `coming_soon` for the
  roadmap UI and fall back to the generic prompt.
- Stack catalog in `src/pravi/personas/stacks.py` is open-set — known stacks
  contribute `additional_skills` hints on top of persona baselines; unknown
  slugs resolve to `unknown` and no hints are loaded.

**Sandbox seam**
- The dev agent's working directory is provisioned through a `Sandbox` Protocol
  (`src/pravi/agents/sandbox/`) so the workflow never touches paths directly.
  Today's only implementation is `LocalWorktreeSandbox` — lazy clone into
  `clone_base/<owner>__<name>`, `git worktree add` per ticket — but the seam
  is the integration point for future Docker / Cloudflare / remote backends,
  selected via `PRAVI_SANDBOX_BACKEND`. See
  [ADR 0003](docs/adr/0003-sandbox-seam-no-local-mounts.md).

**Spend tracking by persona / stack**
- `src/pravi/budget/by_persona.py` aggregates `run_finished` event cost grouped
  by the task ticket's persona or stack — NULL persona rolls up under `other`,
  NULL stack under `unknown`. Windowed (`7d`, `30d`, `all`) and optionally
  scoped to one repo.
- Surfaced as `GET /api/spend/by-persona` and `GET /api/spend/by-stack`
  (params: `window`, `repo_id`) for the dashboard's "what's burning the
  budget" view.

**Web UI** (React + Vite + Tailwind)
- Home dashboard with persisted view state (kind filter / sort / search).
- Markdown plan editor with live preview; approve/cancel buttons signal the
  workflow.
- Live status over SSE; live "what the agent is doing" activity feeds during
  clarify / decompose / plan drafting.

## Stack

- Python 3.11+, [uv](https://docs.astral.sh/uv/)
- [Temporal](https://temporal.io/) for durable workflow orchestration
- [claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python) — the
  dev agent's filesystem-mutating executor (read-only tool subset for the
  architect)
- [LiteLLM](https://docs.litellm.ai/) — optional alternative architect backend
- Postgres for app state (SQLAlchemy 2.x async + Alembic)
- FastAPI + SSE backing a React 18 + TypeScript + Vite + Tailwind UI (React Query)
- Typer CLI (`pravi`)

> **Why this stack?** Architecture decisions (orchestration choice, LLM
> backends, "is Temporal overkill?", "why not LangGraph?") are written up
> as ADRs in [docs/adr/](docs/adr/) with honest tradeoffs and the
> "revisit when…" triggers that would push us to change course.

## Documentation

- **[User guide](docs/user-guide/README.md)** — index of the four guides for
  bringing pravi to your own repo: `.builder/domains.yaml`, GitHub OAuth,
  the persona/stack catalog, and budget ceilings + spend views.
- **[Architecture decision records](docs/adr/README.md)** — the "why" behind
  Temporal, the LLM-agnostic architect, sandbox seams, personas, and no-RAG.
- **[LLM shakedown notes](docs/llm-shakedown.md)** — empirical notes from
  exercising the architect/dev loop against real repos.

## Quickstart

```bash
# Install
uv sync --extra dev

# Bring up Postgres + Temporal locally
docker compose up -d

# One-time: register pravi's custom Temporal search attributes
./scripts/setup-temporal.sh

# Apply DB migrations
uv run alembic upgrade head

# Config — copy the example and fill in what you need
cp .env.example .env
```

**Authentication for Claude** — either `ANTHROPIC_API_KEY` in `.env` (or the
shell), or be logged in via `claude` (the SDK falls back to your Pro/Max
session). See `src/pravi/config.py` for the resolution order.

**GitHub (optional but recommended)** — register an OAuth App at
<https://github.com/settings/developers> with callback
`http://localhost:8765/api/auth/github/callback`, then set
`PRAVI_GITHUB_OAUTH_CLIENT_ID` + `PRAVI_GITHUB_OAUTH_CLIENT_SECRET` in `.env`.
This unlocks repo search, issue browsing/import, and auto-PR.

Then run the three processes:

```bash
# T1 — features worker (orchestration + cheap git/github activities)
uv run python -m pravi.worker --queue features

# T2 — LLM worker (caps concurrent dev-agent runs to bound $$$)
uv run python -m pravi.worker --queue llm --max-activities 2

# T3 — web API + UI
uv run pravi web --port 8765           # serves web/dist if you've built it
# …or for frontend hot-reload:
cd web && npm install && npm run dev    # Vite at :5173, proxies /api → :8765
```

Open <http://localhost:8765> (or <http://localhost:5173> in dev). From there:
connect GitHub → create an epic (or import a GitHub issue) → answer clarifying
questions → decompose → open a task → draft + approve its plan → watch the dev
agent build it and open a PR. Watch workflows in the Temporal UI at
<http://localhost:8233> (filter by `RepoName`, `Domain`, `TicketId`,
`PraviStatus`).

## CLI

The web UI is the primary surface, but the CLI covers scripted runs:

```bash
# Smoke test the worktree machinery (no LLM)
uv run pravi ticket run --fake \
  --repo /path/to/repo --domains-file ./examples/blissful-infra-domains.yaml \
  --domain shared --base-ref dev \
  --smoke-command "git rev-parse --abbrev-ref HEAD"

# Run the dev agent against a task, keep the worktree to inspect
uv run pravi dev "Create packages/shared/HELLO.md with one line 'hi'" \
  --repo /path/to/repo --domains-file ./examples/blissful-infra-domains.yaml \
  --domain shared --base-ref dev          # add --cleanup to tear down after

# Start a ticket workflow (blocks waiting for an approved plan)
uv run pravi ticket start TEST-001 \
  --title "Add a greeting README to shared" \
  --body  "Create packages/shared/HELLO.md with a one-line greeting." \
  --repo /path/to/repo --domain shared --base-ref dev \
  --domains-file ./examples/blissful-infra-domains.yaml --detach

# Draft + approve a plan from the terminal ($EDITOR fallback for the web UI)
uv run pravi plan TEST-001 --no-editor \
  --repo /path/to/repo --domains-file ./examples/blissful-infra-domains.yaml

uv run pravi ticket list-domains --repo /path/to/repo   # inspect a repo's domains
```

Per-run hard limits (wall clock, turns, $ budget) for the dev agent come from
`PRAVI_DEV_MAX_*`; architect budgets + per-mode model overrides from
`PRAVI_ARCHITECT_*`. See `.env.example` and `src/pravi/config.py`.

## Lifecycle (task)

1. `ticket start` (or creating a task in the UI) writes `Repo`+`Ticket` rows and
   launches `FeatureWorkflow`, which blocks on
   `workflow.wait_condition(plan_id is not None)`.
2. The **architect agent** (Claude, read-only — `Read`/`Grep`/`Glob`/`WebFetch`)
   drafts a structured Markdown plan (Summary / Approach / Changes / Tests /
   Risks). This runs as a backgrounded, persisted draft; the UI polls it.
3. On approve: writes a `Plan` row, sends the `approve_plan(plan_id)` signal.
4. The workflow wakes, creates a worktree, runs the dev agent on the LLM queue,
   advances status `planning → plan_approved → in_progress`, then pushes the
   branch and opens a draft PR (`pr_open`) when commits exist + GitHub is
   connected.

Epics and features are organizational containers (no workflow runs); their
tasks each boot a `FeatureWorkflow` lazily when you start them.

**Failure classifications.** When a dev run terminates unsuccessfully,
`_classify_failure` in `src/pravi/activities/dev_activity.py` tags it with a
stable reason code so the UI can render a remediation hint instead of a raw
stack trace:

- `quota_exhausted` — Anthropic rate limit / Max-plan quota hit.
- `wall_timeout` — exceeded `PRAVI_DEV_MAX_WALL_SECONDS`.
- `budget_exhausted` — cost ceiling tripped, either pre-flight (no tokens
  spent) or mid-run; the `Run` row is stamped with `RunStatus.budget_exhausted`.
- `max_turns_exhausted` — hit the SDK's `max_turns` cap.
- `sdk_error` — anything else thrown by claude-agent-sdk.
- `unknown` — failure with no error payload to read.

## Temporal organization

- **Two task queues**: `pravi-features` (orchestration + cheap git/github
  activities) and `pravi-llm` (token-burning activities, capped concurrency via
  `--max-activities`).
- **Workflow IDs**: `feature-<repo-slug>-<ticket-id>`. Uses
  `ALLOW_DUPLICATE_FAILED_ONLY` reuse policy — re-running a ticket only succeeds
  if the prior run failed.
- **Search attributes**: `RepoName`, `Domain`, `TicketId`, `PraviStatus` on every
  workflow for UI filtering. Registered by `./scripts/setup-temporal.sh`.

## Layout

```
src/pravi/
├── cli/         # Typer: ticket run/start/list-domains, dev, plan, web
├── api/         # FastAPI app (REST + SSE): routes, auth_routes (GitHub OAuth), schemas
├── workflows/   # Temporal: SmokeWorkflow, DevWorkflow, FeatureWorkflow
├── activities/  # git, dev (claude-agent-sdk), db (ticket/plan I/O), pr (push + open PR)
├── agents/
│   ├── protocols.py     # Architect + DevAgent Protocols, shared dataclasses
│   ├── factory.py       # get_architect() / get_dev_agent() provider dispatch
│   ├── architects/      # claude.py, litellm.py, context.py, clarify/decompose parsers
│   └── dev/             # claude.py (the only dev-agent impl)
├── services/    # background jobs: clarification, agent_draft (decompose+plan), github
├── budget/      # cost rollup + ceiling resolution across the hierarchy
├── sdk_runner/  # claude-agent-sdk wrapper with heartbeats + budget guardrails
├── domains/     # `.builder/domains.yaml` loader
├── prompts/     # versioned prompts: architect, clarify, decompose, developer
├── db/          # SQLAlchemy models + Alembic migrations
└── config.py

web/             # Vite + React + TS UI
├── src/pages/       # HomePage, NewTicketPage, IssuesPage, TicketPlanPage, RunsPage
├── src/components/  # DecomposePanel, RoadmapView, DependencyEditor, PlanEditor,
│                    #   TicketInfoCard, BudgetMeter, GitHubConnectButton, LiveRunPanel
└── src/lib/         # api.ts (REST + SSE), progressMarkers, useHomeViewState, useIssuesViewState
```
