# ADR 0007 — Ephemeral previews and the e2e repair loop

- **Status:** Accepted
- **Date:** 2026-07-28
- **Deciders:** @cavanpage

## Context

ADR 0006 got pravi to "live on `main`": a new repo from a template gets a
Cloudflare Pages project, and every push to the production branch deploys.
The per-ticket loop, though, still ends at **"PR opened"** — nothing ever
checks that what the agent built actually works in a browser.

Three facts made that gap cheap to close:

- **The ephemeral environment already existed and was unused.** The Pages
  projects pravi creates are git-connected with `deployments_enabled: true`
  (`services/cloudflare.py`), so every pushed branch already got a free,
  auto-expiring preview deployment. We simply never looked at it.
- **`RunKind.tester` had existed since the initial schema** (`db/models.py`)
  and had never been written. The slot for "a verification run happened"
  was there; nothing filled it.
- **Acceptance criteria appeared nowhere.** `grep acceptance` across the
  codebase hit exactly one line — the `tester` persona's description in
  `personas/catalog.py`, which *assumes* acceptance criteria exist. Nothing
  produced them, and the dev prompt explicitly said "do not run tests
  yourself — a separate test step will validate your work" for a test step
  that didn't exist.

So the failure signal a repair loop would need — machine-readable, tied to
a specific commit, produced by exercising the real deployment — was
missing, and everything required to produce it was already paid for.

## Decision

**Extend the per-ticket loop from "opens a PR" to "deploys the branch to an
ephemeral preview, verifies acceptance criteria against it in a real
browser, and lets the dev agent fix what it broke — bounded."**

Five parts:

### 1. The ephemeral environment is a Pages *preview deployment* per branch

On the repo's existing git-connected project. No project-per-ticket: no
name collisions, no subdomain sprawl, no teardown to get wrong, no new
token scope. Cloudflare's own `pr_comments_enabled` posts the preview URL
onto the PR for free.

Verification targets the **per-commit atomic URL** (`<hash>.<project>.pages.dev`),
not the branch alias. The alias always points at the latest build for a
branch and races when two branches sanitize to the same label; the
per-commit URL is unambiguously the build of the SHA we just pushed.
Deployments are matched by `commit_hash`, with a newest-on-this-branch
fallback recorded as a weaker `matched_by="branch"`.

### 2. Acceptance criteria are natural language in the spec; the *dev* agent writes the Playwright specs

They ride the path that already exists — decompose YAML `acceptance:` →
ticket body → `Plan.content_md` → dev prompt — so they need **no new
column, no new table, and no new agent**. `src/pravi/specs/acceptance.py`
renders them into a `## Acceptance criteria` section and parses them back
out.

That parser is also the feature's back-compat gate: every ticket written
before this ADR lacks the heading, yields `[]`, and takes the pre-0007 path
exactly. No migration, no backfill, no per-ticket flag. The dev system
prompt is byte-identical to `dev/v2` when there are no criteria, which is
enforced by a test.

The committed `e2e/*.spec.ts` files are the durable artifact — they outlive
the run and the PR.

### 3. Verification is a bounded loop in the workflow, not inside the agent

Each iteration: push → await preview → run e2e → feed structured failures
into a fresh `run_dev`. Temporal owns durability and the attempt cap; the
agent owns the fix. Five give-up conditions, all of which still leave a
reviewable PR:

| condition | verdict | why not repair |
|---|---|---|
| suite green | `passed` | — |
| attempt cap hit | `failing` | out of budget by construction |
| repair produced no new commit | `failing` | next deploy+test would be byte-identical |
| build never appeared / timed out | `timed_out` | Cloudflare config or slowness — not the agent's code |
| `stop_reason == budget_exhausted` | (as-is) | the cost ceiling already bound |

A Cloudflare build that ran *and failed* IS repairable (usually a
TypeScript error), so its logs get fed back.

**The PR opens after the first successful push, as a draft while e2e is
unproven.** The branch is already public by then — Cloudflare can't build
it otherwise — so the PR is free, repair commits update it automatically,
and a give-up leaves a diff plus a live preview URL instead of today's bare
`in_progress`.

### 4. The build wait is a workflow timer loop, not a long heartbeating activity

`poll_preview_deployment` is one API call; `workflow.sleep` between polls,
with a pure backoff function of the poll counter. Determinism isn't the
discriminator (both replay fine) — **failure behavior** is. A 15-minute
heartbeating activity whose worker dies at minute 12 fails on heartbeat
timeout and restarts from zero. A timer loop resumes at the next poll with
every prior result already in workflow history. It also makes each poll a
discrete event in the Temporal UI, cancels instantly, and doesn't pin a
worker slot for 15 minutes.

### 5. The sandbox seam grows `exec` + `head_sha`

Second deliberate extension of ADR 0003's Protocol (after `push_branch`),
driven by a concrete requirement — running `npm` and `npx playwright`
wherever the code physically lives — not speculation. The workflow still
passes a `SandboxHandle` and an argv, never a path.

The orphaned host-exec `git_activity.run_command` is **deprecated in place,
not folded in**: it takes a bare `cwd` with no handle, which is exactly the
assumption ADR 0003 forbids.

### Production custom domains

`setup_custom_domain` registers the hostname on the Pages project and
points a proxied CNAME at it. Preview deployments stay on `*.pages.dev` —
per-branch custom hostnames need a wildcard-domain setup that isn't a clean
API operation.

Registering the domain needs only `Account → Pages → Edit`; writing DNS
additionally needs `Zone → Read` + `Zone → DNS → Edit`. Without those,
nothing raises: the domain is still registered (it sits `pending` until DNS
resolves) and the result carries the exact record to paste. A
degraded-but-actionable outcome beats a hard failure on an optional leg.

## Consequences

### Wins

- Closes the loop: idea → clarified → planned → built → **deployed →
  verified**, with failures that are machine-readable and actionable rather
  than discovered by a human later.
- Acceptance criteria stop being prose that evaporates at merge and become
  committed tests.
- A per-branch preview URL is independently useful for human review, loop
  or no loop.
- `Repo.cf_pages_project` finally persists — the create-repo flow computed
  it and threw it away, so "which project builds this repo?" was previously
  unanswerable at ticket time.
- `RunKind.tester` gets its first writer, and the existing `/runs` surfaces
  work with no changes. Tester runs carry no cost and emit no
  `run_finished`, so the budget rollup is unperturbed.

### Costs (acknowledged)

- **Cloudflare build latency (~1–3 min) now sits inside each ticket's wall
  clock**, and inside every repair iteration.
- **Worst-case LLM spend per ticket multiplies by `e2e_max_attempts`** —
  3 × `dev_max_cost_usd` = ~$15 by default. The existing `cost_ceiling_usd`
  machinery is the real cap (the loop treats `budget_exhausted` as a
  give-up), but the default is unlimited, so the docs push people to set
  one.
- Playwright browsers are ~150MB on first install; mitigated by a shared
  `PLAYWRIGHT_BROWSERS_PATH`, not eliminated. `--with-deps` is opt-in
  because on Linux it shells to `sudo apt-get` and hangs on a password
  prompt nothing can answer.
- `npm ci` per worktree means `node_modules` × concurrent tickets on disk.
- A flaky dependency can burn every repair attempt on a non-bug. The
  clearest case is Workers AI's free-tier 429; that's handled **in the
  `llm-chat` template's spec** (assert reply *or* quota banner), not in the
  framework — the framework can't know which failures are expected.
- Multiple pushes per ticket means noisier branch history.
- The sandbox seam now implies "can run arbitrary commands", raising the
  bar for any future remote backend.
- `/run/stream`'s close-on-first-`run_finished` semantics had to change (a
  loop produces several). Old clients see identical behavior only because
  the `terminal` payload flag defaults to true when absent.

## Alternatives considered

### A Pages project per ticket

Rejected: name collisions, subdomain sprawl, a teardown lifecycle to get
wrong, a stronger token scope, and it discards Cloudflare's native PR
integration. Preview deployments are the idiomatic ephemeral environment on
this platform and they expire on their own.

### Playwright locally against `vite preview` / `wrangler pages dev`

Rejected: it would not test the actual deployment — build config, Pages
Functions, the Workers AI binding, edge routing — and "it's live at a URL"
is the whole demo. Fast, but it verifies the wrong thing.

### A structured acceptance-criteria DSL (Gherkin, or a YAML step schema)

Rejected: brittle, and the model is better at prose→Playwright than at
DSL→Playwright. Prose also stays human-editable in the plan UI. The cost is
that a bad criterion produces a bad test, which is why the prompt pushes
hard on "omit the field for non-user-visible work" rather than filling it.

### A separate `tester` agent authoring the specs

Deferred: a second LLM run per ticket for work the dev agent is already
in-context for. `RunKind.tester` is used for the *execution* record; the
persona stays available for test-only tickets.

### A long heartbeating activity for the build wait

Rejected — see decision 4.

### GitHub Actions running Playwright

Rejected: moves orchestration out of Temporal, splits the failure signal
across two systems, and hands the agent CI logs it can't reach.

### New `TicketStatus` members for e2e outcomes

Rejected: the verdict is a separate axis from workflow status — a PR can be
open while its tests are red. An `e2e_failing` status would ripple into
`_derive_parent_status`, child rollups, roadmap waves, and the UI colour
maps, for information `tickets.e2e_verdict` carries more precisely.

## When to revisit

- **Extract a `PreviewTarget` Protocol** when a second preview provider
  lands — same discipline as ADR 0006's deferred `DeployTarget`.
- **Add a `deployments` table** when the UI needs deployment history across
  tickets, or when production deploys get tracked. Today
  `tickets.preview_url` + `e2e_verdict` + Event rows cover every consumer.
- **Move `run_e2e` to a dedicated `e2e` task queue** when it starves the
  `features` pool of cheap git/DB activities.
- **Add `read_file` to the sandbox seam** if Playwright's stdout JSON report
  ever outgrows the exec capture limit; the template would then switch to a
  file reporter. (A truncated report fails loudly today rather than
  misparsing as green.)
- **Introduce a dedicated tester agent** if dev-authored specs prove
  consistently low quality. Measure: how often a repair iteration changes a
  spec rather than the app.
- **Delete `git_activity.run_command`** when `SmokeWorkflow` goes.

## Related

- ADR [0003 — Sandbox seam](0003-sandbox-seam-no-local-mounts.md) — the
  Protocol extended here.
- ADR [0004 — Agent personas](0004-agent-personas.md) — the `tester`
  persona whose assumed acceptance criteria now exist.
- ADR [0006 — Deploy targets and LLM-app templates](0006-deploy-targets-and-llm-app-templates.md)
  — the Pages projects and templates this builds directly on.
- [Acceptance criteria & end-to-end tests](../user-guide/acceptance-criteria-and-e2e.md)
- [New repos & Cloudflare Pages](../user-guide/new-repo-and-cloudflare.md)
