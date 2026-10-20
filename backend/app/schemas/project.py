import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.db.models.enums import MemberRole, ProjectStatus, StageName, StageStatus

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
OptionalShort = Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=200)]
Venue = Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=100)]
CitationStyle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
RawIdea = Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=20_000)]


class ProjectCreate(BaseModel):
    title: Title
    domain: OptionalShort = None
    raw_idea: RawIdea = None
    target_venue: Venue = None
    citation_style: CitationStyle = "ieee"


class ProjectUpdate(BaseModel):
    """Partial update: only fields present in the request are changed."""

    title: Title | None = None
    domain: OptionalShort = None
    raw_idea: RawIdea = None
    target_venue: Venue = None
    citation_style: CitationStyle | None = None
    status: ProjectStatus | None = None

    @model_validator(mode="after")
    def _required_fields_not_null(self) -> "ProjectUpdate":
        for name in ("title", "citation_style", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class StageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: StageName
    status: StageStatus
    approved_at: datetime | None


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    domain: str | None
    target_venue: str | None
    current_stage: StageName
    status: ProjectStatus
    role: MemberRole
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectSummary):
    citation_style: str
    raw_idea: str | None
    stages: list[StageRead] = Field(description="All 9 stages in pipeline order")
