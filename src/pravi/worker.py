from __future__ import annotations

import asyncio

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from pravi.activities.git_activity import create_worktree, remove_worktree, run_command
from pravi.config import get_settings
from pravi.logging_setup import configure_logging
from pravi.workflows.feature_workflow import FeatureWorkflow

log = structlog.get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    log.info(
        "worker.starting",
        host=settings.temporal_host,
        namespace=settings.temporal_namespace,
        task_queue=settings.temporal_task_queue,
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[FeatureWorkflow],
        activities=[create_worktree, remove_worktree, run_command],
    )
    await worker.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
