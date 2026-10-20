import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class Reference(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "references"
    __table_args__ = (UniqueConstraint("project_id", "citation_key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("papers.id", ondelete="SET NULL"))
    citation_key: Mapped[str] = mapped_column(String(255))
    csl_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(50))  # crossref / s2 / zotero / bibtex / manual
