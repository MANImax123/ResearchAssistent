"""Literature-analysis outputs: comparison matrix, contradictions, candidate gaps."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin, str_enum
from app.db.models.enums import GapConfidence, GapStatus


class Comparison(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "comparisons"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    matrix: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Contradiction(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "contradictions"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    claim_a: Mapped[str] = mapped_column(Text)
    claim_b: Mapped[str] = mapped_column(Text)
    paper_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    paper_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    explanation: Mapped[str | None] = mapped_column(Text)


class ResearchGap(UUIDPkMixin, TimestampMixin, Base):
    """A *candidate* gap (A4.3) — never a confirmed fact until the researcher accepts it."""

    __tablename__ = "research_gaps"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[GapConfidence] = mapped_column(str_enum(GapConfidence))
    status: Mapped[GapStatus] = mapped_column(str_enum(GapStatus), default=GapStatus.CANDIDATE)
