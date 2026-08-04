"""The deploy → e2e → repair loop in FeatureWorkflow (ADR 0007).

This is the one Temporal-harness test in the repo, and it earns that
exception: the loop is pure control flow — a bounded iteration with five
distinct give-up conditions and a timer-driven polling loop — which is
exactly the shape that's cheap to test with time-skipping and expensive to
test any other way. The alternative is validating it only by running real
Cloudflare builds, which is slow, costs money, and can't reach the failure
branches at all.

Every activity is stubbed, so no DB, no network, and no Cloudflare.

The first run downloads the Temporal test-server binary; set
PRAVI_SKIP_TEMPORAL_TESTS=1 to skip.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from pravi.activities.db_activity import (
    EmitEventRequest,
    PlanData,
    SynthesizePlanRequest,
    TicketRef,
    TicketStatusUpdate,
)
from pravi.activities.dev_activity import DevActivityRequest, DevActivityResult
from pravi.activities.e2e_activity import RunE2ERequest, RunE2EResult
from pravi.activities.pr_activity import (
    CheckPRStateRequest,
    CheckPRStateResult,
    OpenPRRequest,
    OpenPRResult,
    PushBranchRequest,
    PushBranchResult,
)
from pravi.activities.preview_activity import (
    DeploymentLogsRequest,
    LoadPreviewConfigRequest,
    PollPreviewRequest,
    PreviewDeployment,
    PreviewSnapshot,
    RecordPreviewOutcomeRequest,
)
from pravi.activities.sandbox_activity import CleanupRequest, ProvisionRequest
from pravi.agents.sandbox.protocols import SandboxHandle
from pravi.e2e.playwright_report import E2EFailure
from pravi.workflows.feature_workflow import (
    VERDICT_BUILD_FAILED,
    VERDICT_FAILING,
    VERDICT_PASSED,
    VERDICT_SKIPPED_NO_CONFIG,
    VERDICT_SKIPPED_NO_CRITERIA,
    VERDICT_TIMED_OUT,
    FeatureWorkflow,
    FeatureWorkflowInput,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("PRAVI_SKIP_TEMPORAL_TESTS") == "1",
    reason="PRAVI_SKIP_TEMPORAL_TESTS=1",
)

TASK_QUEUE = "test-features"

BODY_WITH_CRITERIA = (
    "Build the todo list.\n\n"
    "## Acceptance criteria\n\n"
    '- [ ] Visiting / shows a heading "Today\'s tasks".\n'
)
BODY_WITHOUT_CRITERIA = "Refactor the store. No user-visible change."

PREVIEW = PreviewSnapshot(
    project="my-app",
    provider="cloudflare-pages",
    wait_timeout_seconds=900,
    first_deployment_grace_seconds=120,
    e2e_dir="e2e",
    e2e_install=["npm", "ci"],
    e2e_browsers=["chromium"],
    e2e_command=["npx", "playwright", "test", "--reporter=json"],
    e2e_base_url_env="E2E_BASE_URL",
    e2e_timeout_seconds=900,
)


class Recorder:
    """Scripted stand-ins for every activity, plus a call log.

    Each `*_script` is a list consumed one entry per call; when it runs
    out the last entry repeats, so "always fails" needs a single entry.
    """

    def __init__(
        self,
        *,
        body: str = BODY_WITH_CRITERIA,
        preview: PreviewSnapshot | None = PREVIEW,
        e2e_script: list[RunE2EResult] | None = None,
        poll_script: list[PreviewDeployment] | None = None,
        push_script: list[PushBranchResult] | None = None,
        dev_script: list[DevActivityResult] | None = None,
        pr_state_script: list[str] | None = None,
    ) -> None:
        self.body = body
        self.preview = preview
        self.e2e_script = e2e_script or [_e2e(passed=True)]
        self.poll_script = poll_script or [_deployed()]
        self.push_script = push_script or []
        self.dev_script = dev_script or []
        # Wait-for-merge poll answers. Default: the PR merges on the first
        # poll, so tests exercise the full open → merged lifecycle without
        # hundreds of time-skipped polls.
        self.pr_state_script = pr_state_script or ["merged"]
        self._pr_state_n = 0
        self.calls: list[str] = []
        self.dev_tasks: list[str] = []
        self.dev_iterations: list[int] = []
        self.dev_terminal: list[bool] = []
        self.open_pr_calls: list[OpenPRRequest] = []
        self.recorded_outcomes: list[RecordPreviewOutcomeRequest] = []
        self.statuses: list[str] = []
        self.events: list[str] = []
        self._push_n = 0
        self._poll_n = 0
        self._e2e_n = 0
        self._dev_n = 0

    def _next(self, script: list, counter_name: str):
        n = getattr(self, counter_name)
        setattr(self, counter_name, n + 1)
        return script[min(n, len(script) - 1)]

    def activities(self) -> list:
        rec = self

        @activity.defn(name="load_ticket")
        async def load_ticket(ticket_id: int) -> TicketRef:
            rec.calls.append("load_ticket")
            return TicketRef(
                ticket_id=ticket_id,
                repo_id=1,
                repo_local_path="/tmp/my-app",
                repo_name="my-app",
                external_id="t-42",
                title="Build the todo list",
                body=rec.body,
                domain_name="frontend",
                ancestral_body_md=rec.body,
            )

        @activity.defn(name="synthesize_plan_from_body")
        async def synthesize_plan(req: SynthesizePlanRequest) -> PlanData:
            rec.calls.append("synthesize_plan")
            return PlanData(
                plan_id=7,
                ticket_id=req.ticket_id,
                domain_name=req.domain_name,
                content_md="## Summary\nDo it.",
            )

        @activity.defn(name="update_ticket_status")
        async def update_status(req: TicketStatusUpdate) -> None:
            rec.statuses.append(req.status)

        @activity.defn(name="emit_ticket_event")
        async def emit_event(req: EmitEventRequest) -> None:
            rec.events.append(req.kind)

        @activity.defn(name="provision_sandbox")
        async def provision(req: ProvisionRequest) -> SandboxHandle:
            rec.calls.append("provision")
            return SandboxHandle(
                sandbox_id="/tmp/wt",
                cwd="/tmp/wt",
                branch=req.branch,
                origin_url="https://github.com/me/my-app.git",
                backend="local",
            )

        @activity.defn(name="cleanup_sandbox")
        async def cleanup(req: CleanupRequest) -> None:
            rec.calls.append("cleanup")

        @activity.defn(name="load_preview_config")
        async def load_preview(
            req: LoadPreviewConfigRequest,
        ) -> PreviewSnapshot | None:
            rec.calls.append("load_preview_config")
            return rec.preview

        @activity.defn(name="run_dev")
        async def run_dev(req: DevActivityRequest) -> DevActivityResult:
            rec.calls.append("run_dev")
            rec.dev_tasks.append(req.task)
            rec.dev_iterations.append(req.iteration)
            rec.dev_terminal.append(req.terminal)
            if rec.dev_script:
                return rec._next(rec.dev_script, "_dev_n")
            return _dev(success=True)

        @activity.defn(name="push_branch")
        async def push(req: PushBranchRequest) -> PushBranchResult:
            rec.calls.append("push_branch")
            if rec.push_script:
                return rec._next(rec.push_script, "_push_n")
            # Default: a distinct SHA per push, so the "no new commit"
            # guard doesn't fire unless a test asks for it.
            rec._push_n += 1
            return PushBranchResult(
                pushed=True,
                commits_ahead=1,
                head_sha=f"{rec._push_n:040d}",
                owner="me",
                repo="my-app",
            )

        @activity.defn(name="open_pr")
        async def open_pr_(req: OpenPRRequest) -> OpenPRResult:
            rec.calls.append("open_pr")
            rec.open_pr_calls.append(req)
            return OpenPRResult(pr_number=11, pr_url="https://github.com/me/my-app/pull/11")

        @activity.defn(name="poll_preview_deployment")
        async def poll(req: PollPreviewRequest) -> PreviewDeployment:
            rec.calls.append("poll")
            return rec._next(rec.poll_script, "_poll_n")

        @activity.defn(name="fetch_deployment_logs")
        async def logs(req: DeploymentLogsRequest) -> str:
            rec.calls.append("fetch_logs")
            return "error TS2304: Cannot find name 'Foo'."

        @activity.defn(name="run_e2e")
        async def run_e2e_(req: RunE2ERequest) -> RunE2EResult:
            rec.calls.append("run_e2e")
            return rec._next(rec.e2e_script, "_e2e_n")

        @activity.defn(name="check_pr_state")
        async def check_pr_state_(req: CheckPRStateRequest) -> CheckPRStateResult:
            rec.calls.append("check_pr_state")
            return CheckPRStateResult(state=rec._next(rec.pr_state_script, "_pr_state_n"))

        @activity.defn(name="record_preview_outcome")
        async def record(req: RecordPreviewOutcomeRequest) -> None:
            rec.recorded_outcomes.append(req)

        return [
            load_ticket,
            synthesize_plan,
            update_status,
            emit_event,
            provision,
            cleanup,
            load_preview,
            run_dev,
            push,
            open_pr_,
            poll,
            logs,
            run_e2e_,
            check_pr_state_,
            record,
        ]

    def count(self, name: str) -> int:
        return self.calls.count(name)


def _dev(*, success: bool = True, stop_reason: str | None = None) -> DevActivityResult:
    return DevActivityResult(
        success=success,
        summary="did the thing",
        prompt_version="dev/v4",
        stop_reason=stop_reason,
        num_turns=3,
        duration_ms=1000,
        total_cost_usd=0.5,
        session_id="s",
    )


def _e2e(*, passed: bool, ran: bool = True) -> RunE2EResult:
    return RunE2EResult(
        ran=ran,
        passed=passed,
        stage="done" if ran else "specs",
        total=2,
        passed_count=2 if passed else 1,
        failed_count=0 if passed else 1,
        failures=[]
        if passed
        else [
            E2EFailure(
                title="home page › shows the heading",
                file="e2e/home.spec.ts",
                line=4,
                message="expect(locator).toBeVisible() failed",
                snippet="> 4 |  await expect(...)",
            )
        ],
    )


def _deployed(*, ok: bool = True) -> PreviewDeployment:
    return PreviewDeployment(
        found=True,
        deployment_id="dep-1",
        url="https://abc123.my-app.pages.dev",
        stage_name="deploy" if ok else "build",
        stage_status="success" if ok else "failure",
        terminal=True,
        succeeded=ok,
        matched_by="commit",
    )


async def _run(rec: Recorder, **overrides):
    inp = FeatureWorkflowInput(
        ticket_id=1,
        domain_name="frontend",
        domain_description="the UI",
        domain_paths=["src/**"],
        base_ref="main",
        llm_task_queue=TASK_QUEUE,
        skip_plan=True,
    )
    inp = replace(inp, **overrides)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        client: Client = env.client
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[FeatureWorkflow],
            activities=rec.activities(),
        ):
            return await client.execute_workflow(
                FeatureWorkflow.run,
                inp,
                id=f"wf-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )


async def test_passes_on_the_first_attempt():
    rec = Recorder()
    result = await _run(rec)

    assert result.verdict == VERDICT_PASSED
    assert result.e2e_attempts == 1
    assert rec.count("run_dev") == 1
    assert rec.count("push_branch") == 1
    assert rec.count("run_e2e") == 1
    assert rec.count("open_pr") == 1
    assert result.preview_url == "https://abc123.my-app.pages.dev"


async def test_fails_twice_then_passes_on_one_pr():
    rec = Recorder(e2e_script=[_e2e(passed=False), _e2e(passed=False), _e2e(passed=True)])
    result = await _run(rec)

    assert result.verdict == VERDICT_PASSED
    assert result.e2e_attempts == 3
    # One initial build + two repairs.
    assert rec.count("run_dev") == 3
    assert rec.count("push_branch") == 3
    assert rec.count("run_e2e") == 3
    # …but only ONE pull request for the whole loop.
    assert rec.count("open_pr") == 1


async def test_repair_prompt_carries_the_failure_detail():
    rec = Recorder(e2e_script=[_e2e(passed=False), _e2e(passed=True)])
    await _run(rec)

    repair = rec.dev_tasks[1]
    assert "Repair attempt 2 of 3" in repair
    assert "https://abc123.my-app.pages.dev" in repair
    assert "e2e/home.spec.ts:4" in repair
    assert "expect(locator).toBeVisible() failed" in repair
    # The anti-cheat rule has to survive prompt edits.
    assert "Do NOT delete, skip, or `.fixme()`" in repair
    # And the criteria themselves, so the agent can judge spec vs app.
    assert "Today's tasks" in repair


async def test_gives_up_after_the_attempt_cap():
    rec = Recorder(e2e_script=[_e2e(passed=False)])
    result = await _run(rec, max_e2e_attempts=2)

    assert result.verdict == VERDICT_FAILING
    assert result.e2e_attempts == 2
    assert rec.count("run_dev") == 2
    assert rec.count("run_e2e") == 2
    assert result.give_up_reason and "exhausted 2" in result.give_up_reason
    # A give-up still leaves a reviewable PR with a live preview on it.
    assert result.pr is not None and result.pr.pr_number == 11


async def test_no_acceptance_criteria_skips_the_whole_leg():
    rec = Recorder(body=BODY_WITHOUT_CRITERIA)
    result = await _run(rec)

    assert result.verdict == VERDICT_SKIPPED_NO_CRITERIA
    assert result.e2e_attempts == 0
    assert rec.count("run_dev") == 1
    assert rec.count("open_pr") == 1
    # None of the preview machinery is touched — this is the pre-0007 path.
    assert rec.count("load_preview_config") == 0
    assert rec.count("poll") == 0
    assert rec.count("run_e2e") == 0
    # And the dev agent isn't asked to write specs.
    assert "End-to-end tests" not in rec.dev_tasks[0]


async def test_repo_without_preview_config_skips_the_leg():
    rec = Recorder(preview=None)
    result = await _run(rec)

    assert result.verdict == VERDICT_SKIPPED_NO_CONFIG
    assert rec.count("load_preview_config") == 1
    assert rec.count("poll") == 0
    assert rec.count("run_e2e") == 0
    assert rec.count("open_pr") == 1


async def test_e2e_disabled_by_input_skips_the_leg():
    rec = Recorder()
    result = await _run(rec, e2e_enabled=False)

    assert result.verdict == VERDICT_SKIPPED_NO_CONFIG
    assert rec.count("load_preview_config") == 0
    assert rec.count("run_e2e") == 0


async def test_build_failure_feeds_the_logs_back_and_repairs():
    rec = Recorder(
        poll_script=[_deployed(ok=False), _deployed(ok=True)],
        e2e_script=[_e2e(passed=True)],
    )
    result = await _run(rec)

    assert result.verdict == VERDICT_PASSED
    assert rec.count("fetch_logs") == 1
    repair = rec.dev_tasks[1]
    assert "Cloudflare Pages build FAILED" in repair
    assert "error TS2304" in repair


async def test_build_failure_at_the_cap_reports_build_failed():
    rec = Recorder(poll_script=[_deployed(ok=False)])
    result = await _run(rec, max_e2e_attempts=1)

    assert result.verdict == VERDICT_BUILD_FAILED
    assert rec.count("run_e2e") == 0
    assert rec.count("run_dev") == 1  # no repair — the cap was 1


async def test_preview_timeout_never_spends_a_repair_run():
    """Cloudflare being slow isn't something an LLM can fix."""
    rec = Recorder(poll_script=[PreviewDeployment(found=False)])
    result = await _run(rec)

    assert result.verdict == VERDICT_TIMED_OUT
    assert rec.count("run_e2e") == 0
    assert rec.count("run_dev") == 1  # the initial build only
    assert result.give_up_reason and "no preview deployment appeared" in (result.give_up_reason)


async def test_budget_exhaustion_stops_the_loop_immediately():
    rec = Recorder(
        e2e_script=[_e2e(passed=False)],
        dev_script=[_dev(success=True), _dev(success=False, stop_reason="budget_exhausted")],
    )
    result = await _run(rec, max_e2e_attempts=3)

    assert rec.count("run_dev") == 2  # stopped well before the cap
    assert result.give_up_reason and "cost ceiling" in result.give_up_reason


async def test_repair_that_commits_nothing_stops_the_loop():
    """Same tip → the next deploy+test would be byte-identical."""
    same = PushBranchResult(
        pushed=True, commits_ahead=1, head_sha="a" * 40, owner="me", repo="my-app"
    )
    rec = Recorder(e2e_script=[_e2e(passed=False)], push_script=[same])
    result = await _run(rec, max_e2e_attempts=5)

    assert result.verdict == VERDICT_FAILING
    assert result.give_up_reason == "repair run produced no new commit"
    # Attempt 1 ran; the second push produced no new SHA, so we stopped.
    assert rec.count("run_e2e") == 1


async def test_nothing_committed_means_no_pr_and_no_preview():
    rec = Recorder(
        push_script=[
            PushBranchResult(
                pushed=False,
                commits_ahead=0,
                skipped_reason="dev agent didn't commit anything",
            )
        ]
    )
    result = await _run(rec)

    assert rec.count("open_pr") == 0
    assert rec.count("poll") == 0
    assert result.pr is None
    assert result.give_up_reason == "dev agent didn't commit anything"


async def test_failed_dev_run_short_circuits_before_pushing():
    rec = Recorder(dev_script=[_dev(success=False)])
    await _run(rec)

    assert rec.count("push_branch") == 0
    assert rec.count("open_pr") == 0
    assert "failed" in rec.statuses


async def test_pr_is_a_draft_while_e2e_is_unproven():
    rec = Recorder()
    await _run(rec)
    assert rec.open_pr_calls[0].draft is True


async def test_pr_defers_to_settings_when_e2e_is_off():
    rec = Recorder(body=BODY_WITHOUT_CRITERIA)
    await _run(rec)
    assert rec.open_pr_calls[0].draft is None


async def test_dev_runs_are_non_terminal_until_the_loop_ends():
    """Mid-loop run_finished events must not close the live SSE stream."""
    rec = Recorder(e2e_script=[_e2e(passed=False), _e2e(passed=True)])
    await _run(rec)
    assert rec.dev_terminal == [False, False]
    assert rec.dev_iterations == [1, 2]


async def test_dev_run_is_terminal_when_the_leg_is_off():
    rec = Recorder(body=BODY_WITHOUT_CRITERIA)
    await _run(rec)
    assert rec.dev_terminal == [True]


async def test_verdict_and_preview_url_are_persisted():
    rec = Recorder()
    await _run(rec)
    assert rec.recorded_outcomes
    last = rec.recorded_outcomes[-1]
    assert last.e2e_verdict == VERDICT_PASSED
    assert last.preview_url == "https://abc123.my-app.pages.dev"


async def test_a_red_suite_still_reports_the_pr_as_open():
    """The verdict is a separate axis from the workflow status: the PR
    opens (red suite or not), and the ticket then closes on merge."""
    rec = Recorder(e2e_script=[_e2e(passed=False)])
    await _run(rec, max_e2e_attempts=1)
    assert "pr_open" in rec.statuses
    # The default mock merges the PR on the first poll — the wait-for-
    # merge loop must flip the ticket to merged, not leave it at pr_open.
    assert rec.statuses[-1] == "merged"


async def test_pr_closed_without_merge_cancels_the_ticket():
    rec = Recorder(pr_state_script=["open", "closed"])
    await _run(rec, max_e2e_attempts=1)
    assert "pr_open" in rec.statuses
    assert rec.statuses[-1] == "cancelled"
    assert "pr_closed" in rec.events


async def test_missing_specs_are_fed_back_as_a_repair_signal():
    rec = Recorder(
        e2e_script=[
            replace(
                _e2e(passed=False, ran=False),
                error="no `e2e/` directory in the branch — the dev agent did not write any end-to-end specs.",
            ),
            _e2e(passed=True),
        ]
    )
    result = await _run(rec)

    assert result.verdict == VERDICT_PASSED
    repair = rec.dev_tasks[1]
    assert "could not run" in repair
    assert "did not write any end-to-end specs" in repair
