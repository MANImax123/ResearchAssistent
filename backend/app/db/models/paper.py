import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin, UUIDPkMixin, str_enum
from app.db.models.enums import ParseStatus


class Paper(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "papers"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # External identifiers — at least one is required before a paper may be cited (A4.1)
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    s2_id: Mapped[str | None] = mapped_column(String(64))
    arxiv_id: Mapped[str | None] = mapped_column(String(64))
    openalex_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(String(500))
    abstract: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(2048))
    pdf_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL")
    )
    relevance_score: Mapped[float | None] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_status: Mapped[ParseStatus] = mapped_column(str_enum(ParseStatus), default=ParseStatus.NOT_STARTED)

    profile: Mapped["PaperProfile | None"] = relationship(back_populates="paper", cascade="all, delete-orphan")
    chunks: Mapped[list["PaperChunk"]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class PaperProfile(TimestampMixin, Base):
    __tablename__ = "paper_profiles"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    problem: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(Text)
    dataset: Mapped[list[str]] = mapped_column(JSONB, default=list)
    metrics: Mapped[list[str]] = mapped_column(JSONB, default=list)
    results: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    tech_stack: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # field name -> list of supporting paper_chunks ids (A4.2 grounding)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    paper: Mapped[Paper] = relationship(back_populates="profile")


class PaperChunk(UUIDPkMixin, Base):
    __tablename__ = "paper_chunks"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_name: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    # Nullable: chunks are stored at parse time and embedded afterwards (Step 4.4)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(get_settings().embedding_dim))
    page: Mapped[int | None] = mapped_column(Integer)

    paper: Mapped[Paper] = relationship(back_populates="chunks")
