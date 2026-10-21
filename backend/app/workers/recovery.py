"""Recover jobs interrupted by a worker restart (code reload, container restart, crash).

A job still marked `running` whose Celery task no worker is executing will never finish: the
broker only redelivers it after its visibility timeout (an hour). On worker start-up we mark such
jobs failed and put their stage back into a state the researcher can act on.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.db.models import AgentRun, Job, Paper, Stage
from app.db.models.enums import JobStatus, ParseStatus, RunStatus, StageName, StageStatus
from app.services.jobs import SessionFactory

log = get_logger(__name__)

INTERRUPTED = "Interrupted: the worker restarted while this was running. Run it again."
# Agent runs this old and still "running" belong to an interrupted job
STALE_RUN_AFTER = timedelta(minutes=10)


async def recover_interrupted_jobs(session_factory: SessionFactory, active_task_ids: set[str]) -> list[uuid.UUID]:
    """Fail running/pending-started jobs that no worker is executing; repair their stages."""
    async with session_factory() as session:
        jobs = list(
            await session.scalars(
                select(Job).where(Job.status == JobStatus.RUNNING, Job.celery_task_id.not_in(active_task_ids or {""}))
            )
        )
        for job in jobs:
            job.status = JobStatus.FAILED
            job.error = INTERRUPTED
            job.message = "Interrupted"
        await session.execute(
            update(AgentRun)
            .where(AgentRun.status == RunStatus.RUNNING, AgentRun.created_at < datetime.now(UTC) - STALE_RUN_AFTER)
            .values(status=RunStatus.FAILED, error=INTERRUPTED)
        )
        await session.commit()

    for job in jobs:
        if job.type == "stage_run" and job.project_id is not None:
            await _repair_stage(session_factory, job.project_id, job)
        if job.type == "literature_parse" and job.project_id is not None:
            await _repair_parsing(session_factory, job.project_id)
    if jobs:
        log.warning("jobs_recovered_after_restart", count=len(jobs), job_ids=[str(j.id) for j in jobs])
    return [j.id for j in jobs]


async def _repair_stage(session_factory: SessionFactory, project_id: uuid.UUID, job: Job) -> None:
    """Running stage → awaiting review if its review survived in the graph, otherwise rejected (Run again)."""
    from app.orchestrator import service as orchestrator  # imported lazily: keeps LangGraph out of worker boot

    async with session_factory() as session:
        stages = list(
            await session.scalars(
                select(Stage).where(Stage.project_id == project_id, Stage.status == StageStatus.RUNNING)
            )
        )
        if not stages:
            return
        try:
            graph = await orchestrator.read_state(project_id)
            pending = StageName(graph.pending.stage) if graph.pending else None
        except Exception:
            log.exception("stage_repair_graph_unreadable", project_id=str(project_id))
            pending = None
        for row in stages:
            row.status = StageStatus.AWAITING_REVIEW if row.name == pending else StageStatus.REJECTED
        await session.commit()
        log.info(
            "stages_repaired", project_id=str(project_id), job_id=str(job.id), stages=[s.name.value for s in stages]
        )


async def _repair_parsing(session_factory: SessionFactory, project_id: uuid.UUID) -> None:
    """Papers left queued/parsing by the interrupted job → failed, so "Get full text" picks them up again."""
    async with session_factory() as session:
        await session.execute(
            update(Paper)
            .where(Paper.project_id == project_id, Paper.parse_status.in_((ParseStatus.PENDING, ParseStatus.PARSING)))
            .values(parse_status=ParseStatus.FAILED, parse_error=INTERRUPTED)
        )
        await session.commit()
