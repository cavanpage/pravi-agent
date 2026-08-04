"""Curated Claude model list + per-stage model resolution.

Two things live here:
  1. `MODEL_CATALOG` — the small set of model IDs the UI exposes in the
     ticket-form dropdown. Keeping it here (not hard-coded in TS) lets the
     backend validate on create and the /api/models endpoint hand it to
     the frontend, so the two never drift.
  2. `resolve_stage_model()` — walks a ticket's inheritance chain to find
     the effective model for a given stage (clarify / decompose / draft /
     dev). Same shape as `pravi.budget.rollup.cost_rollup`.

Resolution order for a given stage:
    ticket.<stage>_model
    → each ancestor.<stage>_model in feature/epic order
    → settings.architect_<stage>_model (or dev_model for dev)
    → settings.architect_model (or dev_model)
    → None  (SDK falls back to its own default, currently claude-opus-5)
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from pravi.config import get_settings
from pravi.db.models import Ticket

Stage = str  # "clarify" | "decompose" | "draft" | "dev"

STAGES: tuple[Stage, ...] = ("clarify", "decompose", "draft", "dev")


@dataclass(frozen=True)
class ModelOption:
    """One entry in the dropdown the ticket form shows."""

    id: str  # canonical model id passed to the SDK
    label: str  # what the UI shows
    tier: str  # "flagship" | "balanced" | "fast" | "creative" — for grouping/tinting
    hint: str  # one-line tradeoff description


# Order matters — this is the order the dropdown renders in.
# Keep small: 4-6 entries. Add specific model IDs only when you actually
# want users choosing them; anything ad-hoc can go via env vars.
MODEL_CATALOG: tuple[ModelOption, ...] = (
    ModelOption(
        id="claude-opus-5",
        label="Opus 5",
        tier="flagship",
        hint="Highest reasoning; slowest and most expensive.",
    ),
    ModelOption(
        id="claude-sonnet-5",
        label="Sonnet 5",
        tier="balanced",
        hint="Strong reasoning, ~3× faster and cheaper than Opus. Good default.",
    ),
    ModelOption(
        id="claude-haiku-4-5-20251001",
        label="Haiku 4.5",
        tier="fast",
        hint="Fastest and cheapest; best for clarify + light planning.",
    ),
    ModelOption(
        id="claude-fable-5",
        label="Fable 5",
        tier="creative",
        hint="Optimised for creative/nuanced writing; rarely the right pick for code.",
    ),
)


_VALID_IDS = frozenset(o.id for o in MODEL_CATALOG)


def is_known_model(model_id: str | None) -> bool:
    """Cheap validation for create/patch payloads. Null is always allowed."""
    return model_id is None or model_id in _VALID_IDS


def _env_default_for_stage(stage: Stage) -> str | None:
    """Env-level fallback for a stage. Returns None if nothing is set."""
    s = get_settings()
    if stage == "dev":
        return s.dev_model
    # architect stages: per-stage override wins, then the umbrella default
    per_stage = getattr(s, f"architect_{stage}_model", None)
    return per_stage or s.architect_model


async def resolve_stage_model(
    session: AsyncSession, ticket: Ticket, stage: Stage
) -> str | None:
    """Walk self → parents → env for the ticket's effective model at `stage`.

    Returns None only when nothing in the chain sets one — meaning the
    architect/dev-agent factory keeps the SDK on its own default (opus-5).
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; must be one of {STAGES}")

    attr = f"{stage}_model"

    # Walk up parent chain, capped at 8 for defense-in-depth against cycles.
    current: Ticket | None = ticket
    for _ in range(8):
        if current is None:
            break
        val = getattr(current, attr, None)
        if val:
            return val
        if current.parent_id is None:
            break
        current = await session.get(Ticket, current.parent_id)

    return _env_default_for_stage(stage)
