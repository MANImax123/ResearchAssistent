import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import JobStatus


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    type: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    message: str | None
    result: dict[str, Any] | None = Field(validation_alias="result_ref")
    error: str | None
    created_at: datetime
    updated_at: datetime


class DemoJobRequest(BaseModel):
    project_id: uuid.UUID | None = None
    steps: int = Field(default=5, ge=1, le=50)
    delay_s: float = Field(default=1.0, ge=0, le=10)
    fail_at: int | None = Field(default=None, ge=1, description="Make the job fail at this step (testing)")
