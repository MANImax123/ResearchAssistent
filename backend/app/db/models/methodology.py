"""Researcher-provided methodology details (Step 5.1) and the agent's follow-up questions."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin


class MethodologyInput(TimestampMixin, Base):
    """One per project: the structured form + free text (A4.5: the researcher's content wins)."""

    __tablename__ = "methodology_inputs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class MethodologyQuestion(UUIDPkMixin, CreatedAtMixin, Base):
    """A follow-up question about something missing or unclear in the methodology input."""

    __tablename__ = "methodology_questions"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(40))  # the form field it is about, e.g. "metrics"
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)  # why it matters (e.g. which research question needs it)
    kind: Mapped[str] = mapped_column(String(16), default="missing")  # missing | unclear
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | answered | dismissed
    answer: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
