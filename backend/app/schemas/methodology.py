import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, model_validator

from app.agents.methodology.schemas import MethodologyInputData
from app.db.models.enums import StageStatus


class MissingRead(BaseModel):
    field: str
    label: str
    reason: str
    essential: bool


class QuestionRead(BaseModel):
    id: uuid.UUID
    field: str
    label: str
    question: str
    reason: str | None
    kind: Literal["missing", "unclear"]
    status: Literal["open", "answered", "dismissed"]
    answer: str | None
    answered_at: datetime | None
    created_at: datetime


class FileCounts(BaseModel):
    diagram: int = 0
    code: int = 0
    notes: int = 0


class MethodologyWorkspaceRead(BaseModel):
    input: MethodologyInputData
    version: int  # 0: nothing saved yet
    updated_at: datetime | None
    editable: bool
    blocked_reason: str | None
    stage_status: StageStatus
    missing: list[MissingRead]
    files: FileCounts
    questions: list[QuestionRead]


class MethodologySave(BaseModel):
    input: MethodologyInputData
    base_version: int | None = None  # the version the edit started from (409 if it changed meanwhile)


class QuestionUpdate(BaseModel):
    status: Literal["open", "answered", "dismissed"]
    answer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)] | None = None

    @model_validator(mode="after")
    def _answer_needed(self) -> "QuestionUpdate":
        if self.status == "answered" and not self.answer:
            raise ValueError("An answer is needed to mark the question answered")
        return self
