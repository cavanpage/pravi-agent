"""Parsing Playwright's JSON reporter output (ADR 0007).

The fixtures under `tests/fixtures/playwright/` are REAL reports captured
from a Playwright run (only the huge `config` block and per-result stdout
blobs were stripped) — a hand-written approximation of this schema is
exactly the kind of thing that silently drifts.

Pure unit: no DB, no network, no browsers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pravi.e2e.playwright_report import MAX_FAILURES, parse_playwright_json

FIXTURES = Path(__file__).parent / "fixtures" / "playwright"


def _fixture(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def test_all_passing_report():
    r = parse_playwright_json(_fixture("all_pass"))
    assert r.ok and r.passed
    assert (r.total, r.passed_count, r.failed_count) == (2, 2, 0)
    assert r.failures == []


def test_report_with_failures():
    r = parse_playwright_json(_fixture("failures"), file_prefix="e2e")
    assert r.ok and not r.passed
    assert (r.total, r.passed_count, r.failed_count, r.skipped_count) == (4, 1, 2, 1)
    assert len(r.failures) == 2


def test_failure_titles_include_the_describe_path_without_repeating_the_file():
    r = parse_playwright_json(_fixture("failures"), file_prefix="e2e")
    titles = [f.title for f in r.failures]
    assert "home page › shows the Today's tasks heading" in titles
    # Arbitrarily nested describes are walked recursively.
    assert "outer › inner › deep failure" in titles
    # Playwright's outermost suite title IS the filename; it's reported
    # separately, so it must not be duplicated into the title.
    assert not any(t.startswith("home.spec.ts") for t in titles)


def test_failure_locations_are_repo_relative():
    """Playwright reports paths relative to testDir; the agent needs to be
    able to open them from the repo root."""
    r = parse_playwright_json(_fixture("failures"), file_prefix="e2e")
    files = {f.file for f in r.failures}
    assert files == {"e2e/home.spec.ts", "e2e/nested.spec.ts"}
    assert all(f.line and f.line > 0 for f in r.failures)


def test_no_prefix_leaves_paths_as_reported():
    r = parse_playwright_json(_fixture("failures"))
    assert {f.file for f in r.failures} == {"home.spec.ts", "nested.spec.ts"}


def test_failure_carries_message_and_source_snippet():
    r = parse_playwright_json(_fixture("failures"), file_prefix="e2e")
    failure = next(f for f in r.failures if "Today's tasks" in f.title)
    assert "expect(received)" in failure.message
    assert "Today's tasks" in failure.message
    assert failure.snippet and "test(" in failure.snippet


def test_a_run_with_no_tests_is_not_a_pass():
    """Playwright exits with `expected: 0, unexpected: 0` when the config
    matches nothing. Read naively that looks like a green suite."""
    r = parse_playwright_json(_fixture("no_tests"))
    assert not r.ok and not r.passed
    assert r.error and "no tests" in r.error.lower()


def test_npm_banner_noise_around_the_json():
    """`npx`/`npm run` print their own preamble on the same stream."""
    noisy = (
        "> my-app@0.0.0 e2e\n> playwright test\n\n"
        + _fixture("failures")
        + "\n\nnpm notice New minor version available\n"
    )
    r = parse_playwright_json(noisy, file_prefix="e2e")
    assert r.ok and r.failed_count == 2


def test_ansi_codes_are_stripped_from_messages():
    data = json.loads(_fixture("failures"))

    def paint(suite):
        for spec in suite.get("specs", []):
            for t in spec.get("tests", []):
                for res in t.get("results", []):
                    if res.get("error", {}).get("message"):
                        res["error"]["message"] = (
                            "\x1b[31m" + res["error"]["message"] + "\x1b[0m"
                        )
        for child in suite.get("suites", []):
            paint(child)

    for s in data["suites"]:
        paint(s)

    r = parse_playwright_json(json.dumps(data))
    assert r.failed_count == 2
    assert all("\x1b" not in f.message for f in r.failures)


@pytest.mark.parametrize(
    "stdout",
    ["", "   ", "Error: command not found: playwright", "not json at all"],
)
def test_unparseable_output_is_reported_not_swallowed(stdout: str):
    r = parse_playwright_json(stdout)
    assert not r.ok and not r.passed
    assert r.error


def test_output_with_no_closing_brace_is_reported():
    r = parse_playwright_json('{"suites": [ truncated')
    assert not r.ok
    assert r.error and "no JSON report found" in r.error


def test_malformed_json_between_valid_delimiters_is_reported():
    """Braces present but the contents don't decode."""
    r = parse_playwright_json('{"suites": [ , ] }')
    assert not r.ok
    assert r.error and "not valid JSON" in r.error


def test_truncated_capture_fails_loudly():
    """A clipped report must never be parsed optimistically — half a
    document yields an empty failure list, which reads as 'all green'."""
    r = parse_playwright_json(_fixture("failures"), truncated=True)
    assert not r.ok and not r.passed
    assert r.error and "capture limit" in r.error


def test_failure_list_is_capped_but_counts_are_not():
    data = json.loads(_fixture("failures"))
    template = data["suites"][0]
    # Clone the failing suite well past the cap.
    data["suites"] = [json.loads(json.dumps(template)) for _ in range(MAX_FAILURES + 5)]
    data["stats"] = {"expected": 0, "unexpected": MAX_FAILURES + 5, "skipped": 0, "flaky": 0}
    r = parse_playwright_json(json.dumps(data))
    assert len(r.failures) == MAX_FAILURES
    assert r.failed_count == MAX_FAILURES + 5
    assert not r.passed


def test_counts_are_read_from_stats_when_present():
    data = json.loads(_fixture("all_pass"))
    data["stats"] = {"expected": 7, "unexpected": 0, "skipped": 2, "flaky": 1}
    r = parse_playwright_json(json.dumps(data))
    assert (r.passed_count, r.skipped_count, r.flaky_count, r.total) == (7, 2, 1, 9)
    assert r.passed


def test_counts_fall_back_to_walking_when_stats_are_missing():
    data = json.loads(_fixture("failures"))
    data.pop("stats", None)
    r = parse_playwright_json(json.dumps(data))
    assert r.failed_count == 2
    assert not r.passed


def test_long_messages_are_capped():
    data = json.loads(_fixture("failures"))

    def blow_up(suite):
        for spec in suite.get("specs", []):
            for t in spec.get("tests", []):
                for res in t.get("results", []):
                    if res.get("error"):
                        res["error"]["message"] = "x" * 50_000
        for child in suite.get("suites", []):
            blow_up(child)

    for s in data["suites"]:
        blow_up(s)

    r = parse_playwright_json(json.dumps(data))
    assert all(len(f.message) <= 2000 for f in r.failures)
