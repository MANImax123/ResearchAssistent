"""Manuscript figures and tables (auto-numbered per project)."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin, str_enum
from app.db.models.enums import SectionType


class Figure(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "figures"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    section_type: Mapped[SectionType | None] = mapped_column(str_enum(SectionType))
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # chart spec when generated from results
    caption: Mapped[str] = mapped_column(Text, default="")
    number: Mapped[int | None] = mapped_column(Integer)


class ManuscriptTable(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "tables"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    section_type: Mapped[SectionType | None] = mapped_column(str_enum(SectionType))
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {"columns": [...], "rows": [[...]]}
    caption: Mapped[str] = mapped_column(Text, default="")
    number: Mapped[int | None] = mapped_column(Integer)
