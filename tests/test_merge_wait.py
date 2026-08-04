"""Wait-for-merge pacing + done-ness semantics (pure unit)."""

from __future__ import annotations

from pravi.api.routes import _TASK_DONE, _derive_parent_status
from pravi.workflows.feature_workflow import _MERGE_MAX_POLLS, _merge_poll_backoff


def test_merge_backoff_is_monotonic_and_bounded():
    delays = [_merge_poll_backoff(n) for n in range(_MERGE_MAX_POLLS)]
    assert delays[0] == 60  # fresh PRs poll fast
    assert all(a <= b for a, b in zip(delays, delays[1:], strict=False))
    assert max(delays) == 900


def test_merge_watch_horizon_is_about_a_week():
    total = sum(_merge_poll_backoff(n) for n in range(_MERGE_MAX_POLLS))
    assert 6 * 86400 <= total <= 8 * 86400


def test_open_pr_does_not_count_as_done():
    """A dependent feature must not unblock while the prerequisite's PR
    is still open — merge is the gate."""
    assert _TASK_DONE == {"merged"}


def test_parent_status_with_open_pr_is_not_merged():
    # 2 tasks: one merged, one with an open PR → the parent is not done.
    derived = _derive_parent_status({"merged": 1, "pr_open": 1})
    assert derived != "merged"
