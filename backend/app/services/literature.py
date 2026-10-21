"""Literature workspace (Step 4.1): search queries, discovery runs, and the project's paper library."""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import plan_from_state
from app.agents.literature.discovery import DiscoveryResult, Filters, Progress, discover
from app.agents.literature.planning import PlannedQuery, plan_queries
from app.core.logging import get_logger
from app.db.models import Paper, Project, SearchQuery
from app.db.models.enums import PaperOrigin, QueryOrigin, StageName, StageStatus
from app.services.academic.models import Author, PaperMetadata, SearchSourceName
from app.services.academic.sources import SearchClient
from app.services.llm import LLMService, SessionFactory

log = get_logger(__name__)


class LiteratureBlockedError(Exception):
    pass


def blocker(project: Project) -> str | None:
    """Why the paper library / queries can't be changed now, or None."""
    statuses = {row.name: row.status for row in project.stages}
    if statuses.get(StageName.IDEA) != StageStatus.APPROVED:
        return "Approve the research plan (Idea stage) first"
    literature = statuses.get(StageName.LITERATURE)
    if literature == StageStatus.RUNNING:
        return "The Literature stage is running"
    if literature == StageStatus.APPROVED:
        return "Go back to the Literature stage to change the paper library"
    return None


# ─── Paper rows ⇄ PaperMetadata ──────────────────────────────────────────

_META_FIELDS = ("title", "year", "venue", "abstract", "doi", "s2_id", "arxiv_id", "openalex_id", "url", "pdf_url",
                "citation_count")  # fmt: skip


def to_meta(paper: Paper) -> PaperMetadata:
    return PaperMetadata(
        **{f: getattr(paper, f) for f in _META_FIELDS},
        authors=[Author(name=a["name"]) for a in paper.authors or [] if a.get("name")],
        sources=list(paper.sources or []),
        found_by=list(paper.found_by or []),
    )


def apply_meta(paper: Paper, meta: PaperMetadata) -> None:
    for f in _META_FIELDS:
        setattr(paper, f, getattr(meta, f))
    paper.authors = [a.model_dump() for a in meta.authors]
    paper.sources = list(meta.sources)
    paper.found_by = list(meta.found_by)


def new_paper(project_id: uuid.UUID, meta: PaperMetadata, origin: PaperOrigin) -> Paper:
    paper = Paper(project_id=project_id, origin=origin, selected=False)
    apply_meta(paper, meta)
    return paper


async def project_papers(session: AsyncSession, project_id: uuid.UUID) -> list[Paper]:
    rows = await session.scalars(select(Paper).where(Paper.project_id == project_id).order_by(Paper.created_at))
    return list(rows)


# ─── Queries ─────────────────────────────────────────────────────────────


async def list_queries(session: AsyncSession, project_id: uuid.UUID) -> list[SearchQuery]:
    rows = await session.scalars(
        select(SearchQuery).where(SearchQuery.project_id == project_id).order_by(SearchQuery.position)
    )
    return list(rows)


async def replace_queries(
    session: AsyncSession, project_id: uuid.UUID, queries: Sequence[dict[str, Any]]
) -> list[SearchQuery]:
    """Store the list in order, labelled Q1…; keeps run history for queries whose text didn't change."""
    previous = {q.id: q for q in await list_queries(session, project_id)}
    await session.execute(delete(SearchQuery).where(SearchQuery.project_id == project_id))
    rows = []
    for position, item in enumerate(queries):
        before = previous.get(item["id"]) if item.get("id") else None
        unchanged = before is not None and before.text == item["text"]
        row = SearchQuery(
            id=before.id if before is not None else uuid.uuid4(),
            project_id=project_id,
            position=position,
            label=f"Q{position + 1}",
            text=item["text"],
            purpose=item.get("purpose") or None,
            targets=list(item.get("targets") or []),
            origin=before.origin if unchanged and before else item.get("origin", QueryOrigin.USER),
            last_run_at=before.last_run_at if unchanged and before else None,
            last_found=before.last_found if unchanged and before else None,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def generate_queries(
    project_id: uuid.UUID, session_factory: SessionFactory, *, llm: LLMService | None = None
) -> list[PlannedQuery]:
    """L1: plan queries from the approved research plan and replace the project's query list."""
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise LookupError("Project not found")
        await session.refresh(project, ["research_state", "stages"])
        if reason := blocker(project):
            raise LiteratureBlockedError(reason)
        plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
    if plan is None:
        raise LiteratureBlockedError("The project has no research plan")
    planned = await plan_queries(plan, llm or registry.llm_factory(session_factory)(), project_id)
    async with session_factory() as session:
        items = [
            {"text": q.text, "purpose": q.purpose, "targets": q.targets, "origin": QueryOrigin.AI} for q in planned
        ]
        await replace_queries(session, project_id, items)
        await session.commit()
    log.info("search_queries_planned", project_id=str(project_id), count=len(planned))
    return planned


# ─── Discovery ───────────────────────────────────────────────────────────


async def run_discovery(
    project_id: uuid.UUID,
    session_factory: SessionFactory,
    *,
    query_ids: list[uuid.UUID] | None,
    sources: list[SearchSourceName],
    filters: Filters,
    per_source: int,
    max_new: int,
    clients: dict[SearchSourceName, SearchClient] | None = None,
    progress: Progress | None = None,
) -> DiscoveryResult:
    """Run the queries, add new papers to the library and enrich the ones already there."""
    available = clients if clients is not None else registry.academic_factory()
    chosen = {s: available[s] for s in sources if s in available}
    async with session_factory() as session:
        queries = await list_queries(session, project_id)
        if query_ids:
            queries = [q for q in queries if q.id in set(query_ids)]
        papers = await project_papers(session, project_id)
        existing = [to_meta(p) for p in papers]
    if not queries:
        raise LiteratureBlockedError("Add at least one search query")

    result = await discover(
        [(q.label, q.text) for q in queries],
        chosen,
        per_source=per_source,
        filters=filters,
        existing=existing,
        max_new=max_new,
        progress=progress,
    )

    async with session_factory() as session:
        rows = {p.id: p for p in await project_papers(session, project_id)}
        for index, meta in result.updated_existing.items():
            row = rows.get(papers[index].id)
            if row is not None:
                apply_meta(row, meta)
        for meta in result.new:
            session.add(new_paper(project_id, meta, PaperOrigin.SEARCH))
        now = datetime.now(UTC)
        for query in await list_queries(session, project_id):
            if query.label in result.per_query and any(q.id == query.id for q in queries):
                query.last_run_at = now
                query.last_found = result.per_query[query.label]
        await session.commit()
    return result


def summary(result: DiscoveryResult, label: Callable[[str], str]) -> dict[str, Any]:
    """What the job reports back (shown in the workspace after a run)."""
    return {
        "added": len(result.new),
        "enriched": len(result.updated_existing),
        "raw": result.raw,
        "filtered_out": result.filtered_out,
        "over_cap": result.over_cap,
        "sources": {
            source: {"label": label(source), "found": r.found, "errors": r.errors[:5], "skipped": r.skipped}
            for source, r in result.sources.items()
        },
    }
