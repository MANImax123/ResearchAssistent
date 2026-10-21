"""OpenAlex works API: search and lookup by DOI. `OPENALEX_EMAIL` puts requests in the polite pool."""

from typing import Any

from app.services.academic.http import ApiClient
from app.services.academic.models import (
    PaperMetadata,
    clean_text,
    dedupe_authors,
    normalize_arxiv_id,
    normalize_doi,
)

SELECT = (
    "id,doi,display_name,publication_year,primary_location,best_oa_location,cited_by_count,"
    "authorships,abstract_inverted_index,type"
)
# Research outputs only (no datasets, editorials, errata, paratext…)
TYPES = "article|preprint|review|book-chapter"


class OpenAlexClient(ApiClient):
    source = "openalex"
    base_url = "https://api.openalex.org"
    min_interval_s = 0.2

    def __init__(self, email: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._email = email

    async def search(
        self, query: str, *, limit: int, year_from: int | None = None, year_to: int | None = None
    ) -> list[PaperMetadata]:
        filters = [f"type:{TYPES}"]
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        params = {
            "search": query,
            "per-page": min(limit, 200),
            "filter": ",".join(filters),
            "select": SELECT,
            "mailto": self._email,
        }
        data = await self.get_json("/works", params)
        return [p for item in (data or {}).get("results") or [] if (p := parse_work(item))]

    async def lookup(self, kind: str, identifier: str) -> PaperMetadata | None:
        doi = identifier if kind == "doi" else f"10.48550/arxiv.{identifier}"
        data = await self.get_json(f"/works/https://doi.org/{doi}", {"select": SELECT, "mailto": self._email})
        return parse_work(data) if data else None


def abstract_from_index(index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]}."""
    if not index:
        return None
    positions = sorted((pos, word) for word, places in index.items() for pos in places)
    return clean_text(" ".join(word for _, word in positions))


def parse_work(item: dict[str, Any]) -> PaperMetadata | None:
    title = clean_text(item.get("display_name") or item.get("title"))
    if not title:
        return None
    primary = item.get("primary_location") or {}
    best_oa = item.get("best_oa_location") or {}
    doi = normalize_doi(item.get("doi"))
    landing = primary.get("landing_page_url") or ""
    arxiv = normalize_arxiv_id(landing) if "arxiv.org" in landing else None
    if doi and doi.startswith("10.48550/arxiv."):
        arxiv = arxiv or doi.removeprefix("10.48550/arxiv.")
    openalex_id = (item.get("id") or "").rsplit("/", 1)[-1] or None
    names = [(a.get("author") or {}).get("display_name", "") for a in item.get("authorships") or []]
    return PaperMetadata(
        title=title,
        authors=dedupe_authors(names),
        year=item.get("publication_year"),
        venue=clean_text((primary.get("source") or {}).get("display_name")),
        abstract=abstract_from_index(item.get("abstract_inverted_index")),
        doi=doi,
        arxiv_id=arxiv,
        openalex_id=openalex_id,
        url=item.get("doi") or landing or None,
        pdf_url=best_oa.get("pdf_url") or None,
        citation_count=item.get("cited_by_count"),
        sources=["openalex"],
    )
