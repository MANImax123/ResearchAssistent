import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.v1.jobs import DispatcherDep, start_job
from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models.enums import MemberRole, StageName, StageStatus
from app.orchestrator import service as orchestrator
from app.orchestrator.checkpoints import thread_config
from app.orchestrator.graph import STAGE_LABELS
from app.schemas.job import JobRead
from app.schemas.project import StageRead
from app.schemas.stage import CheckpointRead, PendingReviewRead, ProjectStateRead, StageRunStarted
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}", tags=["stages"])
log = get_logger(__name__)

STAGE_ORDER = {name: i for i, name in enumerate(StageName)}


@router.post("/stages/{stage}/run", response_model=StageRunStarted, status_code=status.HTTP_202_ACCEPTED)
async def run_stage(
    project_id: uuid.UUID, stage: StageName, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> StageRunStarted:
    """Queue the stage's agent. When it finishes, the stage is `awaiting_review` (poll the returned job)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    rows = {row.name: row for row in project.stages}
    if any(r.status == StageStatus.RUNNING for r in rows.values()):
        raise HTTPException(status.HTTP_409_CONFLICT, "A stage is already running for this project")
    waiting = [r.name for r in rows.values() if r.status == StageStatus.AWAITING_REVIEW]
    if waiting:
        label = STAGE_LABELS[waiting[0]]
        raise HTTPException(status.HTTP_409_CONFLICT, f"{label} is awaiting your review; approve or reject it first")

    row = rows[stage]
    previous = row.status
    row.status = StageStatus.RUNNING
    params = {"project_id": str(project_id), "stage": stage.value, "previous_status": previous.value}
    # start_job commits the status change together with the job row
    try:
        job = await start_job(db, dispatcher, user, "stage_run", params, project_id)
    except HTTPException:
        row.status = previous  # queue unavailable: don't leave the stage stuck in "running"
        await db.commit()
        raise
    log.info("stage_run_queued", project_id=str(project_id), stage=stage.value, job_id=str(job.id))
    return StageRunStarted(stage=stage, status=StageStatus.RUNNING, job=JobRead.model_validate(job))


@router.get("/state", response_model=ProjectStateRead)
async def get_state(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProjectStateRead:
    """The project's ResearchState, stage statuses, and any stage waiting for review."""
    project, _ = await get_project_for_user(db, project_id, user)
    research_state = project.research_state

    checkpoint = pending = None
    try:
        graph = await orchestrator.read_state(project_id)
    except Exception:
        log.exception("checkpointer_unavailable", project_id=str(project_id))
    else:
        if graph.checkpoint_id:
            checkpoint = CheckpointRead(
                thread_id=thread_config(project_id)["configurable"]["thread_id"],
                checkpoint_id=graph.checkpoint_id,
                next=list(graph.next),
            )
        if graph.pending:
            pending = PendingReviewRead(stage=StageName(graph.pending.stage), draft=graph.pending.draft)

    return ProjectStateRead(
        project_id=project_id,
        version=research_state.version if research_state else 0,
        updated_at=research_state.updated_at if research_state else None,
        state=research_state.snapshot if research_state else {},
        stages=[StageRead.model_validate(s) for s in sorted(project.stages, key=lambda s: STAGE_ORDER[s.name])],
        pending_review=pending,
        checkpoint=checkpoint,
    )
