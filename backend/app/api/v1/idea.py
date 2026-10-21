"""Idea workspace (Step 3.1): read and edit the research plan produced by the Idea agent."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.agents.idea.agent import plan_from_state, plan_to_state, render_markdown
from app.agents.idea.schemas import IdeaPlan
from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import Feedback, Project, User
from app.db.models.enums import FeedbackAction, MemberRole, SectionType, StageName, StageStatus, VersionSource
from app.db.session import request_session_factory
from app.orchestrator import service as orchestrator
from app.schemas.idea import IdeaWorkspaceRead
from app.services import sections
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/idea", tags=["idea"])
log = get_logger(__name__)


def _idea_status(project: Project) -> StageStatus:
    return next(row.status for row in project.stages if row.name == StageName.IDEA)


def _blocker(status_: StageStatus, has_plan: bool) -> str | None:
    if status_ == StageStatus.AWAITING_REVIEW and has_plan:
        return None
    if not has_plan:
        return "Generate a plan first"
    if status_ == StageStatus.RUNNING:
        return "The agent is working on the plan"
    return "Go back to the Idea stage to edit the plan"


async def _read(db: DbSession, project_id: uuid.UUID, user: User) -> IdeaWorkspaceRead:
    project, role = await get_project_for_user(db, project_id, user)
    snapshot: dict[str, Any] = project.research_state.snapshot if project.research_state else {}
    plan = plan_from_state(snapshot)
    output = (snapshot.get("stage_outputs") or {}).get(StageName.IDEA.value) or {}
    status_ = _idea_status(project)
    blocker = _blocker(status_, plan is not None)
    if role == MemberRole.VIEWER:
        blocker = "You have view-only access"
    run_id = output.get("agent_run_id")
    return IdeaWorkspaceRead(
        status=status_,
        raw_idea=(snapshot.get("idea") or {}).get("raw_idea"),
        plan=plan,
        editable=blocker is None,
        edit_blocked_reason=blocker,
        generated_at=datetime.fromisoformat(output["generated_at"]) if output.get("generated_at") else None,
        agent_run_id=uuid.UUID(run_id) if run_id else None,
        pending_feedback=snapshot.get("pending_feedback"),
    )


@router.get("", response_model=IdeaWorkspaceRead)
async def get_idea(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> IdeaWorkspaceRead:
    """The research plan (if generated) and whether it can be edited now."""
    return await _read(db, project_id, user)


@router.put("/plan", response_model=IdeaWorkspaceRead)
async def update_plan(project_id: uuid.UUID, plan: IdeaPlan, user: CurrentUser, db: DbSession) -> IdeaWorkspaceRead:
    """Save the researcher's edits to the plan (validated like the agent's output: every research
    question links to an existing objective, every objective has a question)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    snapshot: dict[str, Any] = project.research_state.snapshot if project.research_state else {}
    previous = plan_from_state(snapshot)
    blocker = _blocker(_idea_status(project), previous is not None)
    if blocker:
        raise HTTPException(status.HTTP_409_CONFLICT, blocker)
    if plan.title is None:
        plan = plan.model_copy(update={"title": plan.title_options[0]})

    raw_idea = (snapshot.get("idea") or {}).get("raw_idea")
    decision = {
        "action": "update",
        "by": str(user.id),
        "fields": plan_to_state(plan, raw_idea),
        "draft": render_markdown(plan),
    }
    try:
        await orchestrator.review(project_id, decision, session_factory=request_session_factory(db))
    except (orchestrator.NoPendingReviewError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    if previous is None or plan.title != previous.title:
        await _record_title(db, project_id, plan, user)
    db.add(
        Feedback(
            project_id=project_id,
            stage=StageName.IDEA,
            user_id=user.id,
            action=FeedbackAction.EDIT,
            comment="Edited research plan",
        )
    )
    await db.commit()
    log.info("idea_plan_updated", project_id=str(project_id))
    return await _read(db, project_id, user)


async def _record_title(db: DbSession, project_id: uuid.UUID, plan: IdeaPlan, user: User) -> None:
    """The chosen title becomes the Title section (AI wording if it's one of the options)."""
    assert plan.title is not None
    section = await sections.get_or_create_section(db, project_id, SectionType.TITLE)
    current = await sections.current_version(db, section)
    if current is not None and current.content == plan.title:
        return
    source = VersionSource.AI if plan.title in plan.title_options else sections.edited_source(current)
    await sections.add_version(db, section, plan.title, source, created_by=user.id, change_note="Title chosen in plan")
