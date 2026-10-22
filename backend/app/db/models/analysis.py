"""Literature-analysis outputs: comparison matrix, contradictions, candidate gaps."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {"a": [chunk ids], "b": [chunk ids]}
    explanation: Mapped[str | None] = mapped_column(Text)
    # Step 4.7
    topic: Mapped[str | None] = mapped_column(Text)  # what both papers report on, e.g. "PDBbind core set · RMSE"
    explanation_kind: Mapped[str | None] = mapped_column(String(32))  # different_setup | dataset_version | …
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | dismissed
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismiss_reason: Mapped[str | None] = mapped_column(Text)


class ResearchGap(UUIDPkMixin, TimestampMixin, Base):
    """A *candidate* gap (A4.3) — never a confirmed fact until the researcher accepts it."""

    __tablename__ = "research_gaps"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[GapConfidence] = mapped_column(str_enum(GapConfidence))
    status: Mapped[GapStatus] = mapped_column(str_enum(GapStatus), default=GapStatus.CANDIDATE)
    # Step 4.9
    objective_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)  # plan objectives it could serve
    edited: Mapped[bool] = mapped_column(Boolean, default=False)  # the researcher changed the AI's wording
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanRefinement(UUIDPkMixin, CreatedAtMixin, Base):
    """A proposed change to the approved research plan, based on accepted gaps (Step 4.10).

    `change`: {"kind": "modify_objective" | "modify_question" | "add_question" | "add_objective", "target"?: id,
    "text": str, "question_kind"?: "question" | "hypothesis", "linked_objective_ids"?: [ids],
    "question_text"?: str (add_objective: its first question)}
    """

    __tablename__ = "plan_refinements"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    change: Mapped[dict[str, Any]] = mapped_column(JSONB)
    before: Mapped[str | None] = mapped_column(Text)  # the text it replaces (modify_*), for the diff view
    rationale: Mapped[str | None] = mapped_column(Text)
    gap_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed | accepted | rejected
    applied_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)  # objective/question ids it created
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
