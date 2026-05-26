"""Slice 1A workflow: create worktree → run dev agent → optional cleanup.

This is a development surface for exercising the dev activity end-to-end
*without* the architect plan signal or PR opening — those land in 1B / 1C
and modify FeatureWorkflow instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
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


@dataclass
class DevWorkflowInput:
    repo_path: str
    ticket_id: str
    branch: str
    base_ref: str
    dev_request: DevActivityRequest
    # Task queue for the LLM-pool worker (where run_dev is registered).
    llm_task_queue: str
    cleanup_worktree: bool = False
    delete_branch_on_cleanup: bool = False


@dataclass
class DevWorkflowResult:
    worktree_path: str
    branch: str
    dev: DevActivityResult


@workflow.defn
class DevWorkflow:
    @workflow.run
    async def run(self, inp: DevWorkflowInput) -> DevWorkflowResult:
        wt: WorktreeInfo = await workflow.execute_activity(
            create_worktree,
            WorktreeRequest(
                repo_path=inp.repo_path,
                ticket_id=inp.ticket_id,
                branch=inp.branch,
                base_ref=inp.base_ref,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        # The DevActivityRequest's worktree_path field is filled in here so
        # the caller doesn't need to know it in advance — the worktree path
        # comes from the worktree activity result.
        dev_req = DevActivityRequest(
            repo_path=inp.dev_request.repo_path,
            repo_name=inp.dev_request.repo_name,
            worktree_path=wt.path,
            domain_name=inp.dev_request.domain_name,
            domain_description=inp.dev_request.domain_description,
            domain_paths=inp.dev_request.domain_paths,
            task=inp.dev_request.task,
            # Pass-through: DevWorkflow callers may or may not have a ticket.
            ticket_id=inp.dev_request.ticket_id,
        )

        try:
            dev_result: DevActivityResult = await workflow.execute_activity(
                run_dev,
                dev_req,
                task_queue=inp.llm_task_queue,
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=2),
                # LLM activities are never silently retried — the bounded
                # dev/test feedback loop (added in Slice 2) handles "retry"
                # semantically.
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        finally:
            if inp.cleanup_worktree:
                await workflow.execute_activity(
                    remove_worktree,
                    CleanupRequest(
                        repo_path=inp.repo_path,
                        worktree_path=wt.path,
                        delete_branch=wt.branch if inp.delete_branch_on_cleanup else None,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

        return DevWorkflowResult(worktree_path=wt.path, branch=wt.branch, dev=dev_result)
