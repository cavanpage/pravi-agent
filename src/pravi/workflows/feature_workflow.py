"""Per-ticket FeatureWorkflow — the durable, human-in-the-loop core.

Lifecycle (Slice 1B today; tester/reviewer/PR steps land in 1C/Slice 2):

  1. Load the ticket from Postgres.
  2. Wait for the architect to send `approve_plan(plan_id)` (signal).
  3. Load the approved plan.
  4. Create a per-ticket worktree.
  5. Run the developer agent with the plan as its task (LLM queue).
  6. Optionally cleanup. (PR open + reviewer come in 1C / Slice 2.)

Status is exposed via `@workflow.query current_status()` so the CLI can
introspect from the outside.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pravi.activities.db_activity import (
        EmitEventRequest,
        PlanData,
        SynthesizePlanRequest,
        TicketRef,
        TicketStatusUpdate,
        emit_ticket_event,
        load_plan,
        load_ticket,
        synthesize_plan_from_body,
        update_ticket_status,
    )
    from pravi.activities.dev_activity import (
        DevActivityRequest,
        DevActivityResult,
        run_dev,
    )
    from pravi.activities.e2e_activity import (
        RunE2ERequest,
        RunE2EResult,
        run_e2e,
    )
    from pravi.activities.pr_activity import (
        CheckPRStateRequest,
        CheckPRStateResult,
        OpenPRRequest,
        OpenPRResult,
        PushBranchRequest,
        PushBranchResult,
        check_pr_state,
        open_pr,
        push_branch,
    )
    from pravi.activities.preview_activity import (
        DeploymentLogsRequest,
        LoadPreviewConfigRequest,
        PollPreviewRequest,
        PreviewDeployment,
        PreviewSnapshot,
        RecordPreviewOutcomeRequest,
        fetch_deployment_logs,
        load_preview_config,
        poll_preview_deployment,
        record_preview_outcome,
    )
    from pravi.activities.sandbox_activity import (
        CleanupRequest,
        ProvisionRequest,
        cleanup_sandbox,
        provision_sandbox,
    )
    from pravi.agents.sandbox.protocols import SandboxHandle
    from pravi.events import (
        KIND_PREVIEW_FAILED,
        KIND_PREVIEW_READY,
        KIND_PREVIEW_WAITING,
        KIND_REPAIR_STARTED,
    )
    from pravi.specs.acceptance import extract_acceptance_criteria


# Statuses surfaced via @workflow.query — keep these short, the CLI displays them.
STATUS_LOADING = "loading_ticket"
STATUS_WAITING_FOR_PLAN = "waiting_for_plan"
STATUS_RUNNING_DEV = "running_dev"
STATUS_PUSHING = "pushing"
STATUS_AWAITING_PREVIEW = "awaiting_preview"
STATUS_RUNNING_E2E = "running_e2e"
STATUS_REPAIRING = "repairing"
STATUS_AWAITING_MERGE = "awaiting_merge"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

# End-to-end verdicts. A separate axis from TicketStatus on purpose:
# "the PR is open" and "the tests are red" are independent facts, and
# folding them together would ripple into parent-status derivation, the
# roadmap waves, and the UI colour maps for no gain.
VERDICT_SKIPPED_NO_CRITERIA = "skipped_no_criteria"
VERDICT_SKIPPED_NO_CONFIG = "skipped_no_config"
VERDICT_NOT_RUN = "not_run"
VERDICT_PASSED = "passed"
VERDICT_FAILING = "failing"
VERDICT_BUILD_FAILED = "build_failed"
VERDICT_TIMED_OUT = "timed_out"


@dataclass
class FeatureWorkflowInput:
    ticket_id: int
    domain_name: str
    domain_description: str
    domain_paths: list[str]
    base_ref: str
    llm_task_queue: str
    cleanup_worktree: bool = False
    # When True, skip the wait-for-plan signal and synthesize a Plan row
    # from the ticket body instead. Used by the "start all nested" flow
    # at feature/epic level — decomposed tasks already carry their per-
    # task description (the architect's output at decompose time), so re-
    # running the architect at task level is duplicate work. Review
    # happens at PR time instead of plan-approve time.
    skip_plan: bool = False
    # Deploy → e2e → repair loop (ADR 0007). Both defaulted so existing
    # callers and in-flight workflows are untouched. The leg additionally
    # requires a `preview:` block in the repo and acceptance criteria on
    # the ticket, so `True` here is permission, not a guarantee.
    e2e_enabled: bool = True
    max_e2e_attempts: int = 3


@dataclass
class FeatureWorkflowResult:
    ticket_id: int
    plan_id: int | None
    sandbox_id: str | None  # opaque per-backend; "where did the work happen"
    branch: str | None
    dev: DevActivityResult | None
    pr: OpenPRResult | None
    summary: str
    preview_url: str | None = None
    preview_deployment_id: str | None = None
    e2e: RunE2EResult | None = None
    e2e_attempts: int = 0
    verdict: str = VERDICT_NOT_RUN
    give_up_reason: str | None = None


@workflow.defn
class FeatureWorkflow:
    def __init__(self) -> None:
        self._plan_id: int | None = None
        self._status: str = STATUS_LOADING
        self._cancel_requested: bool = False

    @workflow.signal
    async def approve_plan(self, plan_id: int) -> None:
        """Architect signals an approved Plan row's ID. Idempotent: first wins."""
        if self._plan_id is None:
            self._plan_id = plan_id

    @workflow.signal
    async def cancel(self) -> None:
        """Operator escape hatch — bail out of the wait_condition cleanly."""
        self._cancel_requested = True

    @workflow.query
    def current_status(self) -> str:
        return self._status

    @workflow.query
    def plan_id(self) -> int | None:
        return self._plan_id

    @workflow.run
    async def run(self, inp: FeatureWorkflowInput) -> FeatureWorkflowResult:
        self._status = STATUS_LOADING
        ticket: TicketRef = await workflow.execute_activity(
            load_ticket,
            inp.ticket_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        await workflow.execute_activity(
            update_ticket_status,
            TicketStatusUpdate(
                ticket_id=ticket.ticket_id,
                status="planning",
                workflow_id=workflow.info().workflow_id,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        plan: PlanData
        if inp.skip_plan:
            # Auto-synthesize a Plan from the ticket body. No architect
            # call, no human plan-approve gate — review happens at PR time.
            self._status = STATUS_WAITING_FOR_PLAN  # transient; reused for UI
            plan = await workflow.execute_activity(
                synthesize_plan_from_body,
                SynthesizePlanRequest(
                    ticket_id=ticket.ticket_id,
                    domain_name=inp.domain_name,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            self._plan_id = plan.plan_id
        else:
            # Block until the architect sends approve_plan(plan_id) — or cancel.
            self._status = STATUS_WAITING_FOR_PLAN
            await workflow.wait_condition(
                lambda: self._plan_id is not None or self._cancel_requested
            )

            if self._cancel_requested:
                self._status = STATUS_CANCELLED
                await workflow.execute_activity(
                    update_ticket_status,
                    TicketStatusUpdate(ticket_id=ticket.ticket_id, status="cancelled"),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                return FeatureWorkflowResult(
                    ticket_id=ticket.ticket_id,
                    plan_id=None,
                    sandbox_id=None,
                    branch=None,
                    dev=None,
                    pr=None,
                    summary="cancelled before plan",
                )

            plan = await workflow.execute_activity(
                load_plan,
                self._plan_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        await workflow.execute_activity(
            update_ticket_status,
            TicketStatusUpdate(ticket_id=ticket.ticket_id, status="plan_approved"),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # Acceptance criteria drive the e2e leg (ADR 0007). They live in
        # the ticket body; the architect-drafted path puts them in the plan
        # instead, so check both. Empty is the normal case for every
        # pre-0007 ticket and turns the whole leg off.
        criteria = extract_acceptance_criteria(
            ticket.ancestral_body_md or ticket.body
        ) or extract_acceptance_criteria(plan.content_md)

        self._status = STATUS_RUNNING_DEV
        branch = f"pravi/{ticket.external_id}-{plan.domain_name}"
        handle: SandboxHandle = await workflow.execute_activity(
            provision_sandbox,
            ProvisionRequest(
                repo_id=ticket.repo_id,
                ticket_external_id=str(ticket.external_id),
                branch=branch,
                base_ref=inp.base_ref,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        # Is the e2e leg live for this run? Three independent off-switches:
        # the caller's flag, the repo's `preview:` block, and whether the
        # spec actually carries acceptance criteria.
        preview: PreviewSnapshot | None = None
        if inp.e2e_enabled and criteria:
            preview = await workflow.execute_activity(
                load_preview_config,
                LoadPreviewConfigRequest(handle=handle, repo_id=ticket.repo_id),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        e2e_active = bool(preview and criteria)

        await workflow.execute_activity(
            update_ticket_status,
            TicketStatusUpdate(ticket_id=ticket.ticket_id, status="in_progress"),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        dev_result: DevActivityResult | None = await self._run_dev(
            inp=inp,
            ticket=ticket,
            plan=plan,
            handle=handle,
            task=_build_dev_task(ticket=ticket, plan=plan),
            criteria=criteria,
            preview=preview,
            iteration=1,
            # The stream must stay open while the loop may still run.
            terminal=not e2e_active,
        )

        if not (dev_result and dev_result.success):
            return await self._finish(
                ticket=ticket,
                plan=plan,
                handle=handle,
                inp=inp,
                dev_result=dev_result,
                pr_result=None,
                final_status="failed",
                verdict=(VERDICT_NOT_RUN if e2e_active else VERDICT_SKIPPED_NO_CRITERIA),
            )

        # --- push → deploy → verify → repair ----------------------------
        if not criteria:
            verdict = VERDICT_SKIPPED_NO_CRITERIA
        elif not e2e_active:
            verdict = VERDICT_SKIPPED_NO_CONFIG
        else:
            verdict = VERDICT_NOT_RUN

        give_up_reason: str | None = None
        pr_result: OpenPRResult | None = None
        pr_owner: str | None = None
        pr_repo: str | None = None
        e2e_result: RunE2EResult | None = None
        deployment: PreviewDeployment | None = None
        preview_url: str | None = None
        last_pushed_sha: str | None = None
        attempt = 1

        while True:
            self._status = STATUS_PUSHING
            push: PushBranchResult = await workflow.execute_activity(
                push_branch,
                PushBranchRequest(
                    ticket_external_id=ticket.external_id,
                    handle=handle,
                    base_ref=inp.base_ref,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            if not push.pushed:
                give_up_reason = push.skipped_reason or push.error
                break

            # The PR opens once, right after the first successful push. The
            # branch is already public by then (Cloudflare can't build it
            # otherwise), so the PR is free — and it means a give-up still
            # leaves a reviewable diff with the preview URL on it, rather
            # than today's bare `in_progress`. Draft while e2e is unproven.
            if pr_result is None and push.owner and push.repo:
                pr_owner, pr_repo = push.owner, push.repo
                pr_result = await workflow.execute_activity(
                    open_pr,
                    OpenPRRequest(
                        ticket_id=ticket.ticket_id,
                        ticket_title=ticket.title,
                        owner=push.owner,
                        repo=push.repo,
                        head_branch=handle.branch,
                        base_ref=inp.base_ref,
                        pr_body=_build_pr_body(ticket=ticket, plan=plan, criteria=criteria),
                        draft=True if e2e_active else None,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )

            if not e2e_active or preview is None:
                break

            # A repair run that committed nothing leaves the same tip, so
            # the next deploy+test would be byte-identical. Stop rather
            # than burn the remaining attempts re-proving it.
            if push.head_sha and push.head_sha == last_pushed_sha:
                verdict = VERDICT_FAILING
                give_up_reason = "repair run produced no new commit"
                break
            last_pushed_sha = push.head_sha

            self._status = STATUS_AWAITING_PREVIEW
            deployment = await self._await_preview(
                ticket_id=ticket.ticket_id,
                project=preview.project,
                commit_sha=push.head_sha or "",
                branch=handle.branch,
                wait_timeout_s=preview.wait_timeout_seconds,
                grace_s=preview.first_deployment_grace_seconds,
            )

            if not deployment.succeeded:
                # Two non-repairable shapes, both of which mean "we never
                # got a URL, and it isn't the agent's code":
                #   - not terminal → Cloudflare was slow, we ran out of wait
                #   - terminal but never found → no build was ever triggered
                #     for this commit (project not git-connected, preview
                #     builds disabled, wrong project name)
                # Neither is something an LLM repair can fix, so don't
                # spend an attempt — and the real cause is a config change
                # the human has to make.
                if not deployment.terminal or not deployment.found:
                    verdict = VERDICT_TIMED_OUT
                    give_up_reason = deployment.error or "preview build timed out"
                    break
                logs = "(no deployment to fetch logs for)"
                if deployment.deployment_id:
                    logs = await workflow.execute_activity(
                        fetch_deployment_logs,
                        DeploymentLogsRequest(
                            project=preview.project,
                            deployment_id=deployment.deployment_id,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                verdict = VERDICT_BUILD_FAILED
                feedback = _build_deploy_failure_feedback(
                    deployment=deployment,
                    logs=logs,
                    # Numbered by the attempt this repair will PRODUCE, so
                    # it lines up with the cap the agent is told about.
                    next_attempt=attempt + 1,
                    max_attempts=inp.max_e2e_attempts,
                )
            else:
                preview_url = deployment.url or deployment.alias_url
                self._status = STATUS_RUNNING_E2E
                e2e_result = await workflow.execute_activity(
                    run_e2e,
                    RunE2ERequest(
                        ticket_id=ticket.ticket_id,
                        handle=handle,
                        base_url=preview_url or "",
                        preview=preview,
                        attempt=attempt,
                    ),
                    start_to_close_timeout=_e2e_activity_timeout(preview),
                    heartbeat_timeout=timedelta(minutes=2),
                    # Never auto-retried: it would double the wall clock and
                    # mask real failures. Flakiness is Playwright's `retries`.
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                if e2e_result.passed:
                    verdict = VERDICT_PASSED
                    break
                verdict = VERDICT_FAILING
                feedback = _build_e2e_failure_feedback(
                    result=e2e_result,
                    preview_url=preview_url or "(unknown)",
                    criteria=criteria,
                    e2e_dir=preview.e2e_dir,
                    next_attempt=attempt + 1,
                    max_attempts=inp.max_e2e_attempts,
                )

            if attempt >= inp.max_e2e_attempts:
                give_up_reason = f"exhausted {inp.max_e2e_attempts} attempt(s) without a green run"
                break

            attempt += 1
            self._status = STATUS_REPAIRING
            await self._emit(
                ticket.ticket_id,
                KIND_REPAIR_STARTED,
                f"repair attempt {attempt} of {inp.max_e2e_attempts}",
                {
                    "attempt": attempt,
                    "max_attempts": inp.max_e2e_attempts,
                    "reason": verdict,
                },
            )
            dev_result = await self._run_dev(
                inp=inp,
                ticket=ticket,
                plan=plan,
                handle=handle,
                task=feedback,
                criteria=criteria,
                preview=preview,
                iteration=attempt,
                terminal=False,
            )
            if dev_result is None:
                give_up_reason = "repair run produced no result"
                break
            if dev_result.stop_reason == "budget_exhausted":
                give_up_reason = (
                    "cost ceiling reached mid-repair — raise cost_ceiling_usd "
                    "on the ticket or an ancestor and re-run"
                )
                break
            if not dev_result.success:
                give_up_reason = f"repair run failed: {dev_result.summary[:200]}"
                break

        # `pr_open` once a PR exists, regardless of the e2e verdict — a red
        # suite is reported through `e2e_verdict`, not by pretending the PR
        # isn't there.
        if pr_result and pr_result.pr_number is not None:
            final_status = "pr_open"
        else:
            final_status = "in_progress"

        result = await self._finish(
            ticket=ticket,
            plan=plan,
            handle=handle,
            inp=inp,
            dev_result=dev_result,
            pr_result=pr_result,
            final_status=final_status,
            verdict=verdict,
            e2e_result=e2e_result,
            e2e_attempts=attempt if e2e_active else 0,
            preview_url=preview_url,
            deployment_id=deployment.deployment_id if deployment else None,
            give_up_reason=give_up_reason,
        )

        # A ticket isn't done when the PR opens — it's done when the PR
        # merges. Keep the workflow alive (durable timers, cheap polls)
        # until the human merges or closes it, then flip the status.
        if (
            final_status == "pr_open"
            and pr_result is not None
            and pr_result.pr_number is not None
            and pr_owner
            and pr_repo
        ):
            await self._await_merge(
                ticket_id=ticket.ticket_id,
                owner=pr_owner,
                repo=pr_repo,
                pr_number=pr_result.pr_number,
                handle=handle,
            )

        return result

    # ---- helpers -------------------------------------------------------

    async def _emit(
        self, ticket_id: int, kind: str, message: str, payload: dict | None = None
    ) -> None:
        """Fire-and-forget telemetry. Never fail a ticket over a log line."""
        await workflow.execute_activity(
            emit_ticket_event,
            EmitEventRequest(ticket_id=ticket_id, kind=kind, message=message, payload=payload),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

    async def _await_merge(
        self,
        *,
        ticket_id: int,
        owner: str,
        repo: str,
        pr_number: int,
        handle: SandboxHandle,
    ) -> None:
        """Durable wait for the human review gate: the ticket closes when
        the PR merges, not when it opens.

        Polls GitHub via `check_pr_state` with a backing-off Temporal
        timer (pure counter — replay-safe). On merge → status `merged`;
        on close-without-merge → `cancelled`. After ~7 days of polling we
        stop and leave the ticket at `pr_open` — a stale PR shouldn't
        keep a workflow alive forever, and the status can still be fixed
        by re-running or manually.

        However it ends, the per-ticket worktree is reaped on the way out.
        """
        self._status = STATUS_AWAITING_MERGE
        settled = False
        for poll in range(_MERGE_MAX_POLLS):
            await workflow.sleep(_merge_poll_backoff(poll))
            state: CheckPRStateResult = await workflow.execute_activity(
                check_pr_state,
                CheckPRStateRequest(owner=owner, repo=repo, pr_number=pr_number),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            if state.state == "merged":
                await workflow.execute_activity(
                    update_ticket_status,
                    TicketStatusUpdate(ticket_id=ticket_id, status="merged"),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                await self._emit(
                    ticket_id,
                    "pr_merged",
                    f"PR #{pr_number} merged — ticket closed",
                    {"pr_number": pr_number, "owner": owner, "repo": repo},
                )
                settled = True
                break
            if state.state == "closed":
                await workflow.execute_activity(
                    update_ticket_status,
                    TicketStatusUpdate(ticket_id=ticket_id, status="cancelled"),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                await self._emit(
                    ticket_id,
                    "pr_closed",
                    f"PR #{pr_number} closed without merging — ticket cancelled",
                    {"pr_number": pr_number, "owner": owner, "repo": repo},
                )
                settled = True
                break
            # "open" and "unknown" both mean keep waiting.
        if not settled:
            await self._emit(
                ticket_id,
                "merge_watch_expired",
                f"stopped watching PR #{pr_number} after ~7 days; still open",
                {"pr_number": pr_number},
            )
        await self._reap_worktree(handle)

    async def _reap_worktree(self, handle: SandboxHandle) -> None:
        """Delete the per-ticket worktree now that the PR has settled.

        This is the first provably-safe moment to do it: until the PR is
        merged or closed, a repair run may still need to push from this
        worktree. Before the merge watch existed the workflow simply
        ended at `pr_open`, so worktrees accumulated on the worker host
        indefinitely.

        The REMOTE branch is not touched here — `cleanup` can't delete it
        post-handle, and GitHub reaps it itself via `delete_branch_on_merge`
        (set on repos pravi creates). Best-effort: a failed reap must never
        fail an otherwise-successful ticket, and `cleanup_sandbox` is
        idempotent, so double-reaping an already-cleaned worktree is fine.
        """
        try:
            await workflow.execute_activity(
                cleanup_sandbox,
                CleanupRequest(handle=handle, delete_branch=False),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except Exception as e:  # noqa: BLE001 — cleanup is never fatal
            workflow.logger.warning("worktree reap failed; leaving it on disk: %s", e)

    async def _run_dev(
        self,
        *,
        inp: FeatureWorkflowInput,
        ticket: TicketRef,
        plan: PlanData,
        handle: SandboxHandle,
        task: str,
        criteria: list[str],
        preview: PreviewSnapshot | None,
        iteration: int,
        terminal: bool,
    ) -> DevActivityResult | None:
        self._status = STATUS_RUNNING_DEV if iteration == 1 else STATUS_REPAIRING
        dev_req = DevActivityRequest(
            cwd=handle.cwd,
            repo_name=ticket.repo_name,
            domain_name=plan.domain_name,
            domain_description=inp.domain_description,
            # The dev prompt hard-scopes writes to the domain's paths, and
            # `e2e/**` belongs to no domain. Widen it here rather than
            # making every repo's domains.yaml repeat the pattern — and
            # only for runs that actually author specs.
            domain_paths=_writable_paths(inp.domain_paths, criteria),
            task=task,
            # Lets the activity persist a Run row + push live events on the
            # per-ticket NOTIFY channel for <LiveRunPanel>.
            ticket_id=ticket.ticket_id,
            # Persona + stack framing — see ADR 0004. Null on each →
            # generic dev prompt (today's behavior).
            persona=ticket.persona,
            stack=ticket.stack,
            # ADR 0007 — non-empty asks the agent to also author Playwright
            # specs. Empty leaves the prompt byte-identical to dev/v2.
            acceptance_criteria=criteria,
            e2e_dir=preview.e2e_dir if preview else "e2e",
            e2e_base_url_env=preview.e2e_base_url_env if preview else "E2E_BASE_URL",
            iteration=iteration,
            terminal=terminal,
        )
        return await workflow.execute_activity(
            run_dev,
            dev_req,
            task_queue=inp.llm_task_queue,
            start_to_close_timeout=timedelta(hours=1),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

    async def _await_preview(
        self,
        *,
        ticket_id: int,
        project: str,
        commit_sha: str,
        branch: str,
        wait_timeout_s: int,
        grace_s: int,
    ) -> PreviewDeployment:
        """Wait for Cloudflare to build `commit_sha`, polling on a timer.

        A timer loop rather than one long heartbeating activity: if the
        worker dies at minute 12 of a 15-minute build, this resumes at the
        next poll with every prior result already in workflow history,
        whereas an activity would restart its whole timeout budget. It
        also keeps a worker slot free and makes each poll visible in the
        Temporal UI. Only workflow primitives are awaited, so it replays
        exactly.
        """
        deadline = workflow.now() + timedelta(seconds=wait_timeout_s)
        grace_deadline = workflow.now() + timedelta(seconds=grace_s)
        polls = 0
        last = PreviewDeployment(found=False)

        while workflow.now() < deadline:
            last = await workflow.execute_activity(
                poll_preview_deployment,
                PollPreviewRequest(project=project, commit_sha=commit_sha, branch=branch),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
            )
            if last.terminal:
                if last.succeeded:
                    await self._emit(
                        ticket_id,
                        KIND_PREVIEW_READY,
                        f"preview ready at {last.url}",
                        {
                            "url": last.url,
                            "deployment_id": last.deployment_id,
                            "matched_by": last.matched_by,
                        },
                    )
                else:
                    await self._emit(
                        ticket_id,
                        KIND_PREVIEW_FAILED,
                        f"preview build failed at stage {last.stage_name}",
                        {
                            "stage": last.stage_name,
                            "status": last.stage_status,
                            "error": last.error,
                        },
                    )
                return last

            if not last.found and workflow.now() >= grace_deadline:
                # Cloudflare never registered a build for this commit.
                # Almost always: the project isn't git-connected, preview
                # builds are off, or the project name is wrong.
                last.terminal = True
                last.succeeded = False
                last.error = (
                    f"no preview deployment appeared for commit {commit_sha[:8]} "
                    f"on project {project!r} within {grace_s}s. Check that the "
                    "Pages project is connected to this GitHub repo and that "
                    "preview deployments are enabled for all branches."
                )
                await self._emit(
                    ticket_id,
                    KIND_PREVIEW_FAILED,
                    "no preview deployment appeared",
                    {"project": project, "error": last.error},
                )
                return last

            polls += 1
            if polls == 1 or polls % 4 == 0:
                await self._emit(
                    ticket_id,
                    KIND_PREVIEW_WAITING,
                    f"waiting for the preview build ({last.stage_name or 'queued'})",
                    {
                        "stage": last.stage_name,
                        "status": last.stage_status,
                        "poll": polls,
                    },
                )
            await workflow.sleep(_poll_backoff(polls))

        last.terminal = False
        last.succeeded = False
        last.error = f"preview build did not finish within {wait_timeout_s}s"
        return last

    async def _finish(
        self,
        *,
        ticket: TicketRef,
        plan: PlanData,
        handle: SandboxHandle,
        inp: FeatureWorkflowInput,
        dev_result: DevActivityResult | None,
        pr_result: OpenPRResult | None,
        final_status: str,
        verdict: str,
        e2e_result: RunE2EResult | None = None,
        e2e_attempts: int = 0,
        preview_url: str | None = None,
        deployment_id: str | None = None,
        give_up_reason: str | None = None,
    ) -> FeatureWorkflowResult:
        """Teardown + writeback, shared by every exit path."""
        if preview_url or verdict != VERDICT_NOT_RUN:
            await workflow.execute_activity(
                record_preview_outcome,
                RecordPreviewOutcomeRequest(
                    ticket_id=ticket.ticket_id,
                    preview_url=preview_url,
                    e2e_verdict=verdict,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        # The sandbox has been used for every push by now; safe to tear down.
        if inp.cleanup_worktree:
            await workflow.execute_activity(
                cleanup_sandbox,
                CleanupRequest(handle=handle, delete_branch=False),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        await workflow.execute_activity(
            update_ticket_status,
            TicketStatusUpdate(ticket_id=ticket.ticket_id, status=final_status),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        self._status = STATUS_DONE

        return FeatureWorkflowResult(
            ticket_id=ticket.ticket_id,
            plan_id=plan.plan_id,
            sandbox_id=handle.sandbox_id,
            branch=handle.branch,
            dev=dev_result,
            pr=pr_result,
            summary=(dev_result.summary if dev_result else "no dev result"),
            preview_url=preview_url,
            preview_deployment_id=deployment_id,
            e2e=e2e_result,
            e2e_attempts=e2e_attempts,
            verdict=verdict,
            give_up_reason=give_up_reason,
        )


# Paths the agent may write when it's authoring e2e specs, on top of its
# domain's own. Kept module-level and pure so it's replay-safe.
E2E_WRITE_PATHS = ("e2e/**", "playwright.config.ts")

# How many failing tests to quote in a repair prompt. Past a handful the
# extra failures are noise (usually the same root cause) and just cost
# tokens; the full set lives on the tester Run row and in the UI.
MAX_QUOTED_FAILURES = 4


# Wait-for-merge pacing: 1-minute polls for the first 15 minutes (fresh
# PRs get reviewed fast in a demo loop), 5-minute polls for the rest of
# the first ~5 hours, then 15-minute polls. 720 polls ≈ 7 days total.
_MERGE_MAX_POLLS = 720


def _merge_poll_backoff(polls: int) -> int:
    """Seconds before merge-poll N+1. Pure function of the counter —
    no clock, no randomness — so workflow replay is exact."""
    if polls < 15:
        return 60
    if polls < 72:
        return 300
    return 900


def _poll_backoff(polls: int) -> int:
    """Seconds to wait before poll N+1.

    Cloudflare builds a Vite app in roughly 60–110s, so poll fast early
    and back off for the long tail. Pure function of the counter — no
    clock, no randomness — so workflow replay is exact.
    """
    if polls <= 6:
        return 5
    if polls <= 15:
        return 15
    return 30


def _e2e_activity_timeout(preview: PreviewSnapshot) -> timedelta:
    """Wall clock for one `run_e2e`.

    The activity can chain up to four bounded phases: `npm ci`, an `npm
    install` fallback when the lockfile is stale, the browser download, and
    the suite itself. The first three are each capped at
    `e2e_install_timeout_seconds`, which the workflow can't read (settings
    live worker-side), so budget for its default with room to spare rather
    than risk tripping start_to_close on a cold worktree.

    `heartbeat_timeout` is the real liveness guard; this is just a ceiling.
    """
    return timedelta(seconds=preview.e2e_timeout_seconds + 4 * 900)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{n}. {c}" for n, c in enumerate(items, start=1))


def _build_e2e_failure_feedback(
    *,
    result: RunE2EResult,
    preview_url: str,
    criteria: list[str],
    e2e_dir: str,
    next_attempt: int,
    max_attempts: int,
) -> str:
    """The repair prompt: what broke, where, and the rules for fixing it.

    The hard rule is "don't delete the test to go green" — an agent
    optimizing for a passing suite will otherwise find that shortcut.
    """
    header = (
        f"# Repair attempt {next_attempt} of {max_attempts}\n\n"
        f"Your previous commit was pushed and deployed to a Cloudflare Pages "
        f"preview at {preview_url}. The end-to-end acceptance tests ran "
        f"against that live deployment and did NOT pass.\n\n"
        f"## Acceptance criteria for this task\n\n{_numbered(criteria)}\n"
    )

    if not result.ran:
        body = (
            f"\n## The suite could not run (stage: {result.stage})\n\n"
            f"{result.error or 'unknown error'}\n"
        )
    else:
        quoted = result.failures[:MAX_QUOTED_FAILURES]
        blocks: list[str] = [f"\n## Failing tests ({result.failed_count} of {result.total})\n"]
        for f in quoted:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            blocks.append(f"\n### {loc} — {f.title}\n\n```\n{f.message}\n```\n")
            if f.snippet:
                blocks.append(f"\n```\n{f.snippet}\n```\n")
        remaining = len(result.failures) - len(quoted)
        if remaining > 0:
            blocks.append(
                f"\n(…{remaining} more failure(s) omitted — see the tester run "
                f"for the full report.)\n"
            )
        body = "".join(blocks)

    instructions = (
        f"\n## What to do\n\n"
        f"1. Open the failing spec(s) under `{e2e_dir}/` and the application "
        f"code they exercise. The preview at {preview_url} is the exact build "
        f"that failed — reason about what it actually rendered.\n"
        f"2. Decide whether the APPLICATION is wrong or the SPEC is wrong. "
        f"Default to fixing the application. Only change a spec when it does "
        f"not faithfully encode the acceptance criterion above.\n"
        f"3. Do NOT delete, skip, or `.fixme()` a test to make the suite pass. "
        f"A green run achieved that way is a worse outcome than a red one.\n"
        f"4. Do NOT touch files unrelated to these failures.\n"
        f"5. COMMIT your fix. The branch is re-pushed, re-deployed, and "
        f"re-tested automatically — you do not need to do any of that "
        f"yourself.\n"
    )
    return header + body + instructions


def _build_deploy_failure_feedback(
    *,
    deployment: PreviewDeployment,
    logs: str,
    next_attempt: int,
    max_attempts: int,
) -> str:
    """Repair prompt for a build that never produced a testable URL."""
    return (
        f"# Repair attempt {next_attempt} of {max_attempts}\n\n"
        f"## The Cloudflare Pages build FAILED\n\n"
        f"Your commit was pushed, but the deploy never produced a working "
        f"URL, so no acceptance tests could run.\n\n"
        f"Stage: `{deployment.stage_name}` → `{deployment.stage_status}`\n\n"
        f"Build log tail:\n\n```\n{logs}\n```\n\n"
        f"## What to do\n\n"
        f"Fix whatever broke the build — most often a TypeScript error, a "
        f"missing dependency, or a bad import path. Then COMMIT the fix; the "
        f"branch is re-pushed, rebuilt, and re-tested automatically.\n"
    )


def _writable_paths(domain_paths: list[str], criteria: list[str]) -> list[str]:
    """Domain paths, widened to cover the e2e dir when specs are expected."""
    paths = list(domain_paths)
    if not criteria:
        return paths
    for extra in E2E_WRITE_PATHS:
        if extra not in paths:
            paths.append(extra)
    return paths


def _build_dev_task(*, ticket: TicketRef, plan: PlanData) -> str:
    """Compose the user prompt the dev agent receives.

    The plan is authoritative — the ticket (with epic/feature ancestry merged
    in by `load_ticket`) is included for traceability. We explicitly ask the
    agent to commit (one or more commits — its judgement) so the follow-up
    push activity has something to ship.
    """
    body_md = ticket.ancestral_body_md or ticket.body or "(no description)"
    return (
        f"# Ticket: {ticket.title}\n\n"
        f"External ID: {ticket.external_id}\n\n"
        f"{body_md}\n\n"
        f"---\n\n"
        f"# Approved plan\n\n"
        f"{plan.content_md}\n\n"
        f"---\n\n"
        f"Implement the plan above. Stay inside the domain's allowed paths.\n\n"
        f"When finished, commit your work with descriptive messages — one or "
        f"more commits, your call. A follow-up step will push the branch and "
        f"open a draft PR. If you leave the worktree uncommitted, no PR will "
        f"be opened."
    )


def _build_pr_body(*, ticket: TicketRef, plan: PlanData, criteria: list[str] | None = None) -> str:
    """Markdown body for the GitHub PR."""
    body = ticket.body or "(no description)"
    criteria_block = ""
    if criteria:
        # Reviewers get the same checklist the tests were generated from,
        # so "is this actually done?" is answerable without reading specs.
        items = "\n".join(f"- [ ] {c}" for c in criteria)
        criteria_block = f"### Acceptance criteria (verified end-to-end)\n\n{items}\n\n---\n\n"
    return (
        f"### Ticket\n\n"
        f"**{ticket.title}** ({ticket.external_id})\n\n"
        f"{body}\n\n"
        f"---\n\n"
        f"{criteria_block}"
        f"### Approved plan\n\n"
        f"{plan.content_md}\n\n"
        f"---\n\n"
        f"_Opened by [pravi](https://github.com/cavanpage/pravi-builder-agent). "
        f"Review the diff, mark ready when satisfied._"
    )
