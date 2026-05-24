from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pravi.activities.git_activity import (
        CleanupRequest,
        RunCommandRequest,
        RunCommandResult,
        WorktreeInfo,
        WorktreeRequest,
        create_worktree,
        remove_worktree,
        run_command,
    )


@dataclass
class FeatureWorkflowInput:
    repo_path: str
    ticket_id: str
    branch: str
    base_ref: str = "main"
    smoke_command: list[str] | None = None
    delete_branch_on_cleanup: bool = False


@dataclass
class FeatureWorkflowResult:
    worktree_path: str
    smoke_exit_code: int | None
    summary: str


@workflow.defn
class FeatureWorkflow:
    """Slice 0 skeleton: create worktree, run a smoke command, tear down.

    Future slices will insert the architect-approval signal wait, the dev/test
    loop, reviewer activity, and PR opening between worktree create and cleanup.
    """

    @workflow.run
    async def run(self, inp: FeatureWorkflowInput) -> FeatureWorkflowResult:
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

        smoke_exit: int | None = None
        try:
            if inp.smoke_command:
                result: RunCommandResult = await workflow.execute_activity(
                    run_command,
                    RunCommandRequest(
                        cwd=wt.path,
                        command=inp.smoke_command,
                        timeout_seconds=600,
                    ),
                    start_to_close_timeout=timedelta(minutes=15),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                smoke_exit = result.exit_code
        finally:
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

        summary = (
            f"worktree={wt.path} branch={wt.branch} smoke_exit={smoke_exit}"
            if inp.smoke_command
            else f"worktree={wt.path} branch={wt.branch} (no smoke command)"
        )
        return FeatureWorkflowResult(
            worktree_path=wt.path,
            smoke_exit_code=smoke_exit,
            summary=summary,
        )
