# pravi-builder-agent

Agentic feature builder for domain-driven repos, powered by Claude.

**Status:** Slice 1B — architect drafts plans (read-only Claude), human approves via `$EDITOR`, FeatureWorkflow signal/wait pattern wires plan → dev. Tickets + plans persist in Postgres.

## What it is

`pravi` lets you pick up GitHub issues, have a Claude agent plan + develop them inside a
per-ticket git worktree, run domain tests, and open a draft PR for human review.

- **Humans gate** the *plan* and the *final PR merge*.
- **Agents are autonomous** for develop, review, test, and analyze.
- **Domain-driven**: each developer agent is scoped to a single domain declared in the
  target repo's `.builder/domains.yaml`.

See `/Users/cavanpage/.claude/plans/ok-i-just-initialized-zesty-fountain.md` for the
full implementation plan.

## Stack

- Python 3.11+, [uv](https://docs.astral.sh/uv/)
- [Temporal](https://temporal.io/) for durable workflow orchestration
- [LangGraph](https://langchain-ai.github.io/langgraph/) (later slices) for agent state
- [claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python) (later slices)
  as the developer's filesystem-mutating executor
- Postgres for app state (SQLAlchemy 2.x async + Alembic)
- Typer CLI (`pravi`)

## Quickstart (Slice 0)

```bash
# Install
uv sync --extra dev

# Bring up Postgres + Temporal locally
docker compose up -d

# One-time: register pravi's custom Temporal search attributes
./scripts/setup-temporal.sh

# Apply DB migrations
uv run alembic upgrade head

# Authentication for Claude: either ANTHROPIC_API_KEY in .env,
# or be logged in via Claude Code (the SDK shells out to the local `claude` CLI).

# In one terminal: run the features worker (workflow orchestration + git/github)
uv run python -m pravi.worker --queue features

# In another: run the LLM worker (caps concurrent dev-agent runs at $$$)
uv run python -m pravi.worker --queue llm --max-activities 2

# In a third: kick a smoke workflow against blissful-infra
cp .env.example .env
uv run pravi ticket run --fake \
  --repo /Users/cavanpage/repos/blissful-infra \
  --domains-file ./examples/blissful-infra-domains.yaml \
  --domain shared --base-ref dev \
  --smoke-command "git rev-parse --abbrev-ref HEAD"
```

This creates a worktree of blissful-infra at `~/.pravi/worktrees/<ticket-id>`,
runs the smoke command inside it, and tears the worktree down (the branch is
also removed for `--fake` runs). Open the Temporal UI at <http://localhost:8233>
to watch. You can also filter workflows in the UI by `RepoName`, `Domain`,
`TicketId`, or `PraviStatus`.

## Run the dev agent (Slice 1A)

`pravi dev` spins up a worktree, runs the claude-agent-sdk developer agent
against the requested task, and keeps the worktree for you to inspect:

```bash
uv run pravi dev "Create a file packages/shared/HELLO.md with one line 'hi'" \
  --repo /Users/cavanpage/repos/blissful-infra \
  --domains-file ./examples/blissful-infra-domains.yaml \
  --domain shared --base-ref dev
# prints: ✓ success  turns=2  duration=4.7s  cost=$0.13
# worktree preserved: ~/.pravi/worktrees/dev-<uuid>
#   inspect with: cd ... && git diff dev

# add --cleanup to tear down worktree + branch when done
uv run pravi dev "..." --cleanup ...
```

The dev activity runs on the **LLM queue** so its concurrency is capped by
`--max-activities` on the LLM worker. Per-run hard limits (wall clock, turns,
$ budget) come from `PRAVI_DEV_MAX_*` env vars — see `.env.example`.

## Full ticket lifecycle (Slice 1B)

Two commands, intentionally separated so the workflow visibly pauses for
human approval:

```bash
# T1 — start a ticket; workflow blocks waiting for an approved plan
uv run pravi ticket start TEST-001 \
  --title "Add a greeting README to shared" \
  --body "Create packages/shared/HELLO.md with a one-line greeting." \
  --repo /Users/cavanpage/repos/blissful-infra \
  --domain shared --base-ref dev \
  --domains-file ./examples/blissful-infra-domains.yaml \
  --detach    # exit; workflow stays running in the worker

# T2 — architect drafts a plan, opens $EDITOR, approve/revise/cancel,
#       persists Plan row, signals the workflow with plan_id
uv run pravi plan TEST-001 \
  --repo /Users/cavanpage/repos/blissful-infra \
  --domains-file ./examples/blissful-infra-domains.yaml
# or skip the editor for scripted runs:
uv run pravi plan TEST-001 --no-editor ...
```

What happens:
1. `ticket start` creates `Repo`+`Ticket` rows and launches
   `FeatureWorkflow`, which immediately blocks on
   `workflow.wait_condition(plan_id is not None)`.
2. `pravi plan` runs the **architect agent** (Claude, read-only — only
   `Read`/`Grep`/`Glob`/`WebFetch`), drafts a structured Markdown plan with
   Summary / Approach / Changes / Tests / Risks, opens it in `$EDITOR`,
   prompts to approve/revise/cancel.
3. On approve: writes `Plan` row, sends `approve_plan(plan_id)` signal.
4. Workflow wakes, loads the plan, creates a worktree, runs the dev agent
   on the LLM queue with the plan as the task, updates ticket status
   through `planning → plan_approved → in_progress → pr_open`.

Watch any of it via the workflow's `@workflow.query current_status()` —
the CLI tails it when run without `--detach`.

## Temporal organization

- **Two task queues**: `pravi-features` (orchestration + cheap git/github
  activities) and `pravi-llm` (token-burning activities, capped concurrency).
  The dev activity (Slice 1+) routes to `pravi-llm`.
- **Workflow IDs**: `feature-<repo-slug>-<ticket-id>` (e.g.
  `feature-blissful-infra-42`). Uses `ALLOW_DUPLICATE_FAILED_ONLY` reuse
  policy — re-running a ticket only succeeds if the prior run failed.
- **Search attributes**: `RepoName`, `Domain`, `TicketId`, `PraviStatus` are
  attached to every workflow for UI filtering. Registered by
  `./scripts/setup-temporal.sh`.

## Layout

```
src/pravi/
├── cli/         # Typer: ticket run/start/list-domains, plan, dev
├── workflows/   # Temporal: SmokeWorkflow, DevWorkflow, FeatureWorkflow
├── activities/  # git, dev (claude-agent-sdk), db (ticket/plan I/O)
├── sdk_runner/  # claude-agent-sdk wrapper with heartbeats + budget guardrails
├── agents/      # architect (read-only Claude call); reviewer in Slice 2
├── domains/     # `.builder/domains.yaml` loader
├── prompts/     # versioned prompts: dev/v1, architect/v1
├── events/      # (Slice 2+) typed event bus
├── db/          # SQLAlchemy models + Alembic
├── tools/       # (Slice 2+) shared LangGraph tools
└── config.py
```
