"""Operational records: agent runs, researcher feedback, background jobs."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin, str_enum
from app.db.models.enums import FeedbackAction, JobStatus, RunStatus, StageName


class AgentRun(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_runs"

    # Nullable: dev/debug LLM calls (Step 1.4) are not tied to a project
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(100), index=True)
    stage: Mapped[StageName | None] = mapped_column(str_enum(StageName))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), default=RunStatus.RUNNING)
    error: Mapped[str | None] = mapped_column(Text)


class Feedback(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "feedback"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[StageName] = mapped_column(str_enum(StageName))
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[FeedbackAction] = mapped_column(str_enum(FeedbackAction))
    comment: Mapped[str | None] = mapped_column(Text)


class Job(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    type: Mapped[str] = mapped_column(String(100))
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.PENDING)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    message: Mapped[str | None] = mapped_column(String(500))  # human-readable progress, e.g. "Parsing 3 of 12"
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
