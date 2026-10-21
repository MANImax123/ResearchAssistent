"""The academic sources discovery can search, and lookup of one paper by DOI / arXiv id."""

import asyncio
from functools import lru_cache
from typing import Literal, Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.academic.arxiv import ArxivClient
from app.services.academic.crossref import CrossrefClient
from app.services.academic.dedup import merge
from app.services.academic.http import RedisCache, SourceError
from app.services.academic.models import PaperMetadata, SearchSourceName
from app.services.academic.openalex import OpenAlexClient
from app.services.academic.semantic_scholar import SemanticScholarClient

log = get_logger(__name__)

SOURCE_LABEL: dict[str, str] = {
    "semantic_scholar": "Semantic Scholar",
    "openalex": "OpenAlex",
    "arxiv": "arXiv",
    "crossref": "Crossref",
}

# Which sources can resolve each kind of identifier, most authoritative first
LOOKUP_ORDER: dict[str, tuple[SearchSourceName, ...]] = {
    "doi": ("crossref", "openalex", "semantic_scholar"),
    "arxiv": ("arxiv", "openalex", "semantic_scholar"),
}


class SearchClient(Protocol):
    @property
    def source(self) -> SearchSourceName: ...

    async def search(
        self, query: str, *, limit: int, year_from: int | None = None, year_to: int | None = None
    ) -> list[PaperMetadata]: ...

    async def lookup(self, kind: str, identifier: str) -> PaperMetadata | None: ...


@lru_cache
def default_clients() -> dict[SearchSourceName, SearchClient]:
    settings = get_settings()
    cache = RedisCache(settings.redis_url)
    return {
        "semantic_scholar": SemanticScholarClient(settings.s2_api_key, cache=cache),
        "openalex": OpenAlexClient(settings.openalex_email, cache=cache),
        "arxiv": ArxivClient(cache=cache),
        "crossref": CrossrefClient(settings.crossref_email, cache=cache),
    }


async def lookup(
    kind: Literal["doi", "arxiv"], identifier: str, clients: dict[SearchSourceName, SearchClient]
) -> PaperMetadata | None:
    """Ask every source that knows this kind of id (in parallel) and merge what they return.

    None if no source knows the paper; SourceError if none could be reached at all.
    """
    order = [s for s in LOOKUP_ORDER[kind] if s in clients]
    results = await asyncio.gather(*(clients[s].lookup(kind, identifier) for s in order), return_exceptions=True)
    found: PaperMetadata | None = None
    errors: list[SourceError] = []
    for source, result in zip(order, results, strict=True):
        if isinstance(result, SourceError):
            errors.append(result)
        elif isinstance(result, BaseException):
            log.warning("academic_lookup_failed", source=source, error=repr(result))
            errors.append(SourceError(source, "unexpected error"))
        elif result is not None:
            found = result if found is None else merge(found, result)
    if found is None and errors and len(errors) == len(order):
        raise errors[0]
    return found
