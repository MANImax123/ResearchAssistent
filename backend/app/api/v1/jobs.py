import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import Job
from app.db.models.enums import JobStatus, MemberRole
from app.schemas.job import DemoJobRequest, JobRead
from app.services.jobs import get_job_for_user, list_jobs_for_user, new_job
from app.services.projects import get_project_for_user
from app.workers.dispatch import JobDispatcher, get_dispatcher

router = APIRouter(prefix="/jobs", tags=["jobs"])
log = get_logger(__name__)

DispatcherDep = Annotated[JobDispatcher, Depends(get_dispatcher)]


async def start_job(
    db: DbSession,
    dispatcher: JobDispatcher,
    user: CurrentUser,
    job_type: str,
    params: dict[str, Any],
    project_id: uuid.UUID | None = None,
) -> Job:
    """Create the job row (pending), commit it, then queue it. Shared by every endpoint that starts work."""
    job = new_job(job_type, user, project_id)
    job_id = job.id
    db.add(job)
    # Commit before queueing, or a fast worker could look for a row that doesn't exist yet
    await db.commit()
    try:
        await dispatcher.enqueue(job, params)
    except Exception as exc:
        log.exception("job_enqueue_failed", job_id=str(job_id))
        job.status = JobStatus.FAILED
        job.message = "Failed"
        job.error = "Could not reach the task queue"
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Task queue unavailable, try again shortly") from exc
    log.info("job_queued", job_id=str(job_id), job_type=job_type)
    return await get_job_for_user(db, job_id, user)


@router.post("/demo", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def start_demo_job(body: DemoJobRequest, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep) -> Job:
    """Sleeps for `steps × delay_s` seconds, reporting progress after each step."""
    if body.project_id is not None:
        await get_project_for_user(db, body.project_id, user, min_role=MemberRole.EDITOR)
    params = body.model_dump(include={"steps", "delay_s", "fail_at"})
    return await start_job(db, dispatcher, user, "demo", params, body.project_id)


@router.get("", response_model=list[JobRead])
async def list_jobs(
    user: CurrentUser,
    db: DbSession,
    project_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Job]:
    return await list_jobs_for_user(db, user, project_id, limit)


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Job:
    """Poll this for status and progress until `status` is success, failed or cancelled."""
    return await get_job_for_user(db, job_id, user)
