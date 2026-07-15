# LLM shakedown playbook

A one-shot validation pass that exercises the full plan → build → PR
loop with real Claude calls. Run this when you're ready to burn some
tokens to prove the end-to-end wiring works.

The cheap-end (infra + workers + new endpoints + smoke workflow)
already passed — see the prior session's notes. This doc covers what
still needs human-in-the-loop validation.

## Cost summary

Costs go to whichever auth mode you have set:

- **`ANTHROPIC_API_KEY` set** → real dollars on console.anthropic.com.
- **No key, `claude login` Pro/Max session** → quota burn, no charge.
  This is the current default per `apply_anthropic_auth()` logs.

Rough cost expectations *per ticket walked through the loop*:

| Step | Model (default) | Typical cost |
|---|---|---|
| Clarify | Haiku 4.5 | $0.01 – $0.05 |
| Decompose | Opus (default) | $0.20 – $0.80 |
| Plan draft (per task) | Opus (default) | $0.10 – $0.40 |
| Dev agent run (per task) | Opus | $0.50 – $3.00 |

Total for one tiny epic through one task to PR: **~$1 – $4**. Bigger
tasks scale up.

## Pre-flight

Should already be true from the cheap shakedown — verify quickly:

```bash
docker compose ps           # all 4 containers healthy
uv run alembic current      # at head (c4e2b8d6f1a9 or later)
curl -s http://localhost:8765/healthz   # {"status":"ok"}
curl -s http://localhost:8765/api/auth/github/me   # not null → still connected
```

If anything's off, see the README quickstart.

## Phase 1 — Browser UI smoke (free)

Catches UI wiring without spending anything. Open
<http://localhost:8765/> (or `:5173` if running Vite dev).

- [ ] **Home** — see the search/kind/sort toolbar, "issues" + "runs" + "+ epic" + "+ task" buttons in the header.
- [ ] **Persona spend card** — should be *suppressed* on a fresh install (no spend yet → nothing to show). Don't expect to see it yet.
- [ ] **`/new?kind=epic`** — GitHub repo picker shows (no local-path inputs). Persona dropdown shows all 19 entries; coming-soon ones disabled with "— coming soon" suffix. Stack dropdown shows 10 stacks.
- [ ] **`/issues`** — repo dropdown populated from your GH account. Filter chips (open / closed / all) work. Convert modal opens for any issue.
- [ ] **GitHub Connect chip** in the header — shows `@cavanpage` + avatar; dropdown lets you disconnect.

Anything broken at this stage → fix before spending tokens.

## Phase 2 — Create one tiny epic against pravi-agent itself

We dogfood. `pravi-agent` now has its own `.builder/domains.yaml` with
two domains: `backend` + `web`.

Suggested starter epic — small enough that one task is enough:

> **Title**: Add a structured log line when LocalWorktreeSandbox provisions a new worktree
>
> **Body**:
> Today `LocalWorktreeSandbox.provision()` logs `sandbox.local.worktree_created` only when it creates a fresh worktree. It logs `sandbox.local.worktree_exists` when reusing an existing one. Both already exist — good.
>
> What's missing is a structured-log line at the *start* of provision, before either branch, recording `repo_id`, `ticket_external_id`, and `branch`. This makes it possible to trace "agent X tried to provision Y" in `pravi-postgres` logs without needing the whole transcript.
>
> Add the log line. Match the structlog idiom used elsewhere in `src/pravi/agents/sandbox/local.py`. No new tests needed — this is an observability change.

(Or pick a real issue from `/issues` and convert it.)

- [ ] Create the epic. Watch the clarify activity feed pop up live (Haiku response — should be fast, 5-15s).
- [ ] Answer 1-2 questions (or skip). Verify the multi-choice radios render if the architect offered any.
- [ ] Click "draft decomposition". Watch the progress feed — should show `reading` / `searching` lines.
- [ ] Verify the decomposed feature/task tree includes `persona:` and `stack:` fields. The architect should pick `backend` for sandbox code, `python-stdlib` or `python-fastapi` for stack.

**What to verify post-decompose:**
- [ ] In Postgres: `SELECT external_id, kind, persona, stack FROM tickets WHERE created_at > now() - interval '5 minutes';` — feature + task rows have persona/stack populated.
- [ ] On the home page: ticket rows show the `PersonaChip` next to the title.

## Phase 3 — Draft + approve a plan

Open the task that was created. Click "draft plan with architect".

- [ ] Activity feed shows live tool-use during drafting (the streamed `raw_md` partial).
- [ ] Plan renders in the editor when done. Includes Summary / Approach / Changes / Tests / Risks sections.
- [ ] Approve. Workflow status flips to `plan_approved` → `in_progress`.

## Phase 4 — Dev agent run (the big one)

This is the biggest single cost step. Watch:

- [ ] **`LiveRunPanel`** shows tool-use events as the dev agent works.
- [ ] Worktree appears at `~/.pravi/worktrees/<external-id>/` (the sandbox seam in action).
- [ ] Once the agent commits, the **push + open PR** activity runs.
- [ ] Ticket page shows the new `⤴ PR #N opened on GitHub` chip; status flips to `pr_open`.
- [ ] GitHub side: the issue is annotated with a `Tracked as pravi task t-xxx` comment (if imported from /issues) and the `pravi-imported` label is added. The new branch shows in the GH repo's branch list.

## Phase 5 — Verify the FinOps widget populates

```bash
curl -s "http://localhost:8765/api/spend/by-persona?window=all" | python3 -m json.tool
```

- [ ] Returns a non-empty array. Should see one row per persona that had a run.
- [ ] Reload the home page — the `PersonaSpendCard` is no longer suppressed; shows the stacked bar + per-row table.
- [ ] Window chips (7d / 30d / All-time) re-query and reflect the right bucket.

## Phase 6 — Cleanup

- [ ] Close / merge / delete the test PR on GitHub.
- [ ] Either keep the test ticket as a real example or bulk-delete it from the home page.
- [ ] Worktree under `~/.pravi/worktrees/` can be pruned if `cleanup_worktree=False` was used (the default keeps for inspection).

## What this run validates

| ADR | What this run proves |
|---|---|
| 0001 (Temporal, no LangGraph) | `FeatureWorkflow` blocks on plan signal, picks back up, dev activity runs on LLM queue |
| 0002 (LLM-agnostic architect) | `architect_clarify_model=claude-haiku-4-5` is used for clarify; Opus for decompose/dev |
| 0003 (Sandbox seam) | `LocalWorktreeSandbox` provisions; `push_branch` works; cleanup runs |
| 0004 (Personas + stacks) | Architect assigns `persona`/`stack` in decompose YAML; dev agent gets the persona-specific system prompt modifier; spend lands grouped by persona |
| 0005 (No vector RAG) | The architect navigates the repo via `Read`/`Grep`/`Glob` — no embeddings, no index, just on-demand reads |

## Likely failure modes (notes for fixing)

| Symptom | Probable cause | Fix |
|---|---|---|
| Clarify hangs > 60s | Haiku slow / network — should be fast. Watch worker logs. | Restart LLM worker; check `claude login` session valid. |
| Decompose returns YAML with no persona/stack | Architect ignored the new prompt fields | Check `prompts/decompose.py::VERSION` is `architect/decompose/v2`; check YAML schema actually advertises the fields. |
| Dev agent runs but no commit | The task instructions said something the agent didn't act on | Look at `LiveRunPanel` transcript — was it told to commit? `prompts/developer.py` should include the commit instruction. |
| PR step skipped | No commits OR no GitHub connection | Check `pr_activity.PushAndOpenPRResult.skipped_reason` in logs. |
| Spend widget stays $0 | `Event.payload.total_cost_usd` not populated for the run | Check `run_finished` event payload — SDK should always provide cost when run completes. |
| `PersonaChip` doesn't render | Persona on the ticket is null AND stack is null | Expected — chip suppresses itself when both are null. |

## Out of scope for this shakedown

- Per-stack spend widget (endpoint exists at `/api/spend/by-stack`, no UI yet)
  — *since shipped as `StackSpendCard` on the home dashboard*
- Edit-persona-in-place on TicketPlanPage (currently set-once on /new)
- Auto-`domains.yaml` proposal at repo-connect time
- Promoting a coming_soon persona to active
