import uuid
from datetime import datetime

from pydantic import BaseModel

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
