"""Semantic Scholar Graph API: paper search and lookup by DOI / arXiv id.

Without `S2_API_KEY` requests share a public pool that often answers 429; the client backs off and,
if it still can't get through, discovery reports the source as rate limited and uses the others.
"""

from typing import Any

from app.services.academic.http import ApiClient
from app.services.academic.models import (
    PaperMetadata,
    clean_text,
    dedupe_authors,
    normalize_arxiv_id,
    normalize_doi,
)

FIELDS = "title,authors,year,venue,abstract,externalIds,url,openAccessPdf,citationCount"


class SemanticScholarClient(ApiClient):
    source = "semantic_scholar"
    base_url = "https://api.semanticscholar.org/graph/v1"
    min_interval_s = 1.1  # 1 request/second with a key

    def __init__(self, api_key: str = "", **kwargs: Any) -> None:
        headers = {"x-api-key": api_key} if api_key else {}
        super().__init__(headers=headers, **kwargs)

    async def search(
        self, query: str, *, limit: int, year_from: int | None = None, year_to: int | None = None
    ) -> list[PaperMetadata]:
        year = f"{year_from or ''}-{year_to or ''}" if (year_from or year_to) else None
        params = {"query": query, "limit": min(limit, 100), "fields": FIELDS, "year": year}
        data = await self.get_json("/paper/search", params)
        return [p for item in (data or {}).get("data") or [] if (p := parse_paper(item))]

    async def lookup(self, kind: str, identifier: str) -> PaperMetadata | None:
        prefix = "DOI" if kind == "doi" else "arXiv"
        data = await self.get_json(f"/paper/{prefix}:{identifier}", {"fields": FIELDS})
        return parse_paper(data) if data else None


def parse_paper(item: dict[str, Any]) -> PaperMetadata | None:
    title = clean_text(item.get("title"))
    if not title:
        return None
    ids = item.get("externalIds") or {}
    pdf = item.get("openAccessPdf") or {}
    return PaperMetadata(
        title=title,
        authors=dedupe_authors([a.get("name", "") for a in item.get("authors") or []]),
        year=item.get("year"),
        venue=clean_text(item.get("venue")),
        abstract=clean_text(item.get("abstract")),
        doi=normalize_doi(ids.get("DOI")),
        arxiv_id=normalize_arxiv_id(ids.get("ArXiv")),
        s2_id=item.get("paperId"),
        url=item.get("url"),
        pdf_url=pdf.get("url") or None,
        citation_count=item.get("citationCount"),
        sources=["semantic_scholar"],
    )
