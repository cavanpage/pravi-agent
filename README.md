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
uv sync

# Bring up Postgres + Temporal locally
docker compose up -d

# Apply DB migrations
uv run alembic revision --autogenerate -m "initial"
uv run alembic upgrade head

# In one terminal: run the Temporal worker
uv run python -m pravi.worker

# In another: kick a smoke workflow against blissful-infra
cp .env.example .env  # edit PRAVI_TARGET_REPOS if needed
uv run pravi ticket run --fake \
  --repo /Users/cavanpage/repos/blissful-infra \
  --domains-file ./examples/blissful-infra-domains.yaml \
  --domain shared
```

This creates a worktree of blissful-infra at `~/.pravi/worktrees/<ticket-id>`,
runs `npm run test -w packages/shared` inside it, and tears the worktree down.
Open the Temporal UI at <http://localhost:8233> to watch.

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
