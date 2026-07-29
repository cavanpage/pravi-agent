"""Matching a Cloudflare preview deployment to the commit we pushed, and
deriving branch-alias hostnames (ADR 0007).

Pure unit — `match_deployment_by_commit` and `pages_branch_alias` take
plain data, so no HTTP is involved.
"""

from __future__ import annotations

import pytest

from pravi.services.cloudflare import (
    PagesDeployment,
    match_deployment_by_commit,
    pages_branch_alias,
    strip_ansi,
)

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _dep(**kw) -> PagesDeployment:
    base = dict(
        id="dep-1",
        url="https://a1b2c3d4.my-app.pages.dev",
        branch="pravi/t-42-frontend",
        commit_hash=SHA,
        stage_name="deploy",
        stage_status="success",
        created_on="2026-07-28T10:00:00Z",
    )
    base.update(kw)
    return PagesDeployment(**base)


def test_exact_commit_match():
    dep, how = match_deployment_by_commit([_dep()], commit_sha=SHA, branch="anything")
    assert how == "commit"
    assert dep is not None and dep.id == "dep-1"


def test_short_hash_from_cloudflare_matches_full_sha():
    """Some payload versions return an abbreviated hash."""
    dep, how = match_deployment_by_commit(
        [_dep(commit_hash=SHA[:7])], commit_sha=SHA, branch=None
    )
    assert how == "commit"
    assert dep is not None


def test_full_hash_matches_a_short_sha_we_hold():
    dep, how = match_deployment_by_commit(
        [_dep(commit_hash=SHA)], commit_sha=SHA[:8], branch=None
    )
    assert how == "commit"
    assert dep is not None


def test_hash_comparison_is_case_insensitive():
    dep, how = match_deployment_by_commit(
        [_dep(commit_hash=SHA.upper())], commit_sha=SHA, branch=None
    )
    assert how == "commit"
    assert dep is not None


def test_commit_match_wins_over_a_newer_branch_deployment():
    newer_other_commit = _dep(
        id="dep-newer", commit_hash="ffff1111", created_on="2026-07-28T12:00:00Z"
    )
    ours = _dep(id="dep-ours", created_on="2026-07-28T09:00:00Z")
    dep, how = match_deployment_by_commit(
        [newer_other_commit, ours], commit_sha=SHA, branch="pravi/t-42-frontend"
    )
    assert how == "commit"
    assert dep is not None and dep.id == "dep-ours"


def test_branch_fallback_picks_the_newest():
    older = _dep(id="old", commit_hash="0000", created_on="2026-07-28T09:00:00Z")
    newer = _dep(id="new", commit_hash="1111", created_on="2026-07-28T11:00:00Z")
    dep, how = match_deployment_by_commit(
        [older, newer], commit_sha=SHA, branch="pravi/t-42-frontend"
    )
    assert how == "branch"
    assert dep is not None and dep.id == "new"


def test_branch_fallback_ignores_other_branches():
    dep, how = match_deployment_by_commit(
        [_dep(commit_hash="0000", branch="someone-else")],
        commit_sha=SHA,
        branch="pravi/t-42-frontend",
    )
    assert (dep, how) == (None, None)


def test_no_candidates_at_all():
    assert match_deployment_by_commit([], commit_sha=SHA, branch="b") == (None, None)


def test_deployments_with_no_commit_hash_are_skipped_not_crashed():
    dep, how = match_deployment_by_commit(
        [_dep(id="blank", commit_hash=None), _dep(id="good")],
        commit_sha=SHA,
        branch=None,
    )
    assert how == "commit"
    assert dep is not None and dep.id == "good"


class TestTerminality:
    def test_deploy_success_is_success_and_terminal(self):
        d = _dep()
        assert d.succeeded and d.terminal

    @pytest.mark.parametrize("status", ["failure", "canceled"])
    def test_failed_stages_are_terminal_but_not_successful(self, status: str):
        d = _dep(stage_name="build", stage_status=status)
        assert d.terminal and not d.succeeded

    @pytest.mark.parametrize(
        "name,status",
        [("queued", "active"), ("build", "active"), ("deploy", "active")],
    )
    def test_in_flight_stages_are_neither(self, name: str, status: str):
        d = _dep(stage_name=name, stage_status=status)
        assert not d.terminal and not d.succeeded

    def test_a_successful_build_stage_is_not_yet_a_successful_deploy(self):
        """`build: success` just means the next stage started."""
        d = _dep(stage_name="build", stage_status="success")
        assert not d.succeeded and not d.terminal


@pytest.mark.parametrize(
    "branch,expected",
    [
        ("fix/api", "fix-api.my-app.pages.dev"),
        ("MixedCase/Branch", "mixedcase-branch.my-app.pages.dev"),
        ("pravi/t-42-frontend", "pravi-t-42-frontend.my-app.pages.dev"),
        ("a//b", "a-b.my-app.pages.dev"),
        ("--leading-and-trailing--", "leading-and-trailing.my-app.pages.dev"),
        ("feat/add_thing.v2", "feat-add-thing-v2.my-app.pages.dev"),
    ],
)
def test_branch_alias_sanitization(branch: str, expected: str):
    assert pages_branch_alias(branch, "my-app") == expected


def test_branch_alias_respects_the_dns_label_limit():
    alias = pages_branch_alias("x" * 100, "my-app")
    label = alias.split(".")[0]
    assert len(label) == 63
    assert not label.endswith("-")


def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m plain") == "red plain"
    assert strip_ansi("no codes") == "no codes"
