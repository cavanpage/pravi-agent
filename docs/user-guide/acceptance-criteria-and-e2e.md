# Acceptance criteria & end-to-end tests

Pravi can take a ticket from idea all the way to *verified running
software*: it deploys each ticket's branch to an ephemeral Cloudflare Pages
preview, runs Playwright tests against that live deployment, and — if
anything fails — hands the failures back to the dev agent to fix, up to a
bounded number of attempts.

This guide covers how to drive it, what it costs, and what to do when it
misbehaves. The design rationale is in
[ADR 0007](../adr/0007-ephemeral-previews-and-e2e-repair-loop.md).

## The loop

```
dev agent implements + writes e2e/*.spec.ts → commits
  ↓
push branch ──→ open PR (draft) ─────────────────────────┐
  ↓                                                      │
Cloudflare builds a preview of that exact commit         │  repair commits
  → https://<hash>.<project>.pages.dev                   │  land on the
  ↓                                                      │  same PR
npx playwright test  (E2E_BASE_URL=<preview>)            │
  ↓                                                      │
failed? → structured failures → dev agent fixes ─────────┘  (≤ 3 attempts)
passed? → verdict=passed
```

The ticket page shows a **live preview** chip, a verdict pill, and an
end-to-end panel with per-test failures while this runs.

## Three switches, all of which must be on

The leg only runs when **all** of these hold. Any one of them off is a
clean no-op — the ticket takes the pre-0007 path (build → PR) exactly.

| switch | where | off means |
|---|---|---|
| the ticket has acceptance criteria | `## Acceptance criteria` in the ticket body or plan | `skipped_no_criteria` |
| the repo opts in | a `preview:` block in `.builder/domains.yaml` | `skipped_no_config` |
| the deployment is reachable | a Cloudflare Pages project for the repo | `skipped_no_config` |
| globally enabled | `PRAVI_E2E_ENABLED` (default `true`) | `skipped_no_config` |

Every ticket created before this feature shipped lacks the criteria
heading, so nothing changes for them.

## Writing good acceptance criteria

A criterion is **one user-observable statement, checkable by loading a URL
and interacting with the page.** The architect writes them during decompose
(a per-task `acceptance:` list) or planning (an `## Acceptance criteria`
section); you can edit them in the plan UI before approving.

They land in the ticket body as a checklist, which also shows up on the PR:

```markdown
## Acceptance criteria

- [ ] Visiting `/` shows a heading "Today's tasks".
- [ ] Clicking "Add" with an empty input shows an inline error and adds no row.
```

**Good** — visible behavior, stable anchors:

- `Visiting /settings shows a "Save" button.`
- `Submitting the form with a blank email shows the text "Email is required".`
- `Clicking "Download CSV" starts a file download named report.csv.`

**Bad** — the test can only see the rendered page:

- ~~`POST /api/todos returns 201.`~~ — HTTP status codes aren't visible.
- ~~`The TodoStore reducer handles the ADD action.`~~ — implementation detail.
- ~~`A row is inserted into the todos table.`~~ — DB state.
- ~~`The .btn-primary element is present.`~~ — CSS classes are unstable.

**Write 0–4 per task, and omit them entirely for work with no user-visible
surface** — refactors, config, migrations, docs, backend plumbing. An
invented criterion costs a wasted repair cycle. The architect is instructed
to do this, but it's worth checking at plan-approve time.

## Repo configuration

Add a `preview:` block to `.builder/domains.yaml`. Both starter templates
ship one already. Deleting the block turns the leg off for that repo.

```yaml
domains:
  - name: frontend
    paths: ["src/**"]
  - name: e2e
    description: "Playwright end-to-end specs, run against the deployed preview."
    paths: ["e2e/**", "playwright.config.ts"]
    test: "npx playwright test"

preview:
  provider: cloudflare-pages
  # project: my-app                 # defaults to the repo's Pages project
  wait_timeout_seconds: 900         # how long to wait for a build
  first_deployment_grace_seconds: 120  # webhook-registration grace
  e2e:
    dir: e2e
    install: ["npm", "ci"]
    browsers: ["chromium"]
    command: ["npx", "playwright", "test", "--reporter=json"]
    base_url_env: E2E_BASE_URL
    timeout_seconds: 900
```

Commands are **argv lists, not shell strings** — they're passed straight to
the sandbox, which never invokes a shell.

`project` resolution, in order: this field → the Pages project persisted on
the repo at create time → a one-shot name probe against Cloudflare (cached
back onto the repo row).

### The JSON-reporter contract

`playwright.config.ts` must select the **JSON reporter writing to stdout**
under `CI=1`, which is what the templates ship:

```ts
reporter: process.env.CI ? "json" : "list",
```

Pravi parses that stream. **Do not give the JSON reporter an `outputFile`**
— it would redirect the report to disk and pravi would see an empty result.
A truncated or unparseable report fails loudly rather than reading as a
green suite, but it still costs you the run.

## What the dev agent is told

When a task carries criteria, the dev prompt gains an "End-to-end tests"
section instructing it to:

- write one `test(...)` per criterion, named after the criterion;
- navigate **relatively** (`page.goto("/settings")`) — never hardcode a
  hostname, port, or `.pages.dev` domain;
- prefer `getByRole` / `getByLabel` / `getByText`, adding a `data-testid`
  when there's no stable accessible name;
- never add `waitForTimeout` sleeps.

The agent can't run the tests itself (nothing is deployed yet, and no
browsers are installed) — it writes them, and pravi runs them.

On a repair pass it additionally gets the preview URL, the failing test
names with their error messages and source snippets, and an explicit rule:
**fix the application, not the test**, and never delete/skip/`.fixme()` a
test to go green.

## Verdicts

| verdict | meaning |
|---|---|
| `passed` | every criterion verified against the live preview |
| `failing` | the suite is red after the attempt cap (or a repair made no new commit) |
| `build_failed` | Cloudflare's build broke, so no tests could run |
| `timed_out` | no preview appeared, or the build didn't finish in time |
| `skipped_no_criteria` | nothing to verify |
| `skipped_no_config` | the leg isn't configured for this repo |

The verdict is **a separate axis from the ticket's status** — a PR can be
open (`pr_open`) while its acceptance tests are red. That's deliberate: the
PR exists and is reviewable either way.

## Cost

Each repair attempt is a full dev-agent run. Worst case per ticket is
roughly:

```
PRAVI_E2E_MAX_ATTEMPTS × PRAVI_DEV_MAX_COST_USD   =   3 × $5   =   ~$15
```

⚠️ **Set a ceiling.** `PRAVI_TICKET_COST_CEILING_USD` (or per-ticket
`cost_ceiling_usd`) is the real cap — the loop treats a budget refusal as a
give-up condition — but it defaults to unlimited. A sane starting point:

```bash
PRAVI_TICKET_COST_CEILING_USD=10
```

Cloudflare preview builds and Playwright runs themselves are free; the cost
is entirely LLM time.

## Settings

| env var | default | meaning |
|---|---|---|
| `PRAVI_E2E_ENABLED` | `true` | global off-switch |
| `PRAVI_E2E_MAX_ATTEMPTS` | `3` | verification attempts before giving up |
| `PRAVI_E2E_INSTALL_TIMEOUT_SECONDS` | `900` | `npm ci` + browser install |
| `PRAVI_PLAYWRIGHT_BROWSERS_PATH` | `~/.pravi/playwright-browsers` | shared browser cache |
| `PRAVI_PLAYWRIGHT_INSTALL_DEPS` | `false` | pass `--with-deps` (see below) |

## Troubleshooting

**`no preview deployment appeared for commit …`**
Cloudflare never built the pushed commit. In order of likelihood:
1. The Pages project isn't git-connected to this repo — check
   Workers & Pages → the project → Settings → Builds & deployments.
2. Preview deployments are disabled for non-production branches. Pravi pins
   `preview_deployment_setting: all` on projects it creates, but a project
   made by hand (or edited in the dashboard) may differ.
3. The project name is wrong — set `preview.project` explicitly.
4. Cloudflare's GitHub App authorization was revoked (see
   [New repos & Cloudflare Pages](new-repo-and-cloudflare.md)).

This never triggers a repair run — it's a configuration problem, and an LLM
can't fix it.

**`build failed`**
A real build error. The last 200 log lines are fed to the agent, which gets
a repair attempt. Usually a TypeScript error or a missing dependency. Note
that `e2e/` and `playwright.config.ts` are excluded from the templates'
`tsc -b` (`tsconfig.app.json` includes only `src`) — if you restructure
that, a spec's types can break your production build.

**`no e2e/ directory — the dev agent did not write any specs`**
The agent implemented the feature but skipped the tests. This is treated as
a repair signal, not an infrastructure error, so it gets fed back.

**Browsers won't install**
`playwright install --with-deps` shells out to `sudo apt-get` on Linux and
will hang forever waiting for a password. It's off by default; only set
`PRAVI_PLAYWRIGHT_INSTALL_DEPS=true` where the worker can sudo
non-interactively.

**Flaky tests burning the whole repair budget**
The worst offender is an external dependency failing in a way the agent
can't fix. The `llm-chat` template's chat spec shows the pattern — assert
that the app handles *either* outcome:

```ts
await expect(
  page.getByTestId("assistant-message").or(page.getByTestId("quota-banner")),
).toBeVisible({ timeout: 30_000 });
```

Workers AI's free tier is 10,000 neurons/day account-wide; a 429 is an
expected outcome, not a product bug.

**Running the suite yourself**

```bash
npm run dev                                     # terminal 1
E2E_BASE_URL=http://localhost:5173 npm run e2e  # terminal 2

# …or against any deployed URL:
E2E_BASE_URL=https://my-app.pages.dev npm run e2e
```

## Turning it off

- **One repo:** delete the `preview:` block from `.builder/domains.yaml`.
- **One ticket:** don't give it acceptance criteria.
- **Everywhere:** `PRAVI_E2E_ENABLED=false`.
- **Keep it, but cheaper:** `PRAVI_E2E_MAX_ATTEMPTS=1` — deploy and verify,
  never auto-repair.
