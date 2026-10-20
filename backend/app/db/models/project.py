import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin, str_enum
from app.db.models.enums import MemberRole, ProjectStatus, StageName, StageStatus


class Project(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(200))
    target_venue: Mapped[str | None] = mapped_column(String(100))
    citation_style: Mapped[str] = mapped_column(String(50), default="ieee")
    current_stage: Mapped[StageName] = mapped_column(str_enum(StageName), default=StageName.IDEA)
    status: Mapped[ProjectStatus] = mapped_column(str_enum(ProjectStatus), default=ProjectStatus.ACTIVE)

    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    stages: Mapped[list["Stage"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[MemberRole] = mapped_column(str_enum(MemberRole))

    project: Mapped[Project] = relationship(back_populates="members")


class ResearchStateSnapshot(Base):
    """Persisted copy of the orchestrator's `ResearchState` (B12) for a project."""

    __tablename__ = "research_state"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Stage(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "stages"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[StageName] = mapped_column(str_enum(StageName))
    status: Mapped[StageStatus] = mapped_column(str_enum(StageStatus), default=StageStatus.NOT_STARTED)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="stages")
