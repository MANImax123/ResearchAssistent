import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models.enums import SectionStatus, SectionType, StageName, VersionSource


class VersionMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    source: VersionSource
    created_by: uuid.UUID | None
    created_at: datetime
    change_note: str | None
    agent_run_id: uuid.UUID | None


class VersionRead(VersionMeta):
    content: str


class SectionSummary(BaseModel):
    type: SectionType
    stage: StageName | None  # the stage that drafts it
    status: SectionStatus
    exists: bool
    version_count: int
    current_version: VersionMeta | None
    word_count: int
    editable: bool
    edit_blocked_reason: str | None


class SectionDetail(SectionSummary):
    id: uuid.UUID | None
    content: str


class SaveVersionRequest(BaseModel):
    content: Annotated[str, StringConstraints(max_length=500_000)]
    base_version_id: uuid.UUID | None = None  # the version the editor started from (conflict check)
    autosave: bool = False
    change_note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class SaveVersionResponse(BaseModel):
    version: VersionRead
    coalesced: bool  # True: an existing autosave was updated (or nothing changed) instead of a new version


class DiffRead(BaseModel):
    from_version: int
    to_version: int
    unified: str
    added: int
    removed: int
