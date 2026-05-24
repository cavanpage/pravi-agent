# pravi-builder-agent

Agentic feature builder for domain-driven repos, powered by Claude.

**Status:** Slice 0 — end-to-end skeleton (worktree create / smoke command / cleanup over Temporal).

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

# In one terminal: run the features worker (default queue)
uv run python -m pravi.worker
# OR run an LLM-pool worker with a hard concurrency cap (Slice 1+):
#   uv run python -m pravi.worker --queue llm --max-activities 4

# In another: kick a smoke workflow against blissful-infra
cp .env.example .env
uv run pravi ticket run --fake \
  --repo /Users/cavanpage/repos/blissful-infra \
  --domains-file ./examples/blissful-infra-domains.yaml \
  --domain shared --base-ref dev
```

This creates a worktree of blissful-infra at `~/.pravi/worktrees/<ticket-id>`,
runs the domain's test command inside it, and tears the worktree down. Open
the Temporal UI at <http://localhost:8233> to watch. You can also filter
workflows in the UI by `RepoName`, `Domain`, `TicketId`, or `PraviStatus`.

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
├── cli/         # Typer entrypoint (`pravi`)
├── workflows/   # Temporal workflows (deterministic)
├── activities/  # Temporal activities (I/O, subprocesses)
├── sdk_runner/  # (Slice 1+) claude-agent-sdk wrapper
├── agents/      # (Slice 1+) LangGraph graphs per role
├── domains/     # `.builder/domains.yaml` loader
├── prompts/     # (Slice 1+) versioned prompts
├── events/      # (Slice 2+) typed event bus
├── db/          # SQLAlchemy models + Alembic
├── tools/       # (Slice 1+) shared LangGraph tools
└── config.py
```
