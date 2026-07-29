"""Run the Playwright suite against a deployed preview (ADR 0007).

Four phases inside the sandbox — install deps, install browsers, run the
suite, parse the report — each heartbeating so a long `npm ci` doesn't
look like a hung worker.

Design notes:

  - Non-zero exit from the test command is EXPECTED (that's what a failing
    test looks like); we parse the report either way and let the verdict
    come from the report, not the exit code.
  - A missing `e2e/` directory is a *repair signal*, not an infra error —
    it means the dev agent didn't write the specs it was asked for, and
    telling it so is more useful than failing the ticket.
  - Results land as a `Run(kind=tester)` row. Tester runs emit
    `e2e_started`/`e2e_finished`, never `run_finished`: that sentinel
    closes the live SSE stream, and the loop isn't over yet.
  - Zero tokens are spent here, which is why this belongs on the
    `features` queue rather than the cost-capped `llm` pool.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from temporalio import activity

from pravi.activities.preview_activity import PreviewSnapshot
from pravi.agents.sandbox.factory import get_sandbox
from pravi.agents.sandbox.protocols import (
    SandboxExecRequest,
    SandboxExecResult,
    SandboxHandle,
)
from pravi.config import get_settings
from pravi.db.models import Run, RunKind, RunStatus
from pravi.db.session import session_scope
from pravi.e2e.playwright_report import E2EFailure, parse_playwright_json
from pravi.events import KIND_E2E_FINISHED, KIND_E2E_STARTED, emit_event

log = structlog.get_logger(__name__)

# The test command's stdout carries the whole JSON report, so it gets a
# much larger capture budget than an ordinary command. A truncated report
# is unparseable, and an unparseable report must not read as "no failures".
_REPORT_CAPTURE_BYTES = 4_000_000

# Stages, in order. Reported on failure so the UI can say *where* it broke.
STAGE_SPECS = "specs"
STAGE_INSTALL = "install"
STAGE_BROWSERS = "browsers"
STAGE_TEST = "test"
STAGE_PARSE = "parse"
STAGE_DONE = "done"


@dataclass
class RunE2ERequest:
    ticket_id: int
    handle: SandboxHandle
    base_url: str
    preview: PreviewSnapshot
    attempt: int = 1


@dataclass
class RunE2EResult:
    ran: bool  # did the suite actually execute?
    passed: bool
    stage: str
    total: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    flaky_count: int = 0
    failures: list[E2EFailure] = field(default_factory=list)
    duration_ms: int = 0
    run_id: int | None = None
    base_url: str | None = None
    # Infra-level explanation (install broke, no specs, unparseable report).
    # Distinct from `failures`, which are genuine test failures.
    error: str | None = None


async def _exec_with_heartbeat(
    handle: SandboxHandle, req: SandboxExecRequest, *, stage: str
) -> SandboxExecResult:
    """Run a command, heartbeating every 30s while it works.

    `sandbox.exec` is a single long await; without a ticker alongside it,
    Temporal's heartbeat timeout would fire mid-`npm ci`.
    """
    sandbox = get_sandbox()
    task = asyncio.ensure_future(sandbox.exec(handle, req))
    while not task.done():
        activity.heartbeat(stage)
        await asyncio.wait({task}, timeout=30)
    return task.result()


async def _create_run_row(ticket_id: int, attempt: int) -> int:
    async with session_scope() as session:
        row = Run(
            ticket_id=ticket_id,
            kind=RunKind.tester,
            status=RunStatus.started,
            iteration=attempt,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _finalise_run_row(run_id: int, result: RunE2EResult) -> None:
    async with session_scope() as session:
        row = await session.get(Run, run_id)
        if row is None:
            return
        row.status = RunStatus.succeeded if result.passed else RunStatus.failed
        row.ended_at = datetime.now(UTC)
        row.error = result.error
        # The transcript is what `GET /tickets/{id}/preview` reads back.
        row.transcript = json.dumps(
            {
                "ran": result.ran,
                "passed": result.passed,
                "stage": result.stage,
                "base_url": result.base_url,
                "total": result.total,
                "passed_count": result.passed_count,
                "failed_count": result.failed_count,
                "skipped_count": result.skipped_count,
                "flaky_count": result.flaky_count,
                "duration_ms": result.duration_ms,
                "error": result.error,
                "failures": [
                    {
                        "title": f.title,
                        "file": f.file,
                        "line": f.line,
                        "message": f.message,
                        "snippet": f.snippet,
                    }
                    for f in result.failures
                ],
            }
        )


def _tail(s: str, n: int = 2000) -> str:
    s = (s or "").strip()
    return s[-n:]


@activity.defn
async def run_e2e(req: RunE2ERequest) -> RunE2EResult:
    settings = get_settings()
    preview = req.preview
    started = datetime.now(UTC)
    run_id = await _create_run_row(req.ticket_id, req.attempt)

    async with session_scope() as session:
        await emit_event(
            session,
            ticket_id=req.ticket_id,
            run_id=run_id,
            kind=KIND_E2E_STARTED,
            message=f"running e2e against {req.base_url}",
            payload={"base_url": req.base_url, "attempt": req.attempt},
        )

    def finish(result: RunE2EResult) -> RunE2EResult:
        result.run_id = run_id
        result.base_url = req.base_url
        result.duration_ms = int(
            (datetime.now(UTC) - started).total_seconds() * 1000
        )
        return result

    async def emit_finished(result: RunE2EResult) -> None:
        await _finalise_run_row(run_id, result)
        if result.passed:
            message = f"e2e passed ({result.passed_count}/{result.total})"
        elif result.ran:
            message = f"e2e failed: {result.failed_count} of {result.total} tests"
        else:
            message = f"e2e could not run ({result.stage}): {result.error or 'unknown'}"
        async with session_scope() as session:
            await emit_event(
                session,
                ticket_id=req.ticket_id,
                run_id=run_id,
                # NEVER run_finished — that sentinel closes the live stream,
                # and the repair loop may still have iterations to go.
                kind=KIND_E2E_FINISHED,
                message=message,
                payload={
                    "passed": result.passed,
                    "ran": result.ran,
                    "stage": result.stage,
                    "total": result.total,
                    "failed": result.failed_count,
                    "attempt": req.attempt,
                    "base_url": req.base_url,
                    "error": result.error,
                    "failures": [
                        {"title": f.title, "file": f.file, "line": f.line}
                        for f in result.failures[:5]
                    ],
                },
            )

    # 1) Are there any specs at all? A missing dir means the dev agent
    #    didn't do what it was asked — feed that back rather than failing.
    activity.heartbeat(STAGE_SPECS)
    probe = await _exec_with_heartbeat(
        req.handle,
        SandboxExecRequest(
            command=["test", "-d", preview.e2e_dir], timeout_seconds=30
        ),
        stage=STAGE_SPECS,
    )
    if probe.exit_code != 0:
        result = finish(
            RunE2EResult(
                ran=False,
                passed=False,
                stage=STAGE_SPECS,
                error=(
                    f"no `{preview.e2e_dir}/` directory in the branch — the dev "
                    "agent did not write any end-to-end specs."
                ),
            )
        )
        await emit_finished(result)
        return result

    # 2) Dependencies.
    install = await _exec_with_heartbeat(
        req.handle,
        SandboxExecRequest(
            command=list(preview.e2e_install),
            timeout_seconds=settings.e2e_install_timeout_seconds,
        ),
        stage=STAGE_INSTALL,
    )
    if install.exit_code != 0:
        blob = f"{install.stdout}\n{install.stderr}".lower()
        # `npm ci` refuses without a lockfile, or when one is out of sync
        # with package.json — which is exactly what happens when the agent
        # adds a dependency. Fall back rather than dead-ending the run.
        if any(
            s in blob
            for s in ("package-lock", "npm ci can only install", "lock file")
        ):
            log.info("e2e.install_fallback", ticket_id=req.ticket_id)
            install = await _exec_with_heartbeat(
                req.handle,
                SandboxExecRequest(
                    command=["npm", "install", "--no-audit", "--no-fund"],
                    timeout_seconds=settings.e2e_install_timeout_seconds,
                ),
                stage=STAGE_INSTALL,
            )
    if install.exit_code != 0:
        result = finish(
            RunE2EResult(
                ran=False,
                passed=False,
                stage=STAGE_INSTALL,
                error=f"dependency install failed: {_tail(install.stderr or install.stdout)}",
            )
        )
        await emit_finished(result)
        return result

    # 3) Browsers, into a shared cache so this is a one-time cost per host
    #    rather than ~150MB per worktree.
    browsers_path = str(settings.playwright_browsers_path_resolved)
    browser_cmd = ["npx", "playwright", "install"]
    if settings.playwright_install_deps:
        browser_cmd.append("--with-deps")
    browser_cmd.extend(preview.e2e_browsers)
    browsers = await _exec_with_heartbeat(
        req.handle,
        SandboxExecRequest(
            command=browser_cmd,
            env={"PLAYWRIGHT_BROWSERS_PATH": browsers_path},
            timeout_seconds=settings.e2e_install_timeout_seconds,
        ),
        stage=STAGE_BROWSERS,
    )
    if browsers.exit_code != 0:
        result = finish(
            RunE2EResult(
                ran=False,
                passed=False,
                stage=STAGE_BROWSERS,
                error=f"browser install failed: {_tail(browsers.stderr or browsers.stdout)}",
            )
        )
        await emit_finished(result)
        return result

    # 4) The suite. A non-zero exit here is a failing test, not an error.
    test_run = await _exec_with_heartbeat(
        req.handle,
        SandboxExecRequest(
            command=list(preview.e2e_command),
            env={
                preview.e2e_base_url_env: req.base_url,
                "PLAYWRIGHT_BROWSERS_PATH": browsers_path,
                # Selects the JSON reporter in the template's config.
                "CI": "1",
                # Keep ANSI out of the report in the first place.
                "FORCE_COLOR": "0",
                "NO_COLOR": "1",
            },
            timeout_seconds=preview.e2e_timeout_seconds,
            max_output_bytes=_REPORT_CAPTURE_BYTES,
        ),
        stage=STAGE_TEST,
    )
    if test_run.timed_out:
        result = finish(
            RunE2EResult(
                ran=False,
                passed=False,
                stage=STAGE_TEST,
                error=(
                    f"the e2e suite exceeded its {preview.e2e_timeout_seconds}s "
                    f"timeout. Output tail: {_tail(test_run.stdout)}"
                ),
            )
        )
        await emit_finished(result)
        return result

    # 5) Parse.
    activity.heartbeat(STAGE_PARSE)
    report = parse_playwright_json(
        test_run.stdout,
        truncated=test_run.truncated,
        file_prefix=preview.e2e_dir,
    )
    if not report.ok:
        result = finish(
            RunE2EResult(
                ran=False,
                passed=False,
                stage=STAGE_PARSE,
                error=(
                    f"{report.error} "
                    f"(test command exited {test_run.exit_code}; "
                    f"stderr tail: {_tail(test_run.stderr, 500)})"
                ),
            )
        )
        await emit_finished(result)
        return result

    result = finish(
        RunE2EResult(
            ran=True,
            passed=report.passed,
            stage=STAGE_DONE,
            total=report.total,
            passed_count=report.passed_count,
            failed_count=report.failed_count,
            skipped_count=report.skipped_count,
            flaky_count=report.flaky_count,
            failures=report.failures,
        )
    )
    log.info(
        "e2e.finished",
        ticket_id=req.ticket_id,
        attempt=req.attempt,
        passed=result.passed,
        total=result.total,
        failed=result.failed_count,
        base_url=req.base_url,
    )
    await emit_finished(result)
    return result


__all__ = ["RunE2ERequest", "RunE2EResult", "run_e2e"]
