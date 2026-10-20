"""Project creation, membership-based access, and updates."""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Project, ProjectMember, ResearchStateSnapshot, Stage, User
from app.db.models.enums import MemberRole, StageName

ROLE_RANK = {MemberRole.VIEWER: 0, MemberRole.EDITOR: 1, MemberRole.OWNER: 2}


def new_project(owner: User, title: str, raw_idea: str | None = None, **fields: Any) -> Project:
    """A project with its owner membership, all 9 stages (`not_started`) and initial research state."""
    project_id = uuid.uuid4()
    project = Project(id=project_id, owner_id=owner.id, title=title, **fields)
    project.members = [ProjectMember(user_id=owner.id, role=MemberRole.OWNER)]
    project.stages = [Stage(name=name) for name in StageName]
    project.research_state = ResearchStateSnapshot(
        snapshot={
            "project_id": str(project_id),
            "current_stage": StageName.IDEA.value,
            "idea": {"raw_idea": raw_idea},
        },
        version=1,
    )
    return project


async def get_project_for_user(
    db: AsyncSession, project_id: uuid.UUID, user: User, min_role: MemberRole = MemberRole.VIEWER
) -> tuple[Project, MemberRole]:
    """Load a project the user belongs to, with stages and state.

    Raises 404 for non-members (existence is not revealed) and 403 if their role is too low.
    """
    row = (
        await db.execute(
            select(Project, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(Project.id == project_id, ProjectMember.user_id == user.id)
            .options(selectinload(Project.stages), selectinload(Project.research_state))
            # Refresh already-loaded rows too: after a commit, server-set timestamps must be re-read
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    project, role = row
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_role.value} access to this project")
    return project, role


async def list_projects_for_user(db: AsyncSession, user: User) -> list[tuple[Project, MemberRole]]:
    rows = await db.execute(
        select(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.updated_at.desc(), Project.created_at.desc(), Project.id)
    )
    return [(p, r) for p, r in rows]


def raw_idea(project: Project) -> str | None:
    if project.research_state is None:
        return None
    value = project.research_state.snapshot.get("idea", {}).get("raw_idea")
    return value if isinstance(value, str) else None


def set_raw_idea(project: Project, value: str | None) -> None:
    state = project.research_state
    if state is None:
        project.research_state = ResearchStateSnapshot(
            snapshot={"project_id": str(project.id), "idea": {"raw_idea": value}}
        )
        return
    # Reassign (not mutate) so SQLAlchemy sees the JSONB change
    snapshot = dict(state.snapshot)
    snapshot["idea"] = {**snapshot.get("idea", {}), "raw_idea": value}
    state.snapshot = snapshot
    state.version += 1


def touch(project: Project) -> None:
    """Bump `updated_at` even when only related rows (e.g. research state) changed."""
    project.updated_at = func.now()
