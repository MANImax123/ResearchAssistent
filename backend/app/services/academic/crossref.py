"""Crossref REST API: bibliographic search and DOI metadata. `CROSSREF_EMAIL` goes in the User-Agent."""

from typing import Any

from app.services.academic.http import USER_AGENT, ApiClient
from app.services.academic.models import PaperMetadata, clean_text, dedupe_authors, normalize_doi

SELECT = "DOI,title,author,issued,container-title,abstract,URL,type,is-referenced-by-count"
# Research outputs only: Crossref also indexes supplementary files ("component"), datasets, reviews…
TYPES = ("journal-article", "proceedings-article", "posted-content", "book-chapter")


class CrossrefClient(ApiClient):
    source = "crossref"
    base_url = "https://api.crossref.org"
    min_interval_s = 1.0  # public pool: 1 request/second

    def __init__(self, email: str = "", **kwargs: Any) -> None:
        agent = f"{USER_AGENT} (mailto:{email})" if email else USER_AGENT
        super().__init__(headers={"User-Agent": agent}, **kwargs)

    async def search(
        self, query: str, *, limit: int, year_from: int | None = None, year_to: int | None = None
    ) -> list[PaperMetadata]:
        filters = [f"type:{t}" for t in TYPES]
        if year_from:
            filters.append(f"from-pub-date:{year_from}-01-01")
        if year_to:
            filters.append(f"until-pub-date:{year_to}-12-31")
        params = {"query.bibliographic": query, "rows": min(limit, 100), "filter": ",".join(filters), "select": SELECT}
        data = await self.get_json("/works", params)
        items = ((data or {}).get("message") or {}).get("items") or []
        return [p for item in items if (p := parse_work(item))]

    async def lookup(self, kind: str, identifier: str) -> PaperMetadata | None:
        if kind != "doi":
            return None
        data = await self.get_json(f"/works/{identifier}")
        return parse_work(data["message"]) if data and data.get("message") else None


def parse_work(item: dict[str, Any]) -> PaperMetadata | None:
    title = clean_text((item.get("title") or [None])[0])
    if not title or item.get("type") == "component":
        return None
    parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = parts[0] if parts and isinstance(parts[0], int) else None
    names = [
        a.get("name") or " ".join(x for x in (a.get("given"), a.get("family")) if x) for a in item.get("author") or []
    ]
    doi = normalize_doi(item.get("DOI"))
    return PaperMetadata(
        title=title,
        authors=dedupe_authors(names),
        year=year,
        venue=clean_text((item.get("container-title") or [None])[0]),
        abstract=clean_text(item.get("abstract")),
        doi=doi,
        url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        citation_count=item.get("is-referenced-by-count"),
        sources=["crossref"],
    )
