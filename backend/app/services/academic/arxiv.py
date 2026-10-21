"""arXiv API (Atom feed): search and lookup by arXiv id. arXiv asks for one request every 3 seconds."""

import re
import xml.etree.ElementTree as ET

from app.services.academic.http import ApiClient, SourceError
from app.services.academic.models import (
    PaperMetadata,
    clean_text,
    dedupe_authors,
    normalize_arxiv_id,
    normalize_doi,
)

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "of", "on", "or", "the",
     "to", "with", "using", "via"]
)  # fmt: skip


class ArxivClient(ApiClient):
    source = "arxiv"
    base_url = "https://export.arxiv.org/api"
    min_interval_s = 3.0

    async def search(
        self, query: str, *, limit: int, year_from: int | None = None, year_to: int | None = None
    ) -> list[PaperMetadata]:
        params = {
            "search_query": build_query(query, year_from, year_to),
            "max_results": min(limit, 100),
            "sortBy": "relevance",
        }
        return self._parse(await self.get_text("/query", params))

    async def lookup(self, kind: str, identifier: str) -> PaperMetadata | None:
        if kind != "arxiv":
            return None
        papers = self._parse(await self.get_text("/query", {"id_list": identifier, "max_results": 1}))
        return papers[0] if papers else None

    def _parse(self, text: str | None) -> list[PaperMetadata]:
        if not text:
            return []
        try:
            return parse_feed(text)
        except ET.ParseError as exc:
            raise SourceError(self.source, "invalid Atom feed") from exc


def build_query(query: str, year_from: int | None, year_to: int | None) -> str:
    """Every meaningful term must appear (arXiv ORs bare terms); quoted phrases stay phrases."""
    terms = [t for t in re.findall(r'"[^"]+"|[\w-]+', query) if t.strip('"').lower() not in _STOPWORDS]
    parts = [f"all:{t}" for t in terms] or [f'all:"{query}"']
    if year_from or year_to:
        start = f"{year_from or 1991}01010000"
        end = f"{year_to or 2100}12312359"
        parts.append(f"submittedDate:[{start} TO {end}]")
    return " AND ".join(parts)


def _text(entry: ET.Element, path: str) -> str | None:
    node = entry.find(path, NS)
    return node.text if node is not None and node.text else None


def parse_feed(text: str) -> list[PaperMetadata]:
    # arXiv's own feed; the stdlib parser doesn't resolve external entities
    root = ET.fromstring(text)  # noqa: S314
    papers = []
    for entry in root.findall("atom:entry", NS):
        arxiv_id = normalize_arxiv_id(_text(entry, "atom:id"))
        title = clean_text(_text(entry, "atom:title"))
        if not arxiv_id or not title or title == "Error":
            continue
        published = _text(entry, "atom:published") or ""
        pdf = next((link.get("href") for link in entry.findall("atom:link", NS) if link.get("title") == "pdf"), None)
        papers.append(
            PaperMetadata(
                title=title,
                authors=dedupe_authors([a.text or "" for a in entry.findall("atom:author/atom:name", NS)]),
                year=int(published[:4]) if published[:4].isdigit() else None,
                venue=clean_text(_text(entry, "arxiv:journal_ref")) or "arXiv",
                abstract=clean_text(_text(entry, "atom:summary")),
                doi=normalize_doi(_text(entry, "arxiv:doi")),
                arxiv_id=arxiv_id,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=pdf,
                sources=["arxiv"],
            )
        )
    return papers
