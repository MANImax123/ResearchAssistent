import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.db.models.enums import StageName, StageStatus
from app.schemas.job import JobRead
from app.schemas.project import StageRead


class StageRunStarted(BaseModel):
    stage: StageName
    status: StageStatus
    job: JobRead


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
    stages: list[StageRead]
    pending_review: PendingReviewRead | None
    checkpoint: CheckpointRead | None  # None if the graph hasn't run yet or the checkpointer is unreachable
