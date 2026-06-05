# Contributing to Pravi

Thanks for hacking on Pravi. This guide gets a new contributor from a fresh
clone to a green PR. For *what* Pravi is, see [README.md](README.md); for *why*
the major pieces are shaped the way they are, see [docs/adr/](docs/adr/).

> **Persona writing about itself:** the dev agent that ships PRs here is the
> same kind of agent Pravi builds. When something in this guide feels
> over-engineered for a side project, that's usually because the agent needs
> the structure (durable workflows, scoped worktrees, budget gates) more than
> a human does.

---

## Prerequisites

You need **three** things on your machine. Versions below are what we test
against; older usually works, newer almost always does.

| Tool                 | Why                                                  | Install                                         |
| -------------------- | ---------------------------------------------------- | ----------------------------------------------- |
| **Python ≥ 3.11**    | Backend runtime (FastAPI, Temporal SDK, SQLAlchemy). | `pyenv install 3.11` or system package.         |
| **[uv]**             | Sole dependency + virtualenv manager. No `pip` here. | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Docker + Compose** | Runs Postgres and Temporal locally.                  | Docker Desktop, OrbStack, or `colima`.          |
| **Node ≥ 20 + npm**  | Only if you'll touch the React UI in `web/`.         | `nvm install 20`.                               |

[uv]: https://docs.astral.sh/uv/

You'll also want an **`ANTHROPIC_API_KEY`** (or be logged in via `claude`) if
you intend to actually run the architect or dev agent end-to-end. Plenty of
backend work doesn't need one — the workflow tests use the `--fake` dev agent.

---

## First-time setup

```bash
# 1. Install Python deps into .venv (includes ruff, mypy, pytest).
uv sync --extra dev

# 2. Bring up Postgres + Temporal + the Temporal UI.
docker compose up -d

# 3. One-time: register Pravi's custom Temporal search attributes.
./scripts/setup-temporal.sh

# 4. Apply DB migrations.
uv run alembic upgrade head

# 5. Copy the env template and fill in what you need.
cp .env.example .env
```

After step 2 you should have:

- **Postgres** on `localhost:5433` (note the non-default port — avoids clashing
  with a system Postgres).
- **Temporal** on `localhost:7233`.
- **Temporal Web UI** on <http://localhost:8233>.

`docker compose ps` should show four healthy containers
(`pravi-postgres`, `pravi-temporal`, `pravi-temporal-postgres`,
`pravi-temporal-ui`).

---

## The dev loop

Pravi runs as **three long-lived processes** plus the docker stack. They talk
to each other via Temporal — not HTTP — so the order they start in doesn't
matter, and any one of them can be restarted independently.

| Process           | What it does                                                                                                                                  | Command                                                              |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `features` worker | Runs workflows + cheap activities (git, GitHub API, DB writes). Listens on the `pravi-features` task queue.                                   | `uv run python -m pravi.worker --queue features`                     |
| `llm` worker      | Runs the token-burning activities — architect drafts, dev agent runs. Concurrency is capped (`--max-activities 2`) so an epic can't bankrupt you. | `uv run python -m pravi.worker --queue llm --max-activities 2`       |
| `web`             | FastAPI app: REST + SSE + the built React UI (served out of `web/dist/`).                                                                     | `uv run pravi web --port 8765`                                       |

**The two-queue split is load-bearing.** Workflows themselves are cheap and
their activities are mostly git/DB calls — the `features` worker can handle
those at high concurrency. The dev agent is expensive and slow; pinning it to
its own worker with `--max-activities` is how we bound parallel spend and
prevent one runaway plan from starving everything else.

### One-command launcher

`scripts/dev.sh` automates the steps above end-to-end: it stops stale
processes, brings up docker, runs migrations, registers search attributes,
builds the React app, then starts the three processes in the background and
tails their combined logs. Ctrl-C tears it all down.

```bash
./scripts/dev.sh                # default: clean + build + run
./scripts/dev.sh --no-build     # skip the React build (faster restart)
./scripts/dev.sh --vite         # also start Vite hot-reload on :5173
./scripts/dev.sh --reset-db     # drop docker volumes (DANGER)
./scripts/dev.sh --help
```

To stop the workers/API without touching docker:

```bash
./scripts/stop.sh
```

Use plain `docker compose stop` (or `down`) to stop Postgres + Temporal.

### Frontend hot-reload

If you're iterating on the UI, run Vite alongside the API:

```bash
cd web && npm install            # first time
cd web && npm run dev            # serves :5173, proxies /api → :8765
```

Open <http://localhost:5173>. The API still has to be running for anything
data-driven to load.

---

## Code quality gates

Three checks run on every PR. Run them locally before pushing — CI is not
free, and the dev agent reads these same exit codes to decide whether its work
is shippable.

```bash
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
uv run mypy src                  # type-check (strict mode)
uv run pytest                    # tests (asyncio_mode = auto)
```

Notes:

- **ruff** is configured in `pyproject.toml`: line-length 100, rule set
  `E,F,I,B,UP,W`, `E501` ignored, Alembic versions excluded. Run
  `uv run ruff check . --fix` to auto-fix the easy stuff.
- **mypy** runs in `strict` mode with the pydantic plugin. If you hit a
  missing-stub error in a third-party dep, prefer adding `types-<pkg>` to the
  `dev` extra over `# type: ignore`.
- **pytest** picks up everything under `tests/`. Async tests don't need an
  explicit decorator — `asyncio_mode = "auto"`. Tests that need Postgres assume
  the docker stack is up; nothing requires Temporal because workflow tests use
  the in-memory test environment.

---

## Database migrations

App schema lives under `src/pravi/db/`:

- `models.py` — SQLAlchemy 2.x models (async).
- `migrations/versions/` — Alembic revisions, one per schema change.

`alembic.ini` lives at the repo root and is already wired up — you just
generate revisions and apply them.

### Adding a migration

1. Edit `src/pravi/db/models.py` (add a column, table, index, etc.).

2. Autogenerate a revision:

   ```bash
   uv run alembic revision --autogenerate -m "short_snake_case_summary"
   ```

   This drops a new file in `src/pravi/db/migrations/versions/` named
   `<hash>_<your_summary>.py`. Alembic only sees changes against the *current*
   schema in your local DB, so make sure `alembic upgrade head` ran before you
   autogenerate.

3. **Read the generated file.** Autogenerate misses things — renames look like
   drop+add, server-side defaults aren't always picked up, enum changes need
   manual `op.execute(...)`. Fix it up and add a short docstring at the top
   explaining *why* the change is needed.

4. Apply it:

   ```bash
   uv run alembic upgrade head
   ```

5. Sanity-check the round-trip by downgrading and re-applying:

   ```bash
   uv run alembic downgrade -1 && uv run alembic upgrade head
   ```

   If `downgrade` doesn't cleanly reverse, fix the `downgrade()` function —
   even if we never run it in prod, it's the quickest way to catch a bad
   autogen.

6. Commit the model change **and** the new revision file together. Never edit
   a migration that's been merged to `main`; write a new one on top.

---

## PR checklist

Before you mark a PR ready for review, walk this list:

- [ ] Branch is rebased on the latest `main` (or merge cleanly conflicts-free).
- [ ] `uv run ruff check .` is clean.
- [ ] `uv run ruff format --check .` is clean.
- [ ] `uv run mypy src` is clean.
- [ ] `uv run pytest` is green locally.
- [ ] New code has tests, or the PR description says why it doesn't.
- [ ] If the schema changed: a new Alembic revision is committed alongside the
      `models.py` change, and `alembic upgrade head` succeeds from a fresh DB.
- [ ] If behaviour changed: `README.md`, `.env.example`, or `docs/adr/` are
      updated to match.
- [ ] If a new env var was added: it's in `.env.example` with a comment, and
      it's loaded via `src/pravi/config.py`.
- [ ] No secrets or local paths leaked into the diff (check `.env`, log
      output, fixture data).
- [ ] PR description explains *why*, not just *what* — the diff already shows
      what.

Drafts opened automatically by the dev agent will satisfy most of the
mechanical items above; humans gate the *why* and the final merge.

---

## Where to look when something breaks

- **Worker won't start** — check `.pravi/logs/features-worker.log` and
  `.pravi/logs/llm-worker.log` (created by `scripts/dev.sh`).
- **Workflow stuck** — open <http://localhost:8233>, filter by `TicketId` or
  `PraviStatus`, inspect the event history.
- **Alembic complains about an unknown revision** — your DB is ahead of the
  branch. Either `alembic downgrade <rev>` or, for local-only data,
  `./scripts/dev.sh --reset-db`.
- **Agent runs but bills nothing / does nothing** — confirm `ANTHROPIC_API_KEY`
  is exported (or `claude` is logged in) and that the `llm` worker is running.
