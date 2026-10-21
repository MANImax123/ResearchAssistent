import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.agents.idea.critique import CritiqueSet
from app.agents.idea.schemas import IdeaPlan
from app.db.models.enums import StageStatus


class IdeaWorkspaceRead(BaseModel):
    status: StageStatus
    raw_idea: str | None
    plan: IdeaPlan | None
    editable: bool  # the plan can be edited only while the Idea stage awaits review
    edit_blocked_reason: str | None
    generated_at: datetime | None
    agent_run_id: uuid.UUID | None
    pending_feedback: str | None
    critiques: CritiqueSet | None


class ReviseRequest(BaseModel):
    suggestion_ids: list[Annotated[str, StringConstraints(pattern=r"^[NFM][1-9]\d*$")]] = Field(
        min_length=1, max_length=12
    )
