"""Stage runs and the researcher's review actions (A4.4: every stage needs explicit approval).

    run · approve · reject · edit · regenerate · go-back

Approve / reject / edit / go-back are applied immediately (no agent runs). Run and regenerate run an
agent, so they're queued as background jobs; poll the returned job, then re-read the state.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.v1.jobs import DispatcherDep, start_job
from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import Feedback, Project, Stage, User
from app.db.models.enums import FeedbackAction, MemberRole, StageName, StageStatus
from app.db.session import request_session_factory
from app.orchestrator import service as orchestrator
from app.orchestrator import workflow
from app.orchestrator.checkpoints import thread_config
from app.orchestrator.graph import STAGE_LABELS
from app.schemas.job import JobRead
from app.schemas.stage import (
    ApproveRequest,
    CheckpointRead,
    EditRequest,
    PendingReviewRead,
    ProjectStateRead,
    RegenerateRequest,
    RejectRequest,
    RunStageRequest,
    StageRunStarted,
    StageState,
)
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}", tags=["stages"])
log = get_logger(__name__)

STAGE_ORDER = {name: i for i, name in enumerate(StageName)}


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail)


def _label(stage: StageName | str) -> str:
    return STAGE_LABELS[StageName(stage)]


def _rows(project: Project) -> dict[StageName, Stage]:
    return {row.name: row for row in project.stages}


def _ensure_nothing_running(rows: dict[StageName, Stage]) -> None:
    if any(r.status == StageStatus.RUNNING for r in rows.values()):
        raise _conflict("A stage is running; wait for it to finish")


def _ensure_awaiting_review(rows: dict[StageName, Stage], stage: StageName) -> None:
    if rows[stage].status != StageStatus.AWAITING_REVIEW:
        raise _conflict(f"{_label(stage)} is not awaiting review")


def _ensure_prerequisites(rows: dict[StageName, Stage], stage: StageName) -> None:
    missing = workflow.blocked_by(stage, {name: row.status for name, row in rows.items()})
    if missing:
        raise _conflict(f"Approve {', '.join(_label(m) for m in missing)} first")


def _ensure_no_other_review(rows: dict[StageName, Stage]) -> None:
    waiting = [name for name, row in rows.items() if row.status == StageStatus.AWAITING_REVIEW]
    if waiting:
        raise _conflict(f"{_label(waiting[0])} is awaiting your review; approve or reject it first")


async def _record(
    db: DbSession, project_id: uuid.UUID, stage: StageName, user: User, action: FeedbackAction, comment: str | None
) -> None:
    db.add(Feedback(project_id=project_id, stage=stage, user_id=user.id, action=action, comment=comment))
    await db.commit()


def _translate(exc: Exception) -> HTTPException:
    """Orchestrator errors that slipped past the pre-checks (e.g. a concurrent request)."""
    if isinstance(exc, orchestrator.ReviewPendingError):
        return _conflict(f"{_label(exc.stage)} is awaiting your review; approve or reject it first")
    if isinstance(exc, orchestrator.PrerequisitesNotMetError):
        return _conflict(f"Approve {', '.join(_label(m) for m in exc.missing)} first")
    return _conflict(str(exc))


ORCHESTRATOR_ERRORS = (
    orchestrator.ReviewPendingError,
    orchestrator.NoPendingReviewError,
    orchestrator.PrerequisitesNotMetError,
    orchestrator.InvalidStageActionError,
)


# ─── Run / regenerate (background jobs) ───────────────────────────────


async def _queue_stage_job(
    db: DbSession,
    dispatcher: DispatcherDep,
    user: User,
    project_id: uuid.UUID,
    row: Stage,
    params: dict[str, Any],
) -> StageRunStarted:
    previous = row.status
    row.status = StageStatus.RUNNING
    params = {"project_id": str(project_id), "stage": row.name.value, "previous_status": previous.value, **params}
    try:
        # start_job commits the status change together with the job row
        job = await start_job(db, dispatcher, user, "stage_run", params, project_id)
    except HTTPException:
        row.status = previous  # queue unavailable: don't leave the stage stuck in "running"
        await db.commit()
        raise
    log.info("stage_job_queued", project_id=str(project_id), stage=row.name.value, mode=params.get("mode", "run"))
    return StageRunStarted(stage=row.name, status=StageStatus.RUNNING, job=JobRead.model_validate(job))


@router.post("/stages/{stage}/run", response_model=StageRunStarted, status_code=status.HTTP_202_ACCEPTED)
async def run_stage(
    project_id: uuid.UUID,
    stage: StageName,
    user: CurrentUser,
    db: DbSession,
    dispatcher: DispatcherDep,
    body: RunStageRequest | None = None,
) -> StageRunStarted:
    """Queue the stage's agent; when the job finishes the stage is `awaiting_review`.

    Allowed when the stage is not started, rejected or stale and all prerequisites are approved.
    Optional `instructions` are given to the agent for this run.
    """
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    rows = _rows(project)
    _ensure_nothing_running(rows)
    _ensure_no_other_review(rows)
    if rows[stage].status not in workflow.RUNNABLE:
        raise _conflict(f"{_label(stage)} is {rows[stage].status.value.replace('_', ' ')}; use Regenerate or Go back")
    _ensure_prerequisites(rows, stage)
    if stage == StageName.IDEA and not _raw_idea(project):
        raise _conflict("Add your research idea to the project first")
    params: dict[str, Any] = {"mode": "run"}
    if body and body.instructions:
        params["feedback"] = body.instructions
    return await _queue_stage_job(db, dispatcher, user, project_id, rows[stage], params)


def _raw_idea(project: Project) -> str:
    snapshot: dict[str, Any] = project.research_state.snapshot if project.research_state else {}
    return ((snapshot.get("idea") or {}).get("raw_idea") or "").strip()


@router.post("/stages/{stage}/regenerate", response_model=StageRunStarted, status_code=status.HTTP_202_ACCEPTED)
async def regenerate_stage(
    project_id: uuid.UUID,
    stage: StageName,
    body: RegenerateRequest,
    user: CurrentUser,
    db: DbSession,
    dispatcher: DispatcherDep,
) -> StageRunStarted:
    """Run the stage's agent again, with your feedback. Stages that depend on it become stale."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    rows = _rows(project)
    _ensure_nothing_running(rows)
    current = rows[stage].status
    if current == StageStatus.NOT_STARTED:
        raise _conflict(f"{_label(stage)} hasn't been run yet; use Run")
    if current == StageStatus.AWAITING_REVIEW:
        params: dict[str, Any] = {"mode": "regenerate", "feedback": body.feedback, "by": str(user.id)}
    else:
        _ensure_no_other_review(rows)
        _ensure_prerequisites(rows, stage)
        params = {"mode": "run", "feedback": body.feedback or ""}
    db.add(
        Feedback(
            project_id=project_id, stage=stage, user_id=user.id, action=FeedbackAction.REGENERATE, comment=body.feedback
        )
    )
    return await _queue_stage_job(db, dispatcher, user, project_id, rows[stage], params)


# ─── Review actions (immediate) ───────────────────────────────────────


async def _apply_review(
    db: DbSession, project_id: uuid.UUID, stage: StageName, user: User, decision: dict[str, Any]
) -> None:
    try:
        await orchestrator.review(project_id, decision, session_factory=request_session_factory(db))
    except ORCHESTRATOR_ERRORS as exc:
        raise _translate(exc) from exc


@router.post("/stages/{stage}/approve", response_model=ProjectStateRead)
async def approve_stage(
    project_id: uuid.UUID, stage: StageName, body: ApproveRequest, user: CurrentUser, db: DbSession
) -> ProjectStateRead:
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_awaiting_review(_rows(project), stage)
    await _apply_review(db, project_id, stage, user, {"action": "approve", "by": str(user.id), "comment": body.comment})
    await _record(db, project_id, stage, user, FeedbackAction.APPROVE, body.comment)
    return await build_state(db, project_id, user)


@router.post("/stages/{stage}/reject", response_model=ProjectStateRead)
async def reject_stage(
    project_id: uuid.UUID, stage: StageName, body: RejectRequest, user: CurrentUser, db: DbSession
) -> ProjectStateRead:
    """Reject the draft; the comment is passed to the agent when the stage runs again."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_awaiting_review(_rows(project), stage)
    await _apply_review(db, project_id, stage, user, {"action": "reject", "by": str(user.id), "comment": body.comment})
    await _record(db, project_id, stage, user, FeedbackAction.REJECT, body.comment)
    return await build_state(db, project_id, user)


@router.post("/stages/{stage}/edit", response_model=ProjectStateRead)
async def edit_stage(
    project_id: uuid.UUID, stage: StageName, body: EditRequest, user: CurrentUser, db: DbSession
) -> ProjectStateRead:
    """Replace the draft under review with your own text (it stays awaiting review). To edit an
    approved stage, Go back to it first."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_awaiting_review(_rows(project), stage)
    await _apply_review(db, project_id, stage, user, {"action": "edit", "by": str(user.id), "content": body.content})
    await _record(db, project_id, stage, user, FeedbackAction.EDIT, body.change_note)
    return await build_state(db, project_id, user)


@router.post("/stages/{stage}/go-back", response_model=ProjectStateRead)
async def go_back(project_id: uuid.UUID, stage: StageName, user: CurrentUser, db: DbSession) -> ProjectStateRead:
    """Reopen an earlier stage for review (edit, regenerate or re-approve it). Every stage that
    depends on it and has already been worked on is marked stale."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_nothing_running(_rows(project))
    try:
        await orchestrator.go_back(project_id, stage, session_factory=request_session_factory(db))
    except ORCHESTRATOR_ERRORS as exc:
        raise _translate(exc) from exc
    await _record(db, project_id, stage, user, FeedbackAction.BACK, None)
    return await build_state(db, project_id, user)


# ─── State ────────────────────────────────────────────────────────────


DONE = (StageStatus.APPROVED, StageStatus.REJECTED, StageStatus.STALE)  # stages with a finished draft


def _stage_states(rows: dict[StageName, Stage]) -> list[StageState]:
    """Mirror the endpoint checks, so the UI only offers actions that will succeed."""
    statuses = {name: row.status for name, row in rows.items()}
    running = any(s == StageStatus.RUNNING for s in statuses.values())
    pending = next((n for n, s in statuses.items() if s == StageStatus.AWAITING_REVIEW), None)
    idle = not running and pending is None  # nothing running and nothing awaiting review

    def state(name: StageName) -> StageState:
        status_ = statuses[name]
        blocked = workflow.blocked_by(name, statuses)
        rerunnable = idle and status_ in DONE and not blocked
        return StageState(
            name=name,
            status=status_,
            approved_at=rows[name].approved_at,
            prerequisites=list(workflow.PREREQUISITES[name]),
            blocked_by=blocked,
            can_run=idle and status_ in workflow.RUNNABLE and not blocked,
            can_regenerate=not running and (status_ == StageStatus.AWAITING_REVIEW or rerunnable),
            can_go_back=not running and status_ in DONE and (pending is None or pending in workflow.dependents(name)),
        )

    return [state(name) for name in sorted(rows, key=lambda n: STAGE_ORDER[n])]


async def build_state(db: DbSession, project_id: uuid.UUID, user: User) -> ProjectStateRead:
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
        stages=_stage_states(_rows(project)),
        pending_review=pending,
        checkpoint=checkpoint,
    )


@router.get("/state", response_model=ProjectStateRead)
async def get_state(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProjectStateRead:
    """The project's ResearchState, each stage's status and allowed actions, and any draft awaiting review."""
    return await build_state(db, project_id, user)
