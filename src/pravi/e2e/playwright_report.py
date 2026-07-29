"""Parse Playwright's JSON reporter output into a structured verdict.

The templates configure `reporter: "json"` under `CI=1`, which writes the
report to *stdout*. We parse that stream rather than reading a file, which
keeps the sandbox seam down to `exec` (no `read_file` needed).

Two things make this less trivial than `json.loads`:

  - npm/npx wrap the run in banner noise, so the JSON has to be located
    inside the captured stdout rather than assumed to be all of it.
  - Playwright nests results by file → describe → spec, arbitrarily deep,
    so failures have to be walked recursively.

Pure — no I/O — so it's exercised entirely by fixtures in tests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

MAX_FAILURES = 10
MAX_MESSAGE_CHARS = 2000
MAX_SNIPPET_CHARS = 1000


@dataclass
class E2EFailure:
    """One failing spec, shaped for both the UI and the repair prompt."""

    title: str  # "home page › shows the Today's tasks heading"
    file: str  # "e2e/home.spec.ts"
    line: int | None = None
    message: str = ""
    snippet: str | None = None  # Playwright's source excerpt around the failure


@dataclass
class ParsedReport:
    ok: bool  # did the report parse at all?
    passed: bool  # …and did every test pass?
    total: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    flaky_count: int = 0
    failures: list[E2EFailure] = field(default_factory=list)
    error: str | None = None  # set when ok is False


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s or "")


def _extract_json_object(stdout: str) -> str | None:
    """Carve the JSON report out of surrounding npm/npx banner noise."""
    if not stdout:
        return None
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stdout[start : end + 1]


def _walk_specs(
    suite: dict[str, Any], ancestors: list[str]
) -> list[tuple[dict[str, Any], list[str], str]]:
    """Flatten a suite tree into (spec, title_path, file) triples.

    `file` is carried down from whichever ancestor declared it — nested
    describe blocks don't repeat it.
    """
    out: list[tuple[dict[str, Any], list[str], str]] = []
    title = suite.get("title")
    path = [*ancestors, title] if title else list(ancestors)
    file = suite.get("file") or ""

    for spec in suite.get("specs") or []:
        out.append((spec, path, spec.get("file") or file))
    for child in suite.get("suites") or []:
        if not child.get("file"):
            child = {**child, "file": file}
        out.extend(_walk_specs(child, path))
    return out


def _failure_from_spec(
    spec: dict[str, Any], title_path: list[str], file: str, file_prefix: str
) -> E2EFailure:
    # Name it the way a human would read it: suite path › test title.
    # Playwright's outermost suite title IS the file name, which we already
    # report separately — drop it so the title isn't "home.spec.ts ›
    # home.spec.ts ›".
    parts = list(title_path)
    if parts and file and parts[0] == file:
        parts = parts[1:]
    parts = [p for p in [*parts, spec.get("title")] if p]
    message = ""
    snippet: str | None = None
    for test in spec.get("tests") or []:
        results = test.get("results") or []
        if not results:
            continue
        # The last result is the one that stuck (earlier ones are retries).
        err = results[-1].get("error") or {}
        if err:
            message = _strip_ansi(str(err.get("message") or ""))[:MAX_MESSAGE_CHARS]
            raw_snippet = err.get("snippet")
            if raw_snippet:
                snippet = _strip_ansi(str(raw_snippet))[:MAX_SNIPPET_CHARS]
            break
        errors = results[-1].get("errors") or []
        if errors:
            message = _strip_ansi(str(errors[0].get("message") or ""))[
                :MAX_MESSAGE_CHARS
            ]
            break
    return E2EFailure(
        title=" › ".join(parts) or "(untitled test)",
        # Playwright reports paths relative to testDir; prefix them so the
        # dev agent can open the file straight from the repo root.
        file=f"{file_prefix.rstrip('/')}/{file}" if file_prefix and file else file,
        line=spec.get("line"),
        message=message,
        snippet=snippet,
    )


def _counts_from_stats(stats: dict[str, Any]) -> tuple[int, int, int, int] | None:
    if not stats:
        return None
    try:
        return (
            int(stats.get("expected") or 0),
            int(stats.get("unexpected") or 0),
            int(stats.get("skipped") or 0),
            int(stats.get("flaky") or 0),
        )
    except (TypeError, ValueError):
        return None


def parse_playwright_json(
    stdout: str, *, truncated: bool = False, file_prefix: str = ""
) -> ParsedReport:
    """Turn captured stdout into a `ParsedReport`.

    `truncated=True` (the sandbox clipped the output) is reported as a
    parse failure rather than risking a misparse of half a document — a
    silently-empty failure list would look like a passing suite.

    `file_prefix` is the repo-relative test dir (e.g. `"e2e"`); Playwright
    reports paths relative to `testDir`, so this makes them openable.
    """
    if truncated:
        return ParsedReport(
            ok=False,
            passed=False,
            error=(
                "the test report exceeded the output capture limit, so it "
                "could not be parsed. Reduce the number of tests, or switch "
                "the reporter to write to a file."
            ),
        )

    blob = _extract_json_object(stdout)
    if blob is None:
        head = (stdout or "")[:1000]
        tail = (stdout or "")[-1000:]
        return ParsedReport(
            ok=False,
            passed=False,
            error=f"no JSON report found in test output. stdout head: {head!r} tail: {tail!r}",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as e:
        return ParsedReport(
            ok=False,
            passed=False,
            error=f"test output was not valid JSON ({e}); got {blob[:500]!r}",
        )
    if not isinstance(data, dict):
        return ParsedReport(
            ok=False, passed=False, error="JSON report root was not an object"
        )

    flattened: list[tuple[dict[str, Any], list[str], str]] = []
    for suite in data.get("suites") or []:
        flattened.extend(_walk_specs(suite, []))

    failures: list[E2EFailure] = []
    walked_passed = walked_failed = walked_skipped = 0
    for spec, title_path, file in flattened:
        # Playwright marks a skipped spec ok=True, so check both.
        is_skipped = all(
            (t.get("status") == "skipped") for t in (spec.get("tests") or []) if t
        ) and bool(spec.get("tests"))
        if is_skipped:
            walked_skipped += 1
            continue
        if spec.get("ok"):
            walked_passed += 1
            continue
        walked_failed += 1
        if len(failures) < MAX_FAILURES:
            failures.append(_failure_from_spec(spec, title_path, file, file_prefix))

    stats = _counts_from_stats(data.get("stats") or {})
    if stats is not None:
        passed_count, failed_count, skipped_count, flaky_count = stats
    else:
        passed_count, failed_count = walked_passed, walked_failed
        skipped_count, flaky_count = walked_skipped, 0

    # A suite that never ran a single test is not a pass. This is the
    # case where Playwright itself errored (bad config, no specs matched)
    # and would otherwise read as "0 failures, all green".
    total = passed_count + failed_count + skipped_count
    top_level_errors = data.get("errors") or []
    if total == 0 and not flattened:
        detail = ""
        if top_level_errors:
            detail = _strip_ansi(str(top_level_errors[0].get("message") or ""))[:500]
        return ParsedReport(
            ok=False,
            passed=False,
            error=f"the test run produced no tests. {detail}".strip(),
        )

    return ParsedReport(
        ok=True,
        passed=failed_count == 0 and not failures,
        total=total,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        flaky_count=flaky_count,
        failures=failures,
    )
