from __future__ import annotations

import argparse
import asyncio
from typing import Literal

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from pravi.activities.db_activity import load_plan, load_ticket, update_ticket_status
from pravi.activities.dev_activity import run_dev
from pravi.activities.git_activity import create_worktree, remove_worktree, run_command
from pravi.activities.pr_activity import push_and_open_pr
from pravi.activities.sandbox_activity import cleanup_sandbox, provision_sandbox
from pravi.config import apply_anthropic_auth, get_settings
from pravi.logging_setup import configure_logging
from pravi.workflows.dev_workflow import DevWorkflow
from pravi.workflows.feature_workflow import FeatureWorkflow
from pravi.workflows.smoke_workflow import SmokeWorkflow

log = structlog.get_logger(__name__)

Queue = Literal["features", "llm"]


def _resolve_queue(queue: Queue) -> tuple[str, list, list]:
    """Return (task_queue_name, workflows, activities) for the given pool.

    Workflows are registered on both pools so workflow tasks load-balance.
    Activities are pool-specific so `execute_activity(..., task_queue=...)`
    routes work to the right worker — and concurrency caps actually mean
    something.
    """
    s = get_settings()
    # Workflows are registered on both pools so workflow tasks load-balance.
    workflows = [SmokeWorkflow, DevWorkflow, FeatureWorkflow]
    if queue == "features":
        return (
            s.temporal_task_queue_features,
            workflows,
            [
                # Legacy git-path activities — still used by smoke/dev CLI flows
                # that bypass the Repo identity and take a raw `--repo` path.
                create_worktree,
                remove_worktree,
                run_command,
                # Sandbox-backed activities for FeatureWorkflow (see ADR 0003).
                provision_sandbox,
                cleanup_sandbox,
                load_ticket,
                load_plan,
                update_ticket_status,
                push_and_open_pr,
            ],
        )
    if queue == "llm":
        return (s.temporal_task_queue_llm, workflows, [run_dev])
    raise ValueError(f"unknown queue: {queue}")


async def run(queue: Queue, max_activities: int | None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Only the LLM worker actually invokes the SDK — but apply to both for
    # consistency, since `claude` activity callers may run on the features
    # pool in the future (e.g. tester/reviewer agents).
    apply_anthropic_auth()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    task_queue, workflows, activities = _resolve_queue(queue)

    log.info(
        "worker.starting",
        host=settings.temporal_host,
        namespace=settings.temporal_namespace,
        queue=queue,
        task_queue=task_queue,
        max_concurrent_activities=max_activities,
    )
    worker_kwargs = {}
    if max_activities is not None:
        worker_kwargs["max_concurrent_activities"] = max_activities

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
        **worker_kwargs,
    )
    await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser(prog="pravi.worker")
    parser.add_argument(
        "--queue",
        choices=["features", "llm"],
        default="features",
        help="Which task-queue pool this worker serves (default: features).",
    )
    parser.add_argument(
        "--max-activities",
        type=int,
        default=None,
        help="Cap concurrent activity executions (recommended for --queue llm).",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.queue, args.max_activities))
    except KeyboardInterrupt:
        log.info("worker.stopped")


if __name__ == "__main__":
    main()
