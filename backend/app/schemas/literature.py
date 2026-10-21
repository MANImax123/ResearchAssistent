import uuid
from datetime import datetime
from typing import Annotated, Any

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
    sources: list[SourceStatus]
    research_questions: list[dict[str, Any]]  # id + text, to show what each query targets
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
    parse_status: ParseStatus
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
