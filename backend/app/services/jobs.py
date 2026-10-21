"""Background jobs: creation, visibility, and the progress/status lifecycle.

Lifecycle: pending (row created, task queued) → running → success | failed.
Each status/progress write commits on its own, so pollers see it immediately.
"""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Job, ProjectMember, User
from app.db.models.enums import JobStatus

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
TERMINAL = frozenset({JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELLED})
MAX_ERROR_CHARS = 2000


def new_job(job_type: str, user: User, project_id: uuid.UUID | None = None) -> Job:
    job_id = uuid.uuid4()
    # The job id doubles as the Celery task id, so logs on both sides line up
    return Job(
        id=job_id,
        type=job_type,
        project_id=project_id,
        created_by=user.id,
        celery_task_id=str(job_id),
        status=JobStatus.PENDING,
        progress=0,
        message="Waiting for a worker",
    )


def _visible_to(user: User) -> Any:
    member_projects = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    return or_(Job.created_by == user.id, Job.project_id.in_(member_projects))


async def get_job_for_user(db: AsyncSession, job_id: uuid.UUID, user: User) -> Job:
    """A job the user started, or one belonging to a project they're a member of (else 404)."""
    job = await db.scalar(
        select(Job).where(Job.id == job_id, _visible_to(user)).execution_options(populate_existing=True)
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


async def list_jobs_for_user(
    db: AsyncSession, user: User, project_id: uuid.UUID | None = None, limit: int = 20
) -> list[Job]:
    query = select(Job).where(_visible_to(user)).order_by(Job.created_at.desc()).limit(limit)
    if project_id is not None:
        query = query.where(Job.project_id == project_id)
    return list(await db.scalars(query))


class JobReporter:
    """Handed to job handlers to report progress; writes go straight to the `jobs` row."""

    def __init__(self, job_id: uuid.UUID, session_factory: SessionFactory) -> None:
        self.job_id = job_id
        self._session_factory = session_factory

    @property
    def session_factory(self) -> SessionFactory:
        """Database sessions for the handler's own work (same database as the job row)."""
        return self._session_factory

    async def _write(self, **values: Any) -> None:
        async with self._session_factory() as session:
            await session.execute(update(Job).where(Job.id == self.job_id).values(**values, updated_at=func.now()))
            await session.commit()

    async def start(self) -> None:
        await self._write(status=JobStatus.RUNNING, progress=0, message="Started", error=None)

    async def progress(self, percent: float, message: str | None = None) -> None:
        values: dict[str, Any] = {"progress": max(0, min(100, round(percent)))}
        if message is not None:
            values["message"] = message[:500]
        await self._write(**values)

    async def succeed(self, result: dict[str, Any] | None) -> None:
        await self._write(status=JobStatus.SUCCESS, progress=100, message="Done", result_ref=result)

    async def fail(self, error: str) -> None:
        await self._write(status=JobStatus.FAILED, message="Failed", error=error[:MAX_ERROR_CHARS])


# An async function taking (reporter, **params) and returning a JSON-able result or None
JobHandler = Callable[..., Awaitable[dict[str, Any] | None]]


async def execute_job(
    job_id: uuid.UUID, handler: JobHandler, params: dict[str, Any], session_factory: SessionFactory
) -> JobStatus:
    """Run `handler` for a job, recording running → success/failed. Never raises for handler errors."""
    reporter = JobReporter(job_id, session_factory)
    await reporter.start()
    try:
        result = await handler(reporter, **params)
    except Exception as exc:
        log.exception("job_failed", job_id=str(job_id))
        await reporter.fail(f"{type(exc).__name__}: {exc}")
        return JobStatus.FAILED
    await reporter.succeed(result)
    log.info("job_succeeded", job_id=str(job_id))
    return JobStatus.SUCCESS
