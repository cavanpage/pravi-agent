"""Cost ceilings + roll-ups across the epic → feature → task hierarchy.

Two-layer protection:
  * **Per-run** — handled by the SDK via `ClaudeAgentOptions.max_budget_usd`.
    A single run can't exceed `settings.dev_max_cost_usd` (default $5).
  * **Cumulative** — handled here. Each ticket has an optional
    `cost_ceiling_usd`; null inherits from the nearest ancestor that sets
    one, falling back to `settings.ticket_cost_ceiling_usd` (default None
    = unlimited). At task-run start the dev_activity calls
    ``effective_remaining()`` to:
      1. Refuse to start if any applicable ceiling is already exhausted.
      2. Clamp the per-run cap down to the smallest remaining budget so a
         single run can't push spend past the ceiling.

Spend is computed by summing ``total_cost_usd`` out of the ``run_finished``
event payload for every descendant task. The SDK populates that field from
token counts × Anthropic's API rates — on a Pro account those are *notional*
dollars (no money changes hands), but they still track quota burn linearly.
"""
from pravi.budget.by_persona import (
    PersonaSpend,
    StackSpend,
    aggregate_by_persona,
    aggregate_by_stack,
)
from pravi.budget.rollup import (
    BudgetBreakdown,
    BudgetRollup,
    cost_rollup,
    descendant_task_ids,
    effective_remaining,
    total_spend,
)

__all__ = [
    "BudgetBreakdown",
    "BudgetRollup",
    "PersonaSpend",
    "StackSpend",
    "aggregate_by_persona",
    "aggregate_by_stack",
    "cost_rollup",
    "descendant_task_ids",
    "effective_remaining",
    "total_spend",
]
