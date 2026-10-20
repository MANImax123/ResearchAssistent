"""Hands created jobs to the task queue. Swapped out in tests (FastAPI dependency)."""

import asyncio
from typing import Any, Protocol

from app.db.models import Job


class JobDispatcher(Protocol):
    async def enqueue(self, job: Job, params: dict[str, Any]) -> None: ...


class CeleryDispatcher:
    async def enqueue(self, job: Job, params: dict[str, Any]) -> None:
        from app.workers.tasks import run_job  # imported lazily: keeps Celery out of API startup

        # Publishing is a blocking Redis call; fail fast instead of hanging the request if Redis is down
        await asyncio.to_thread(
            run_job.apply_async,
            args=[str(job.id), job.type, params],
            task_id=job.celery_task_id,
            retry=True,
            retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.5, "interval_max": 1},
        )


def get_dispatcher() -> JobDispatcher:
    return CeleryDispatcher()
