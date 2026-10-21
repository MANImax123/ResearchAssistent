"""Paper sections: read, edit (versioned, with autosave), history, restore and diff."""

import re
import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import Project, Section, SectionVersion
from app.db.models.enums import MemberRole, SectionStatus, SectionType, StageName, StageStatus
from app.schemas.section import (
    DiffRead,
    SaveVersionRequest,
    SaveVersionResponse,
    SectionDetail,
    SectionSummary,
    VersionMeta,
    VersionRead,
)
from app.services import sections as svc
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/sections", tags=["sections"])
log = get_logger(__name__)

SECTION_ORDER = {t: i for i, t in enumerate(SectionType)}
_WORDS = re.compile(r"[\w'’-]+")


def _statuses(project: Project) -> dict[StageName, StageStatus]:
    return {row.name: row.status for row in project.stages}


def _word_count(content: str) -> int:
    return len(_WORDS.findall(re.sub(r"\[@[^\]]+\]", " ", content)))


async def _summary(
    db: DbSession,
    section_type: SectionType,
    section: Section | None,
    statuses: dict[StageName, StageStatus],
    can_edit: bool,
) -> tuple[SectionSummary, SectionVersion | None]:
    current = await svc.current_version(db, section) if section else None
    count = 0
    if section is not None:
        count = await db.scalar(select(func.count()).where(SectionVersion.section_id == section.id)) or 0
    blocker = svc.edit_blocker(section_type, statuses) if can_edit else "You have view-only access"
    summary = SectionSummary(
        type=section_type,
        stage=svc.SECTION_STAGE.get(section_type),
        status=section.status if section else SectionStatus.EMPTY,
        exists=section is not None,
        version_count=count,
        current_version=VersionMeta.model_validate(current) if current else None,
        word_count=_word_count(current.content) if current else 0,
        editable=blocker is None,
        edit_blocked_reason=blocker,
    )
    return summary, current


async def _load(
    db: DbSession, project_id: uuid.UUID, section_type: SectionType, user: CurrentUser, min_role: MemberRole
) -> tuple[Project, MemberRole, Section | None]:
    project, role = await get_project_for_user(db, project_id, user, min_role=min_role)
    return project, role, await svc.get_section(db, project_id, section_type)


def _can_edit(role: MemberRole) -> bool:
    return role in (MemberRole.OWNER, MemberRole.EDITOR)


async def _version_or_404(db: DbSession, section: Section | None, version_id: uuid.UUID) -> SectionVersion:
    version = await db.get(SectionVersion, version_id) if section else None
    if section is None or version is None or version.section_id != section.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    return version


@router.get("", response_model=list[SectionSummary])
async def list_sections(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[SectionSummary]:
    """All section types in manuscript order, including ones not written yet."""
    project, role = await get_project_for_user(db, project_id, user)
    existing = {s.type: s for s in await db.scalars(select(Section).where(Section.project_id == project_id))}
    statuses = _statuses(project)
    return [
        (await _summary(db, t, existing.get(t), statuses, _can_edit(role)))[0]
        for t in sorted(SectionType, key=lambda t: SECTION_ORDER[t])
    ]


@router.get("/{section_type}", response_model=SectionDetail)
async def get_section(
    project_id: uuid.UUID, section_type: SectionType, user: CurrentUser, db: DbSession
) -> SectionDetail:
    project, role, section = await _load(db, project_id, section_type, user, MemberRole.VIEWER)
    summary, current = await _summary(db, section_type, section, _statuses(project), _can_edit(role))
    return SectionDetail(
        **summary.model_dump(), id=section.id if section else None, content=current.content if current else ""
    )


@router.get("/{section_type}/versions", response_model=list[VersionMeta])
async def list_versions(
    project_id: uuid.UUID, section_type: SectionType, user: CurrentUser, db: DbSession
) -> list[SectionVersion]:
    """Newest first."""
    _, _, section = await _load(db, project_id, section_type, user, MemberRole.VIEWER)
    if section is None:
        return []
    query = select(SectionVersion).where(SectionVersion.section_id == section.id).order_by(SectionVersion.number.desc())
    return list(await db.scalars(query))


@router.get("/{section_type}/versions/{version_id}", response_model=VersionRead)
async def get_version(
    project_id: uuid.UUID, section_type: SectionType, version_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> SectionVersion:
    _, _, section = await _load(db, project_id, section_type, user, MemberRole.VIEWER)
    return await _version_or_404(db, section, version_id)


def _ensure_editable(project: Project, section_type: SectionType) -> None:
    blocker = svc.edit_blocker(section_type, _statuses(project))
    if blocker is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, blocker)


@router.post("/{section_type}/versions", response_model=SaveVersionResponse)
async def save_version(
    project_id: uuid.UUID,
    section_type: SectionType,
    body: SaveVersionRequest,
    user: CurrentUser,
    db: DbSession,
    response: Response,
) -> SaveVersionResponse:
    """Save the researcher's text: a new version (201), or an update to their own recent autosave (200).

    Send `base_version_id` (the version the editor loaded): if the section changed meanwhile → 409.
    """
    project, _, section = await _load(db, project_id, section_type, user, MemberRole.EDITOR)
    _ensure_editable(project, section_type)
    if section is None:
        section = await svc.get_or_create_section(db, project_id, section_type)
    try:
        version, coalesced = await svc.save_edit(
            db,
            section,
            body.content,
            user.id,
            base_version_id=body.base_version_id,
            autosave=body.autosave,
            change_note=body.change_note,
        )
    except svc.StaleBaseError as exc:
        current = f"v{exc.current.number}" if exc.current else "a newer version"
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This section changed since you opened it (now {current}); reload to continue"
        ) from exc
    await db.commit()
    if not coalesced:
        response.status_code = status.HTTP_201_CREATED
        log.info("section_version_saved", project_id=str(project_id), section=section_type.value, number=version.number)
    return SaveVersionResponse(version=VersionRead.model_validate(version), coalesced=coalesced)


@router.post(
    "/{section_type}/versions/{version_id}/restore", response_model=VersionRead, status_code=status.HTTP_201_CREATED
)
async def restore_version(
    project_id: uuid.UUID, section_type: SectionType, version_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> SectionVersion:
    """Make an old version current again by copying it into a new version (history is kept)."""
    project, _, section = await _load(db, project_id, section_type, user, MemberRole.EDITOR)
    old = await _version_or_404(db, section, version_id)
    _ensure_editable(project, section_type)
    assert section is not None
    version = await svc.restore(db, section, old, user.id)
    await db.commit()
    log.info("section_version_restored", project_id=str(project_id), section=section_type.value, number=old.number)
    return version


@router.get("/{section_type}/diff", response_model=DiffRead)
async def diff_versions(
    project_id: uuid.UUID,
    section_type: SectionType,
    user: CurrentUser,
    db: DbSession,
    from_version: uuid.UUID,
    to_version: uuid.UUID | None = None,
) -> DiffRead:
    """Unified diff between two versions (`to_version` defaults to the current one)."""
    _, _, section = await _load(db, project_id, section_type, user, MemberRole.VIEWER)
    old = await _version_or_404(db, section, from_version)
    assert section is not None
    target = to_version or section.current_version_id
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    new = await _version_or_404(db, section, target)
    result = svc.diff(old, new)
    return DiffRead(
        from_version=old.number,
        to_version=new.number,
        unified=result.unified,
        added=result.added,
        removed=result.removed,
    )
