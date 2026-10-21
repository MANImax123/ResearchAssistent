"""PaperMetadata: one schema for papers from every academic source, plus identifier helpers."""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SearchSourceName = Literal["semantic_scholar", "openalex", "arxiv", "crossref"]  # sources discovery can search
Source = Literal[SearchSourceName, "manual", "upload"]  # where a library entry came from
SEARCH_SOURCES: tuple[SearchSourceName, ...] = ("semantic_scholar", "openalex", "arxiv", "crossref")

_DOI = re.compile(r"(10\.\d{4,9}/\S+)", re.IGNORECASE)
# New-style (2003.06505, 2003.06505v2) and old-style (cs/0112017) arXiv ids
_ARXIV = re.compile(r"(?:arxiv[:/]\s*|abs/|pdf/)?(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.IGNORECASE)


class Author(BaseModel):
    name: str


class PaperMetadata(BaseModel):
    title: str
    authors: list[Author] = []
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    doi: str | None = None
    s2_id: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    url: str | None = None
    pdf_url: str | None = None  # an open-access PDF location, when the source reports one
    citation_count: int | None = None
    sources: list[Source] = Field(default_factory=list)
    found_by: list[str] = Field(default_factory=list)  # labels of the search queries that found it (Q1…)

    @field_validator("doi")
    @classmethod
    def _doi(cls, v: str | None) -> str | None:
        return normalize_doi(v)

    @field_validator("arxiv_id")
    @classmethod
    def _arxiv(cls, v: str | None) -> str | None:
        return normalize_arxiv_id(v)

    @property
    def has_identifier(self) -> bool:
        """A verified external id: required before a paper may be cited (A4.1)."""
        return any((self.doi, self.s2_id, self.arxiv_id, self.openalex_id))


def normalize_doi(value: str | None) -> str | None:
    """`https://doi.org/10.1/ABC.` → `10.1/abc` (DOIs are case-insensitive)."""
    if not value:
        return None
    match = _DOI.search(value.strip())
    if not match:
        return None
    return match.group(1).rstrip(".,;)").lower()


def normalize_arxiv_id(value: str | None) -> str | None:
    """`arXiv:2003.06505v2`, `https://arxiv.org/abs/2003.06505` → `2003.06505` (version dropped)."""
    if not value:
        return None
    match = _ARXIV.search(value.strip())
    return match.group(1) if match else None


def parse_identifier(value: str) -> tuple[Literal["doi", "arxiv"], str] | None:
    """What the researcher typed into "Add by DOI / arXiv ID" (bare ids or URLs)."""
    text = value.strip()
    if doi := normalize_doi(text):
        # arXiv's own DOIs (10.48550/arXiv.2003.06505) resolve better through arXiv
        if doi.startswith("10.48550/arxiv."):
            return "arxiv", doi.removeprefix("10.48550/arxiv.")
        return "doi", doi
    url_or_id = r"(?:https?://)?(?:www\.)?(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?\S+"
    if re.fullmatch(url_or_id, text, re.IGNORECASE) and (arxiv := normalize_arxiv_id(text)):
        return "arxiv", arxiv
    return None


def normalize_title(title: str) -> str:
    """Lower-case ASCII words only, for comparing titles across sources."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))  # é → e; dashes etc. become separators
    text = re.sub(r"<[^>]+>", " ", text)  # inline markup (Crossref titles carry <i>, <sub>)
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace and strip JATS/HTML markup; None for empty."""
    if not value:
        return None
    text = re.sub(r"<jats:title>\s*Abstract\s*</jats:title>", " ", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def dedupe_authors(names: list[str]) -> list[Author]:
    """Keep order, drop blanks and repeats (OpenAlex sometimes lists an author several times)."""
    seen: set[str] = set()
    authors = []
    for name in names:
        name = re.sub(r"\s+", " ", name or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            authors.append(Author(name=name))
    return authors
