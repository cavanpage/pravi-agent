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
        PlanData,
        TicketRef,
        TicketStatusUpdate,
        load_plan,
        load_ticket,
        update_ticket_status,
    )
    from pravi.activities.dev_activity import (
        DevActivityRequest,
        DevActivityResult,
        run_dev,
    )
    from pravi.activities.git_activity import (
        CleanupRequest,
        WorktreeInfo,
        WorktreeRequest,
        create_worktree,
        remove_worktree,
    )
    from pravi.activities.pr_activity import (
        PushAndOpenPRRequest,
        PushAndOpenPRResult,
        push_and_open_pr,
    )


# Statuses surfaced via @workflow.query — keep these short, the CLI displays them.
STATUS_LOADING = "loading_ticket"
STATUS_WAITING_FOR_PLAN = "waiting_for_plan"
STATUS_RUNNING_DEV = "running_dev"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"


@dataclass
class FeatureWorkflowInput:
    ticket_id: int
    domain_name: str
    domain_description: str
    domain_paths: list[str]
    base_ref: str
    llm_task_queue: str
    cleanup_worktree: bool = False


@dataclass
class FeatureWorkflowResult:
    ticket_id: int
    plan_id: int | None
    worktree_path: str | None
    branch: str | None
    dev: DevActivityResult | None
    pr: PushAndOpenPRResult | None
    summary: str


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
                worktree_path=None,
                branch=None,
                dev=None,
                pr=None,
                summary="cancelled before plan",
            )

        plan: PlanData = await workflow.execute_activity(
            load_plan,
            self._plan_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        await workflow.execute_activity(
            update_ticket_status,
            TicketStatusUpdate(
                ticket_id=ticket.ticket_id, status="plan_approved"
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        self._status = STATUS_RUNNING_DEV
        branch = f"pravi/{ticket.external_id}-{plan.domain_name}"
        wt: WorktreeInfo = await workflow.execute_activity(
            create_worktree,
            WorktreeRequest(
                repo_path=ticket.repo_local_path,
                ticket_id=str(ticket.external_id),
                branch=branch,
                base_ref=inp.base_ref,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        dev_result: DevActivityResult | None = None
        try:
            await workflow.execute_activity(
                update_ticket_status,
                TicketStatusUpdate(ticket_id=ticket.ticket_id, status="in_progress"),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            dev_req = DevActivityRequest(
                repo_path=ticket.repo_local_path,
                repo_name=ticket.repo_name,
                worktree_path=wt.path,
                domain_name=plan.domain_name,
                domain_description=inp.domain_description,
                domain_paths=list(inp.domain_paths),
                task=_build_dev_task(ticket=ticket, plan=plan),
                # Lets the activity persist a Run row + push live events on
                # the per-ticket NOTIFY channel for <LiveRunPanel>.
                ticket_id=ticket.ticket_id,
            )
            dev_result = await workflow.execute_activity(
                run_dev,
                dev_req,
                task_queue=inp.llm_task_queue,
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        finally:
            pass  # worktree cleanup moved below — we need it intact to push.

        # Push + open PR if the dev step succeeded and committed something.
        pr_result: PushAndOpenPRResult | None = None
        if dev_result and dev_result.success:
            pr_result = await workflow.execute_activity(
                push_and_open_pr,
                PushAndOpenPRRequest(
                    ticket_id=ticket.ticket_id,
                    ticket_external_id=ticket.external_id,
                    ticket_title=ticket.title,
                    repo_path=ticket.repo_local_path,
                    worktree_path=wt.path,
                    branch=wt.branch,
                    base_ref=inp.base_ref,
                    pr_body=_build_pr_body(ticket=ticket, plan=plan),
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

        # Now the worktree's been used for the push; safe to remove if asked.
        if inp.cleanup_worktree:
            await workflow.execute_activity(
                remove_worktree,
                CleanupRequest(
                    repo_path=ticket.repo_local_path,
                    worktree_path=wt.path,
                    delete_branch=None,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        # Final status reflects what actually shipped:
        # - dev failed → failed
        # - dev ok + PR opened → pr_open
        # - dev ok + no commits OR no GitHub connection → in_progress (still work to do)
        if dev_result and not dev_result.success:
            final_status = "failed"
        elif pr_result and pr_result.pr_number is not None:
            final_status = "pr_open"
        else:
            final_status = "in_progress"
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
            worktree_path=wt.path,
            branch=wt.branch,
            dev=dev_result,
            pr=pr_result,
            summary=(dev_result.summary if dev_result else "no dev result"),
        )


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


def _build_pr_body(*, ticket: TicketRef, plan: PlanData) -> str:
    """Markdown body for the GitHub PR."""
    body = ticket.body or "(no description)"
    return (
        f"### Ticket\n\n"
        f"**{ticket.title}** ({ticket.external_id})\n\n"
        f"{body}\n\n"
        f"---\n\n"
        f"### Approved plan\n\n"
        f"{plan.content_md}\n\n"
        f"---\n\n"
        f"_Opened by [pravi](https://github.com/cavanpage/pravi-builder-agent) "
        f"as a draft PR. Review the diff, mark ready when satisfied._"
    )
