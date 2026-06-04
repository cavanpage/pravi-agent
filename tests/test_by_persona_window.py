"""Window cutoff math for the per-persona spend rollup (ADR 0004 FinOps).

The `_window_start` helper parses the user-facing slug (`7d`, `30d`,
`all`) into an `event.at` cutoff. A subtle off-by-one or unit mistake
would silently include / exclude the wrong runs in the spend totals.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


def test_window_7d_subtracts_correct_delta():
    from pravi.budget.by_persona import _window_start

    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    assert _window_start("7d", now) == now - timedelta(days=7)


def test_window_30d_subtracts_correct_delta():
    from pravi.budget.by_persona import _window_start

    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    assert _window_start("30d", now) == now - timedelta(days=30)


def test_window_all_returns_none():
    """Sentinel: `all` means no cutoff — every run is included."""
    from pravi.budget.by_persona import _window_start

    assert _window_start("all", datetime.now(UTC)) is None


@pytest.mark.parametrize("v", ["", None, "bogus", "7", "d7", "7m", "abc", "-7d", "0d"])
def test_invalid_window_returns_none(v):
    """Garbage input degrades to 'all-time' rather than crashing the
    endpoint. The UI's chip is constrained to 7d/30d/all anyway."""
    from pravi.budget.by_persona import _window_start

    assert _window_start(v, datetime.now(UTC)) is None


def test_window_large_d_value_works():
    """`365d` (a year) — sanity check that we handle non-trivial deltas."""
    from pravi.budget.by_persona import _window_start

    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    assert _window_start("365d", now) == now - timedelta(days=365)
