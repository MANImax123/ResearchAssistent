"""Methodology input workspace (Step 5.1): the researcher's details, uploads and follow-up questions."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.agents.methodology.schemas import FIELD_LABEL
from app.api.v1.jobs import DispatcherDep, start_job
from app.core.deps import CurrentUser, DbSession
from app.db.models import Job, MethodologyQuestion, Project
from app.db.models.enums import FileKind, JobStatus, MemberRole, StageName
from app.schemas.job import JobRead
from app.schemas.methodology import (
    FileCounts,
    MethodologySave,
    MethodologyWorkspaceRead,
    MissingRead,
    QuestionRead,
    QuestionUpdate,
)
from app.services import methodology
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/methodology", tags=["methodology"])

FOLLOW_UP_JOB = "methodology_follow_up"


def _question(q: MethodologyQuestion) -> QuestionRead:
    return QuestionRead.model_validate(
        {"id": q.id, "field": q.field, "label": FIELD_LABEL.get(q.field, q.field), "question": q.question,
         "reason": q.reason, "kind": q.kind, "status": q.status, "answer": q.answer, "answered_at": q.answered_at,
         "created_at": q.created_at}
    )  # fmt: skip


def _can_edit(role: MemberRole) -> bool:
    return role in (MemberRole.OWNER, MemberRole.EDITOR)


async def _read(db: DbSession, project: Project, can_edit: bool) -> MethodologyWorkspaceRead:
    data, version, updated_at = await methodology.load(db, project.id)
    counts = await methodology.file_counts(db, project.id)
    found = await methodology.checklist(db, project.id, data)
    questions = list(
        await db.scalars(
            select(MethodologyQuestion)
            .where(MethodologyQuestion.project_id == project.id, MethodologyQuestion.status != "dismissed")
            .order_by(MethodologyQuestion.created_at)
        )
    )
    questions.sort(key=lambda q: q.status != "open")
    reason = methodology.blocker(project)
    return MethodologyWorkspaceRead(
        input=data, version=version, updated_at=updated_at, editable=can_edit and reason is None,
        blocked_reason=reason,
        stage_status=next(row.status for row in project.stages if row.name == StageName.METHODOLOGY),
        missing=[MissingRead(field=m.field, label=FIELD_LABEL[m.field], reason=m.reason, essential=m.essential)
                 for m in found],
        files=FileCounts(diagram=counts.get(FileKind.DIAGRAM, 0), code=counts.get(FileKind.CODE, 0),
                         notes=counts.get(FileKind.NOTES, 0)),
        questions=[_question(q) for q in questions],
    )  # fmt: skip


def _ensure_editable(project: Project) -> None:
    if reason := methodology.blocker(project):
        raise HTTPException(status.HTTP_409_CONFLICT, reason)


@router.get("", response_model=MethodologyWorkspaceRead)
async def get_methodology(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> MethodologyWorkspaceRead:
    """The input form, what it is still missing, upload counts and the follow-up questions."""
    project, role = await get_project_for_user(db, project_id, user)
    return await _read(db, project, _can_edit(role))


@router.put("", response_model=MethodologyWorkspaceRead)
async def save_methodology(
    project_id: uuid.UUID, body: MethodologySave, user: CurrentUser, db: DbSession
) -> MethodologyWorkspaceRead:
    """Save the researcher's methodology details (their wording is kept as entered)."""
    project, role = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    try:
        await methodology.save(db, project, body.input, user.id, body.base_version)
    except methodology.VersionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    return await _read(db, project, _can_edit(role))


@router.post("/follow-ups", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def ask_follow_ups(project_id: uuid.UUID, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep) -> Job:
    """The agent's follow-up questions for what the methodology is missing."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    busy = await db.scalar(
        select(Job.id).where(
            Job.project_id == project_id,
            Job.type == FOLLOW_UP_JOB,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
        )
    )
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "The agent is already reviewing the methodology")
    return await start_job(db, dispatcher, user, FOLLOW_UP_JOB, {"project_id": str(project_id)}, project_id)


@router.patch("/questions/{question_id}", response_model=QuestionRead)
async def update_question(
    project_id: uuid.UUID, question_id: uuid.UUID, body: QuestionUpdate, user: CurrentUser, db: DbSession
) -> QuestionRead:
    """Answer a follow-up question (the answer joins the methodology input), dismiss it, or reopen it."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    question = await db.scalar(
        select(MethodologyQuestion).where(
            MethodologyQuestion.id == question_id, MethodologyQuestion.project_id == project_id
        )
    )
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    _ensure_editable(project)
    await methodology.update_question(db, project, question, answer=body.answer, status=body.status)
    await db.commit()
    return _question(question)
