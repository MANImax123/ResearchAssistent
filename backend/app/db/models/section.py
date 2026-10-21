import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin, str_enum
from app.db.models.enums import SectionStatus, SectionType, VersionSource


class Section(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("project_id", "type"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    type: Mapped[SectionType] = mapped_column(str_enum(SectionType))
    # sections <-> section_versions reference each other; use_alter breaks the creation cycle
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("section_versions.id", ondelete="SET NULL", use_alter=True),
    )
    status: Mapped[SectionStatus] = mapped_column(str_enum(SectionStatus), default=SectionStatus.EMPTY)

    versions: Mapped[list["SectionVersion"]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        foreign_keys="SectionVersion.section_id",
        order_by="SectionVersion.created_at",
    )


class SectionVersion(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "section_versions"
    __table_args__ = (UniqueConstraint("section_id", "number"),)

    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3… per section
    content: Mapped[str] = mapped_column(Text, default="")  # markdown with [@key] citations
    source: Mapped[VersionSource] = mapped_column(str_enum(VersionSource))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    change_note: Mapped[str | None] = mapped_column(String(500))

    section: Mapped[Section] = relationship(back_populates="versions", foreign_keys=[section_id])
