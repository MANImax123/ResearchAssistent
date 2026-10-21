import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models.enums import StageName, StageStatus
from app.schemas.job import JobRead

Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class StageRunStarted(BaseModel):
    stage: StageName
    status: StageStatus
    job: JobRead


class StageState(BaseModel):
    """A stage plus what the researcher can do with it right now (rules live on the server)."""

    model_config = ConfigDict(from_attributes=True)

    name: StageName
    status: StageStatus
    approved_at: datetime | None
    prerequisites: list[StageName]
    blocked_by: list[StageName]  # prerequisites not approved yet
    can_run: bool
    can_regenerate: bool
    can_go_back: bool


class PendingReviewRead(BaseModel):
    stage: StageName
    draft: str | None


class CheckpointRead(BaseModel):
    thread_id: str
    checkpoint_id: str | None
    next: list[str]


class ProjectStateRead(BaseModel):
    project_id: uuid.UUID
    version: int
    updated_at: datetime | None
    state: dict[str, Any]  # ResearchState snapshot
    stages: list[StageState]
    pending_review: PendingReviewRead | None
    checkpoint: CheckpointRead | None  # None if the graph hasn't run yet or the checkpointer is unreachable


class ApproveRequest(BaseModel):
    comment: Comment | None = None


class RejectRequest(BaseModel):
    comment: Comment  # what should change; given to the agent on the next run


class RegenerateRequest(BaseModel):
    feedback: Comment | None = None


class RunStageRequest(BaseModel):
    instructions: Comment | None = None  # extra guidance for the agent on this run


class EditRequest(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000)]
    change_note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
