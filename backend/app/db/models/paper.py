import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin, str_enum
from app.db.models.enums import PaperOrigin, ParseStatus, QueryOrigin


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
    # Discovery (Step 4.1)
    origin: Mapped[PaperOrigin] = mapped_column(str_enum(PaperOrigin), default=PaperOrigin.SEARCH)
    sources: Mapped[list[str]] = mapped_column(JSONB, default=list)  # academic sources that returned it
    found_by: Mapped[list[str]] = mapped_column(JSONB, default=list)  # search query labels (Q1…)
    citation_count: Mapped[int | None] = mapped_column(Integer)
    pdf_url: Mapped[str | None] = mapped_column(String(2048))  # open-access PDF reported by a source
    # Relevance ranking (Step 4.2). relevance_score (above) is the LLM's 0–1 judgement for reranked papers.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(get_settings().embedding_dim))  # title + abstract
    similarity: Mapped[float | None] = mapped_column(Float)  # best cosine similarity to a research question
    rq_similarity: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict)  # RQ id → cosine similarity
    relevance_reason: Mapped[str | None] = mapped_column(Text)  # the LLM's "why relevant"
    relevance_rqs: Mapped[list[str]] = mapped_column(JSONB, default=list)  # RQ ids the LLM says it informs
    ranked_for: Mapped[str | None] = mapped_column(String(16))  # fingerprint of the RQs it was judged against
    must_include: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_error: Mapped[str | None] = mapped_column(Text)  # why parsing failed / no PDF was found (Step 4.3)

    profile: Mapped["PaperProfile | None"] = relationship(back_populates="paper", cascade="all, delete-orphan")
    document: Mapped["PaperDocument | None"] = relationship(back_populates="paper", cascade="all, delete-orphan")
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
    # field name -> supporting paper_chunks ids (A4.2 grounding). Text fields (problem, method, results):
    # a list of ids; list fields (dataset, metrics, limitations, tech_stack): one id list per item, by index.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Step 4.5: the analysis run that wrote it, and the fields the researcher has edited since
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    edited_fields: Mapped[list[str]] = mapped_column(JSONB, default=list)

    paper: Mapped[Paper] = relationship(back_populates="profile")


class PaperChunk(UUIDPkMixin, Base):
    __tablename__ = "paper_chunks"
    __table_args__ = (
        # Approximate nearest-neighbour search by cosine distance (Step 4.4)
        Index(
            "ix_paper_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_name: Mapped[str | None] = mapped_column(String(255))
    section_kind: Mapped[str | None] = mapped_column(String(32))  # abstract | methodology | dataset | results …
    text: Mapped[str] = mapped_column(Text)
    # Nullable: chunks are stored at parse time and embedded afterwards (Step 4.4)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(get_settings().embedding_dim))
    page: Mapped[int | None] = mapped_column(Integer)

    paper: Mapped[Paper] = relationship(back_populates="chunks")


class SearchQuery(UUIDPkMixin, CreatedAtMixin, Base):
    """A literature search query (L1). The planning agent proposes them; the researcher edits them."""

    __tablename__ = "search_queries"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(8))  # Q1, Q2… (stable while the list is unchanged)
    text: Mapped[str] = mapped_column(String(300))
    purpose: Mapped[str | None] = mapped_column(Text)
    targets: Mapped[list[str]] = mapped_column(JSONB, default=list)  # research questions / subtopics it serves
    origin: Mapped[QueryOrigin] = mapped_column(str_enum(QueryOrigin), default=QueryOrigin.AI)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_found: Mapped[int | None] = mapped_column(Integer)  # results across sources in the last run


class PaperDocument(TimestampMixin, Base):
    """A paper's parsed full text (Step 4.3): sections with kinds and pages, figure/table captions.

    Kept apart from `papers` because it is large and only needed when a paper is opened.
    """

    __tablename__ = "paper_documents"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL")
    )  # the PDF that was parsed
    pdf_source: Mapped[str] = mapped_column(String(32))  # upload | arxiv | open_access | unpaywall
    pdf_url: Mapped[str | None] = mapped_column(String(2048))  # where it was downloaded from
    parser: Mapped[str] = mapped_column(String(16))  # grobid | pymupdf
    page_count: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    captions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    limitations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    reference_count: Mapped[int] = mapped_column(Integer, default=0)

    paper: Mapped[Paper] = relationship(back_populates="document")
