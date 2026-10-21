"""L2 Paper Discovery: run the search queries on every source, deduplicate, filter, cap.

Sources run in parallel; within a source queries run one after another (each client spaces its own
requests to the source's rate limit). A source that fails is reported and skipped after two failures
in a row; the others carry on. Results are merged with the project's existing papers, so a paper
already in the library is never added twice (its record is enriched instead).
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.services.academic.dedup import Deduplicator
from app.services.academic.http import SourceError
from app.services.academic.models import PaperMetadata, SearchSourceName
from app.services.academic.sources import SearchClient

log = get_logger(__name__)

MAX_FAILURES_IN_A_ROW = 2
Progress = Callable[[int, str], Awaitable[None]]


@dataclass(frozen=True)
class Filters:
    year_from: int | None = None
    year_to: int | None = None
    venue: str | None = None  # case-insensitive substring of the venue name

    def keeps(self, paper: PaperMetadata) -> bool:
        if paper.year is not None:
            if self.year_from and paper.year < self.year_from:
                return False
            if self.year_to and paper.year > self.year_to:
                return False
        if self.venue:
            return bool(paper.venue) and self.venue.lower() in (paper.venue or "").lower()
        return True


@dataclass
class SourceReport:
    found: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: bool = False


@dataclass
class DiscoveryResult:
    new: list[PaperMetadata]
    updated_existing: dict[int, PaperMetadata]  # index in `existing` → enriched record
    sources: dict[str, SourceReport]
    per_query: dict[str, int]  # label → results returned (all sources, before dedup)
    raw: int  # results returned in total
    filtered_out: int
    over_cap: int  # new papers dropped by max_new


def _priority(paper: PaperMetadata) -> tuple[int, int, int]:
    """Before relevance ranking exists (4.2): papers found by more queries/sources first, then citations."""
    return (len(paper.found_by) + len(paper.sources), paper.citation_count or 0, paper.year or 0)


async def discover(
    queries: list[tuple[str, str]],
    clients: dict[SearchSourceName, SearchClient],
    *,
    per_source: int,
    filters: Filters,
    existing: list[PaperMetadata] | None = None,
    max_new: int = 100,
    progress: Progress | None = None,
) -> DiscoveryResult:
    """`queries`: (label, text) pairs. Returns the deduplicated new papers and what each source did."""
    reports = {source: SourceReport() for source in clients}
    results: dict[tuple[str, str], list[PaperMetadata]] = {}
    total = max(len(queries) * len(clients), 1)
    done = 0

    async def run_source(source: SearchSourceName, client: SearchClient) -> None:
        nonlocal done
        report = reports[source]
        failures = 0
        for label, text in queries:
            if not report.skipped:
                try:
                    papers = await client.search(
                        text, limit=per_source, year_from=filters.year_from, year_to=filters.year_to
                    )
                    results[(label, source)] = papers
                    report.found += len(papers)
                    failures = 0
                except SourceError as exc:
                    failures += 1
                    report.errors.append(f"{label}: {exc.message}")
                    report.skipped = failures >= MAX_FAILURES_IN_A_ROW
            done += 1
            if progress is not None:
                await progress(5 + int(85 * done / total), f"Searched {done}/{total} (query × source)")

    await asyncio.gather(*(run_source(source, client) for source, client in clients.items()))

    dedup = Deduplicator(existing)
    raw = 0
    per_query: dict[str, int] = {}
    for label, _ in queries:
        per_query[label] = 0
        for source in clients:
            for paper in results.get((label, source), []):
                raw += 1
                per_query[label] += 1
                dedup.add(paper.model_copy(update={"found_by": [label]}))

    new = [p for p in dedup.new_papers if filters.keeps(p)]
    filtered_out = len(dedup.new_papers) - len(new)
    new.sort(key=_priority, reverse=True)
    kept, over_cap = new[:max_new], max(len(new) - max_new, 0)
    log.info("discovery_done", raw=raw, new=len(kept), filtered_out=filtered_out, over_cap=over_cap,
             errors={s: len(r.errors) for s, r in reports.items()})  # fmt: skip
    return DiscoveryResult(
        new=kept,
        updated_existing={i: dedup.papers[i] for i in sorted(dedup.updated_existing)},
        sources={source: report for source, report in reports.items()},
        per_query=per_query,
        raw=raw,
        filtered_out=filtered_out,
        over_cap=over_cap,
    )
