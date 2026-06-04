# Budgets and spend tracking

Pravi spends real money. Every dev-agent run calls the Claude SDK, and
those calls roll up to a per-ticket spend total. This guide explains
how the budget system stays out of your way while it works — and how to
tighten it when it matters.

Three layers control spend, from cheapest to most expensive to learn:

1. **Per-run caps** — env-driven hard limits for a single dev or
   architect invocation (`PRAVI_DEV_MAX_*`, `PRAVI_ARCHITECT_*`).
2. **Per-ticket ceilings** — cumulative caps that follow the Epic →
   Feature → Task hierarchy (`cost_ceiling_usd`).
3. **Spend dashboards** — read-only views grouped by persona and stack
   so you can see *where* the money is going (`/api/spend/by-persona`,
   `/api/spend/by-stack`).

If you only read one section, make it [Pre-flight enforcement](#pre-flight-enforcement)
— that's the safety net that protects every dev run.

---

## Per-run caps (env overrides)

These are the floor: a single SDK call cannot exceed them, regardless
of what ceilings sit above it. They are read once at process start
from `pravi.config.Settings`.

### Dev agent — `PRAVI_DEV_MAX_*`

| Env var | Default | What it caps |
|---|---|---|
| `PRAVI_DEV_MAX_WALL_SECONDS` | `1800` | Temporal-side activity timeout for a single dev run. |
| `PRAVI_DEV_MAX_TURNS` | `50` | SDK-side cap on agent iterations per run. |
| `PRAVI_DEV_MAX_COST_USD` | `5.0` | SDK-side hard budget per run, in USD. |

The cost cap is what stops a runaway dev agent from quietly burning a
month's allowance on one bad task. If the per-ticket effective
remaining is tighter (see below), the activity clamps the per-run cap
down to that smaller value before invoking the SDK.

### Architect agent — `PRAVI_ARCHITECT_*`

| Env var | Default | What it caps |
|---|---|---|
| `PRAVI_ARCHITECT_MAX_WALL_SECONDS` | `300` | Wall-clock cap for clarify / decompose / draft. |
| `PRAVI_ARCHITECT_MAX_TURNS` | `30` | SDK turns per architect call. |
| `PRAVI_ARCHITECT_MAX_COST_USD` | `1.0` | Per-call cost ceiling for an architect call. |

These apply to every architect mode (clarify, decompose, draft).
There is no per-ticket rollup for architect spend today — the
architect runs happen on epics/features *before* tasks exist, so the
per-call cap is the whole story.

### Example: tighter caps for a side project

```bash
export PRAVI_DEV_MAX_COST_USD=2.0
export PRAVI_DEV_MAX_TURNS=30
export PRAVI_ARCHITECT_MAX_COST_USD=0.5
```

Add these to `.env` if you'd rather not export them per-shell.

---

## Per-ticket ceilings and hierarchical inheritance

Every ticket (Epic, Feature, or Task) has an optional
`cost_ceiling_usd` field. It is **cumulative** — the cap is on total
spend across every dev run that has ever landed against the ticket's
subtree, not per-run.

```
Epic       ($20 ceiling)
 └─ Feature ($10 ceiling)
     └─ Task A   (no ceiling set)
     └─ Task B   ($3 ceiling)
```

A run against **Task B** is constrained by the *tightest* remaining
budget across `self`, `feature`, and `epic`. If Task B has spent
$1, the feature has spent $4, and the epic has spent $9, then:

- Task B remaining: `$3 - $1 = $2`
- Feature remaining: `$10 - $4 = $6`
- Epic remaining: `$20 - $9 = $11`
- **Effective remaining: `$2`** (Task B is the binding constraint)

The constraint source — `self` / `feature` / `epic` / `env_default` /
`unlimited` — surfaces in the API response so the UI's `BudgetMeter`
can show *which* level is squeezing the run.

### The fallback chain

If nothing in the chain sets a ceiling, Pravi falls back to:

1. `PRAVI_TICKET_COST_CEILING_USD` if set — applies to *self* as the
   `env_default` constraint.
2. Otherwise unlimited (the per-run `PRAVI_DEV_MAX_COST_USD` is still
   in force).

The rollup logic lives in
[`src/pravi/budget/rollup.py`](../../src/pravi/budget/rollup.py) — see
`cost_rollup()` for the full pure-read implementation and
`effective_remaining()` for the one-call helper the dev activity uses.

### How spend is summed

`total_spend(session, ticket_id)` walks the subtree (Task → just
itself; Feature → child Tasks; Epic → grandchild Tasks via Features)
and sums `total_cost_usd` from every `run_finished` event. Prompt-
cache-only turns sometimes report a `None` cost; those count as zero.

---

## Pre-flight enforcement

Before every dev run, `dev_activity.run_dev_agent` calls
`effective_remaining(session, ticket_id)`:

1. **If remaining ≤ 0** → the activity refuses the run and emits a
   `budget_exhausted` failure classification. No SDK call happens.
   This is the safety net: a stuck loop that re-queues the same
   ticket cannot keep spending.
2. **If remaining is finite** → the per-run cap (`max_cost_usd`) is
   clamped down to `min(PRAVI_DEV_MAX_COST_USD, effective_remaining)`
   *before* the SDK starts. A single run cannot overshoot the ticket's
   remaining headroom, even if the SDK's own budget tracking lags.
3. **If remaining is `None`** → unlimited everywhere; only the
   per-run env cap applies.

The `run_started` event records the resolved `budget_remaining_usd`
and `max_cost_usd` so post-hoc analysis can tell whether the run was
constrained.

Setting a child ceiling that exceeds the parent's effective remaining
is rejected at PATCH time, too — see below. The pre-flight only
catches the runtime case; the API rejects the foot-gun earlier.

---

## Editing a ticket's ceiling

### `PATCH /api/tickets/{external_id}/budget`

Body:

```json
{ "cost_ceiling_usd": 7.5 }
```

Passing `null` clears the ceiling and reverts to inheritance.

```bash
# Set Task t-1a85 to $7.50 cumulative.
curl -X PATCH http://localhost:8765/api/tickets/t-1a85/budget \
  -H 'content-type: application/json' \
  -d '{"cost_ceiling_usd": 7.5}'

# Clear it — re-inherit from parent / env default.
curl -X PATCH http://localhost:8765/api/tickets/t-1a85/budget \
  -H 'content-type: application/json' \
  -d '{"cost_ceiling_usd": null}'
```

The endpoint rejects:

- Negative values → `400 cost_ceiling_usd cannot be negative`.
- Ceilings that exceed the parent's effective remaining → `400`
  with the binding constraint named. Example:

  ```
  cost_ceiling_usd $25.00 exceeds parent's effective
  remaining $11.40 (constrained by epic)
  ```

Response is the updated `TicketOut` with the new `cost_ceiling_usd`
field populated.

### Inspecting the full rollup

`GET /api/tickets/{external_id}/cost-rollup` returns the same view
the UI `BudgetMeter` renders:

```json
{
  "ticket_id": 42,
  "external_id": "t-1a85",
  "kind": "task",
  "own_ceiling_usd": 7.5,
  "own_spent_usd": 1.20,
  "effective_remaining_usd": 6.30,
  "constraint_source": "self",
  "chain": [
    { "kind": "task",    "title": "...", "own_ceiling_usd": 7.5,  "spent_usd": 1.20, "remaining_usd": 6.30  },
    { "kind": "feature", "title": "...", "own_ceiling_usd": 10.0, "spent_usd": 4.05, "remaining_usd": 5.95  },
    { "kind": "epic",    "title": "...", "own_ceiling_usd": 20.0, "spent_usd": 9.10, "remaining_usd": 10.90 }
  ]
}
```

Use `constraint_source` to label *why* a run was blocked or clamped.

---

## Spend dashboards

Two read-only endpoints power the FinOps widgets on the home
dashboard. Both aggregate `run_finished` event costs by attributes on
the task ticket (not on its ancestors — only tasks have dev runs).

### `GET /api/spend/by-persona`

Sums dev-run cost grouped by `tickets.persona`. NULL persona
aggregates under the slug `other`.

Query parameters:

| Name | Default | Notes |
|---|---|---|
| `window` | `all` | `7d`, `30d`, or `all` |
| `repo_id` | *(unset)* | Scope to one repo's tickets |

```bash
curl 'http://localhost:8765/api/spend/by-persona?window=7d'
```

```json
[
  { "persona": "backend",  "spent_usd": 12.413, "run_count": 8, "ticket_count": 3 },
  { "persona": "frontend", "spent_usd":  3.802, "run_count": 4, "ticket_count": 2 },
  { "persona": "other",    "spent_usd":  0.910, "run_count": 1, "ticket_count": 1 }
]
```

Rows are sorted by `spent_usd` descending — highest burners first,
which matches the dashboard's reading order.

### `GET /api/spend/by-stack`

Same shape, grouped by `tickets.stack`. NULL stack aggregates under
`unknown`. The architect mints stack slugs during decomposition (see
the persona/stack catalog guide); unknown slugs in the catalog still
show up here under their literal slug — the resolution to `unknown`
only happens for NULL.

```bash
curl 'http://localhost:8765/api/spend/by-stack?window=30d&repo_id=4'
```

```json
[
  { "stack": "python-fastapi", "spent_usd": 14.21, "run_count": 9, "ticket_count": 4 },
  { "stack": "react-vite",     "spent_usd":  2.07, "run_count": 3, "ticket_count": 2 },
  { "stack": "unknown",        "spent_usd":  0.18, "run_count": 1, "ticket_count": 1 }
]
```

`ticket_count` counts *distinct* tickets that contributed — so a
ticket with five runs against it counts as `ticket_count: 1`,
`run_count: 5`. Use that to spot tickets that are churning runs without
making forward progress.

---

## Quick reference

| You want to… | Do this |
|---|---|
| Cap a single dev call | Set `PRAVI_DEV_MAX_COST_USD`. |
| Cap an entire epic across all child tasks | `PATCH /api/tickets/{epic-id}/budget` with the epic-wide cap. |
| Stop runs once a task hits a hard limit | Set a Task-level ceiling. Pre-flight will block further runs. |
| Re-enable an over-budget ticket | Raise its (or an ancestor's) ceiling via PATCH, or clear with `null`. |
| Find which persona is burning the most this week | `GET /api/spend/by-persona?window=7d`. |
| Find which stack is most expensive on one repo | `GET /api/spend/by-stack?repo_id=N`. |
| Default cap for *all* tickets when none is set | `PRAVI_TICKET_COST_CEILING_USD`. |
| See the constraint that blocked a run | `GET /api/tickets/{id}/cost-rollup` → `constraint_source`. |

---

## See also

- [`src/pravi/budget/rollup.py`](../../src/pravi/budget/rollup.py) —
  ceiling resolution + subtree spend rollup.
- [`src/pravi/budget/by_persona.py`](../../src/pravi/budget/by_persona.py)
  — persona / stack aggregation queries.
- [`src/pravi/activities/dev_activity.py`](../../src/pravi/activities/dev_activity.py)
  — pre-flight enforcement and `budget_exhausted` classification.
- ADR 0004 — persona / stack catalog (what drives the dashboards'
  grouping keys).
