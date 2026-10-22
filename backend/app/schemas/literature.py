import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, model_validator

from app.db.models.enums import PaperOrigin, ParseStatus, QueryOrigin, StageStatus
from app.services.academic.models import SEARCH_SOURCES, SearchSourceName

Year = Annotated[int, Field(ge=1900, le=2100)]


class QueryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    text: str
    purpose: str | None
    targets: list[str]
    origin: QueryOrigin
    last_run_at: datetime | None
    last_found: int | None


class QueryWrite(BaseModel):
    id: uuid.UUID | None = None  # keep an existing query's history when its text is unchanged
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    purpose: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    targets: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )


class QueriesWrite(BaseModel):
    queries: list[QueryWrite] = Field(max_length=30)


class SourceStatus(BaseModel):
    name: SearchSourceName
    label: str
    configured: bool  # API key / contact email set (better rate limits)
    note: str


class LiteratureWorkspaceRead(BaseModel):
    status: StageStatus
    editable: bool
    blocked_reason: str | None
    queries: list[QueryRead]
    paper_count: int
    selected_count: int
    must_include_count: int
    judged_count: int  # papers the LLM judged for the current research questions
    unranked_count: int  # papers without a similarity score yet (added since the last ranking)
    parse_counts: dict[str, int]  # selected papers by parse status
    indexed_chunks: int  # passages searchable by "Ask the literature"
    pending_chunks: int  # parsed passages not embedded yet
    profiled_count: int  # selected papers with an analysis profile
    sources: list[SourceStatus]
    research_questions: list[dict[str, Any]]  # id + text, to show what each query targets
    objectives: list[dict[str, Any]] = []  # id + text, for linking gaps to objectives
    subtopics: list[str]


class DiscoverRequest(BaseModel):
    query_ids: list[uuid.UUID] | None = None  # None: every query
    sources: list[SearchSourceName] = Field(default_factory=lambda: list(SEARCH_SOURCES), min_length=1)
    year_from: Year | None = None
    year_to: Year | None = None
    venue: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    per_source: int = Field(default=10, ge=3, le=50)  # results requested per query per source
    max_new: int = Field(default=100, ge=5, le=200)  # cap on papers added by one run

    @model_validator(mode="after")
    def _years(self) -> "DiscoverRequest":
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must not be after year_to")
        return self


class PaperRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    authors: list[dict[str, Any]]
    year: int | None
    venue: str | None
    abstract: str | None
    doi: str | None
    s2_id: str | None
    arxiv_id: str | None
    openalex_id: str | None
    url: str | None
    pdf_url: str | None
    pdf_file_id: uuid.UUID | None
    citation_count: int | None
    origin: PaperOrigin
    sources: list[str]
    found_by: list[str]
    selected: bool
    must_include: bool
    similarity: float | None
    rq_similarity: dict[str, float]
    relevance_score: float | None  # LLM judgement, 0–1
    relevance_reason: str | None
    relevance_rqs: list[str]
    parse_status: ParseStatus
    parse_error: str | None
    has_profile: bool = False  # set by the library list
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_identifier(self) -> bool:
        """A verified external id: needed before the paper can be cited (A4.1)."""
        return any((self.doi, self.s2_id, self.arxiv_id, self.openalex_id))


class PaperPage(BaseModel):
    total: int
    items: list[PaperRead]


class LookupRequest(BaseModel):
    identifier: Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=300)]


class LookupResult(BaseModel):
    paper: PaperRead
    created: bool  # False: it was already in the library


class RankRequest(BaseModel):
    top_n: int = Field(default=40, ge=1, le=200)  # most similar papers the LLM judges


class PaperFlags(BaseModel):
    selected: bool | None = None
    must_include: bool | None = None  # implies selected


class SelectionRequest(BaseModel):
    mode: Literal["top", "none"]
    k: int = Field(default=20, ge=1, le=200)  # "top": how many to select
    min_score: float | None = Field(default=None, ge=0, le=1)  # "top": only papers the LLM scored at least this


class SelectionResult(BaseModel):
    selected: int


class ParseRequest(BaseModel):
    paper_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)  # None: the selected papers
    force: bool = False  # also re-parse papers that are already parsed


class DocumentSection(BaseModel):
    heading: str
    title: str
    number: str | None
    kind: str
    page: int | None
    text: str


class DocumentCaption(BaseModel):
    kind: str
    label: str
    text: str
    page: int | None


class LimitationMention(BaseModel):
    section: str
    text: str
    page: int | None


class PaperDocumentRead(BaseModel):
    paper: PaperRead
    parser: str
    pdf_source: str
    pdf_url: str | None
    page_count: int | None
    abstract: str | None
    sections: list[DocumentSection]
    captions: list[DocumentCaption]
    limitations: list[LimitationMention]
    reference_count: int
    chunk_count: int


SectionKindName = Literal[
    "abstract", "introduction", "related_work", "methodology", "dataset", "results", "discussion",
    "limitations", "conclusion", "appendix", "other",
]  # fmt: skip


class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
    section_kinds: list[SectionKindName] | None = None  # only search these parts of the papers
    paper_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    k: int = Field(default=8, ge=3, le=15)  # passages given to the model


class SourceRead(BaseModel):
    marker: str  # "S3", as cited in the answer
    chunk_id: uuid.UUID
    paper_id: uuid.UUID
    paper_title: str
    year: int | None
    section: str | None
    kind: str | None
    page: int | None
    text: str
    similarity: float
    pdf_file_id: uuid.UUID | None  # the stored PDF, to open it at `page`


class AnswerRead(BaseModel):
    answer: str
    insufficient: bool  # the passages didn't (fully) answer the question
    citations: list[SourceRead]  # every [S#] in the answer, in order of first use
    retrieved: int  # passages the model was given
    removed_markers: list[str]  # citations the model made up, removed from the answer


class AnalyzeRequest(BaseModel):
    paper_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)  # None: selected parsed papers
    force: bool = False  # also re-analyse papers that already have a profile (replaces researcher edits)


class EvidenceRead(BaseModel):
    chunk_id: uuid.UUID
    section: str | None
    kind: str | None
    page: int | None
    text: str


class ProfileValue(BaseModel):
    value: str
    evidence: list[EvidenceRead]


class ProfileRead(BaseModel):
    paper_id: uuid.UUID
    problem: ProfileValue | None
    method: ProfileValue | None
    results: ProfileValue | None
    dataset: list[ProfileValue]
    metrics: list[ProfileValue]
    limitations: list[ProfileValue]
    tech_stack: list[ProfileValue]
    edited_fields: list[str]  # fields the researcher changed after the analysis
    updated_at: datetime


ProfileText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)]


class ProfileItemWrite(BaseModel):
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    evidence: list[uuid.UUID] = Field(default_factory=list, max_length=20)  # kept from the analysis, or empty


class ProfileWrite(BaseModel):
    problem: ProfileText | None = None
    method: ProfileText | None = None
    results: ProfileText | None = None
    dataset: list[ProfileItemWrite] = Field(default_factory=list, max_length=50)
    metrics: list[ProfileItemWrite] = Field(default_factory=list, max_length=50)
    limitations: list[ProfileItemWrite] = Field(default_factory=list, max_length=50)
    tech_stack: list[ProfileItemWrite] = Field(default_factory=list, max_length=50)


# ─── Comparison matrix (Step 4.6) ────────────────────────────────────────


class ComparisonColumn(BaseModel):
    key: str
    label: str
    builtin: bool


class ComparisonCell(BaseModel):
    value: str
    source: Literal["profile", "abstract", "ai", "user"]
    evidence: list[EvidenceRead]


class ComparisonPaper(BaseModel):
    id: uuid.UUID
    title: str
    year: int | None
    venue: str | None
    parse_status: ParseStatus
    has_profile: bool


class ComparisonRow(BaseModel):
    paper: ComparisonPaper
    cells: dict[str, ComparisonCell]


class ComparisonRead(BaseModel):
    columns: list[ComparisonColumn]
    rows: list[ComparisonRow]  # one per selected paper
    version: int
    generated_at: datetime | None
    uncompared: int  # selected papers with no built-in cells yet


class CompareRequest(BaseModel):
    force: bool = False  # rebuild every row's AI cells (the researcher's cells are always kept)


ColumnLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class ColumnCreate(BaseModel):
    label: ColumnLabel


class ColumnUpdate(BaseModel):
    label: ColumnLabel | None = None
    remove: bool = False


class CellWrite(BaseModel):
    paper_id: uuid.UUID
    key: Annotated[str, StringConstraints(max_length=40)]
    value: Annotated[str, StringConstraints(max_length=1000)]


class InsertTableRequest(BaseModel):
    caption: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)] = (
        "Comparison of the selected studies."
    )
    columns: list[str] | None = None  # column keys to include (default: all)


class InsertTableResult(BaseModel):
    table_number: int
    section_version: int


# ─── Contradictions (Step 4.7) ───────────────────────────────────────────


class ClaimSide(BaseModel):
    paper_id: uuid.UUID
    title: str
    year: int | None
    claim: str
    evidence: list[EvidenceRead]


class ContradictionRead(BaseModel):
    id: uuid.UUID
    topic: str | None
    a: ClaimSide
    b: ClaimSide
    explanation: str | None
    explanation_kind: str | None
    status: Literal["open", "dismissed"]
    dismiss_reason: str | None
    dismissed_at: datetime | None
    created_at: datetime


class DismissRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


# ─── Step 4.9: potential research gaps ───

GapSignalKind = Literal["limitation", "contradiction", "untested", "future_work"]


class PaperRef(BaseModel):
    id: uuid.UUID
    title: str
    year: int | None


class GapSignalRead(BaseModel):
    kind: GapSignalKind
    text: str
    papers: list[PaperRef]
    evidence: list[EvidenceRead]


class ObjectiveRef(BaseModel):
    id: str
    text: str


class GapRead(BaseModel):
    id: uuid.UUID
    statement: str
    rationale: str | None
    confidence: Literal["low", "medium", "high"]
    status: Literal["candidate", "accepted", "rejected"]
    objectives: list[ObjectiveRef]
    signals: list[GapSignalRead]
    edited: bool
    decided_at: datetime | None
    created_at: datetime


class GapUpdate(BaseModel):
    statement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)] | None = None
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1500)] | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    objective_ids: list[str] | None = None
    status: Literal["candidate", "accepted", "rejected"] | None = None
