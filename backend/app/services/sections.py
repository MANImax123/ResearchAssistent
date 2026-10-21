"""Paper sections and their version history.

Every change creates a numbered `SectionVersion` (markdown with `[@key]` citations); nothing is
overwritten except a researcher's own recent autosave, which is updated in place so typing doesn't
produce hundreds of versions. Restoring copies an old version into a new one.
"""

import difflib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Section, SectionVersion
from app.db.models.enums import SectionStatus, SectionType, StageName, StageStatus, VersionSource

# The section each stage drafts (later steps add more, e.g. results → discussion in 6.3)
STAGE_SECTIONS: dict[StageName, SectionType] = {
    StageName.IDEA: SectionType.ABSTRACT,  # draft abstract (3.3); finalised in 7.2
    StageName.LITERATURE: SectionType.LITERATURE_SURVEY,
    StageName.METHODOLOGY: SectionType.METHODOLOGY,
    StageName.RESULTS: SectionType.RESULTS,
    StageName.INTRODUCTION: SectionType.INTRODUCTION,
    StageName.CONCLUSION: SectionType.CONCLUSION,
    StageName.REFERENCES: SectionType.REFERENCES,
}
SECTION_STAGE: dict[SectionType, StageName] = {section: stage for stage, section in STAGE_SECTIONS.items()}

SECTION_STATUS_FOR_STAGE: dict[StageStatus, SectionStatus] = {
    StageStatus.NOT_STARTED: SectionStatus.EMPTY,
    StageStatus.RUNNING: SectionStatus.DRAFT,
    StageStatus.AWAITING_REVIEW: SectionStatus.DRAFT,
    StageStatus.REJECTED: SectionStatus.DRAFT,
    StageStatus.APPROVED: SectionStatus.APPROVED,
    StageStatus.STALE: SectionStatus.STALE,
}

AUTOSAVE_NOTE = "Autosave"
AUTOSAVE_WINDOW = timedelta(minutes=10)


class StaleBaseError(Exception):
    """The editor's base version isn't the current one: someone else changed the section."""

    def __init__(self, current: SectionVersion | None) -> None:
        super().__init__("This section changed since you opened it")
        self.current = current


@dataclass(frozen=True)
class Diff:
    unified: str
    added: int
    removed: int


async def get_section(session: AsyncSession, project_id: uuid.UUID, section_type: SectionType) -> Section | None:
    return await session.scalar(
        select(Section)
        .where(Section.project_id == project_id, Section.type == section_type)
        .execution_options(populate_existing=True)
    )


async def get_or_create_section(session: AsyncSession, project_id: uuid.UUID, section_type: SectionType) -> Section:
    section = await get_section(session, project_id, section_type)
    if section is None:
        section = Section(project_id=project_id, type=section_type, status=SectionStatus.EMPTY)
        session.add(section)
        await session.flush()
    return section


async def current_version(session: AsyncSession, section: Section) -> SectionVersion | None:
    if section.current_version_id is None:
        return None
    return await session.get(SectionVersion, section.current_version_id, populate_existing=True)


async def add_version(
    session: AsyncSession,
    section: Section,
    content: str,
    source: VersionSource,
    *,
    created_by: uuid.UUID | None = None,
    change_note: str | None = None,
    agent_run_id: uuid.UUID | None = None,
) -> SectionVersion:
    """Append a version and make it current (caller commits)."""
    last = await session.scalar(select(func.max(SectionVersion.number)).where(SectionVersion.section_id == section.id))
    version = SectionVersion(
        section_id=section.id,
        number=(last or 0) + 1,
        content=content,
        source=source,
        created_by=created_by,
        change_note=change_note,
        agent_run_id=agent_run_id,
    )
    session.add(version)
    await session.flush()
    section.current_version_id = version.id
    if section.status == SectionStatus.EMPTY:
        section.status = SectionStatus.DRAFT
    return version


def edited_source(base: SectionVersion | None) -> VersionSource:
    """A researcher's edit of AI text is `ai_edited`; of their own text, `user`."""
    if base is not None and base.source in (VersionSource.AI, VersionSource.AI_EDITED):
        return VersionSource.AI_EDITED
    return VersionSource.USER


async def save_edit(
    session: AsyncSession,
    section: Section,
    content: str,
    user_id: uuid.UUID,
    *,
    base_version_id: uuid.UUID | None,
    autosave: bool,
    change_note: str | None = None,
) -> tuple[SectionVersion, bool]:
    """Save the researcher's text. Returns (version, coalesced).

    Autosaves update the researcher's own recent autosave in place; explicit saves always add a
    version. `base_version_id` must be the current version (optimistic concurrency).
    """
    current = await current_version(session, section)
    if current is not None and base_version_id is not None and base_version_id != current.id:
        raise StaleBaseError(current)
    if current is not None and current.content == content:
        if not autosave and current.change_note == AUTOSAVE_NOTE:
            # "Save version" on unchanged text: keep the autosave as a named version, so later
            # typing starts a new version instead of merging into this one
            current.change_note = change_note or "Saved by researcher"
            await session.flush()
        return current, True  # no new version
    if (
        autosave
        and current is not None
        and current.created_by == user_id
        and current.change_note == AUTOSAVE_NOTE
        and current.created_at >= datetime.now(UTC) - AUTOSAVE_WINDOW
    ):
        current.content = content
        await session.flush()
        return current, True
    version = await add_version(
        session,
        section,
        content,
        edited_source(current),
        created_by=user_id,
        change_note=AUTOSAVE_NOTE if autosave else change_note,
    )
    return version, False


async def restore(session: AsyncSession, section: Section, old: SectionVersion, user_id: uuid.UUID) -> SectionVersion:
    """Copy an old version into a new current version (history is never rewritten)."""
    return await add_version(
        session, section, old.content, old.source, created_by=user_id, change_note=f"Restored from v{old.number}"
    )


def diff(old: SectionVersion, new: SectionVersion) -> Diff:
    lines = list(
        difflib.unified_diff(
            old.content.splitlines(),
            new.content.splitlines(),
            fromfile=f"v{old.number}",
            tofile=f"v{new.number}",
            lineterm="",
        )
    )
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return Diff(unified="\n".join(lines), added=added, removed=removed)


def edit_blocker(section_type: SectionType, stage_statuses: dict[StageName, StageStatus]) -> str | None:
    """Why the section can't be edited right now, or None.

    A stage's section is editable only while that stage awaits review: approved content changes go
    through Go back, so the staleness rule always sees them.
    """
    stage = SECTION_STAGE.get(section_type)
    if stage is None:
        return None
    status = stage_statuses.get(stage, StageStatus.NOT_STARTED)
    if status == StageStatus.AWAITING_REVIEW:
        return None
    if status == StageStatus.NOT_STARTED:
        return "Run this stage first"
    if status == StageStatus.RUNNING:
        return "The agent is writing this section"
    return "Go back to this stage to edit it"
