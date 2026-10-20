import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import Project
from app.db.models.enums import MemberRole, StageName
from app.schemas.project import ProjectCreate, ProjectDetail, ProjectSummary, ProjectUpdate, StageRead
from app.services import projects as svc
from app.services.storage import Storage, get_storage, project_prefix

router = APIRouter(prefix="/projects", tags=["projects"])
log = get_logger(__name__)

STAGE_ORDER = {name: i for i, name in enumerate(StageName)}


def to_summary(project: Project, role: MemberRole) -> ProjectSummary:
    return ProjectSummary.model_validate({**_columns(project), "role": role})


def to_detail(project: Project, role: MemberRole) -> ProjectDetail:
    stages = sorted(project.stages, key=lambda s: STAGE_ORDER[s.name])
    return ProjectDetail.model_validate(
        {
            **_columns(project),
            "role": role,
            "raw_idea": svc.raw_idea(project),
            "stages": [StageRead.model_validate(s) for s in stages],
        }
    )


def _columns(project: Project) -> dict[str, object]:
    return {c.key: getattr(project, c.key) for c in Project.__table__.columns}


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, user: CurrentUser, db: DbSession) -> ProjectDetail:
    project = svc.new_project(
        user,
        body.title,
        raw_idea=body.raw_idea or None,
        domain=body.domain or None,
        target_venue=body.target_venue or None,
        citation_style=body.citation_style,
    )
    project_id = project.id
    db.add(project)
    await db.commit()
    log.info("project_created", project_id=str(project_id))
    project, role = await svc.get_project_for_user(db, project_id, user)
    return to_detail(project, role)


@router.get("", response_model=list[ProjectSummary])
async def list_projects(user: CurrentUser, db: DbSession) -> list[ProjectSummary]:
    return [to_summary(p, r) for p, r in await svc.list_projects_for_user(db, user)]


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProjectDetail:
    project, role = await svc.get_project_for_user(db, project_id, user)
    return to_detail(project, role)


@router.patch("/{project_id}", response_model=ProjectDetail)
async def update_project(project_id: uuid.UUID, body: ProjectUpdate, user: CurrentUser, db: DbSession) -> ProjectDetail:
    project, _ = await svc.get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    changes = body.model_dump(exclude_unset=True)
    if "raw_idea" in changes:
        svc.set_raw_idea(project, changes.pop("raw_idea") or None)
    for field in ("domain", "target_venue"):
        if field in changes:
            changes[field] = changes[field] or None
    for field, value in changes.items():
        setattr(project, field, value)
    svc.touch(project)
    await db.commit()
    log.info("project_updated", project_id=str(project.id), fields=sorted(body.model_fields_set))
    project, role = await svc.get_project_for_user(db, project_id, user)
    return to_detail(project, role)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: Annotated[Storage, Depends(get_storage)]
) -> Response:
    project, _ = await svc.get_project_for_user(db, project_id, user, min_role=MemberRole.OWNER)
    await db.delete(project)
    await db.commit()
    try:
        removed = await storage.delete_prefix(project_prefix(project_id))
    except Exception:
        log.exception("project_objects_delete_failed", project_id=str(project_id))
        removed = 0
    log.info("project_deleted", project_id=str(project_id), objects_removed=removed)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
