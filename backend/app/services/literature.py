"""Literature workspace (Step 4.1): search queries, discovery runs, and the project's paper library."""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import plan_from_state
from app.agents.idea.schemas import IdeaPlan
from app.agents.literature import parsing, ranking
from app.agents.literature.discovery import DiscoveryResult, Filters, Progress, discover
from app.agents.literature.planning import PlannedQuery, plan_queries
from app.core.logging import get_logger
from app.db.models import Paper, Project, SearchQuery
from app.db.models.enums import PaperOrigin, ParseStatus, QueryOrigin, StageName, StageStatus
from app.services import retrieval
from app.services.academic.models import Author, PaperMetadata, SearchSourceName
from app.services.academic.sources import SearchClient
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMService, SessionFactory

log = get_logger(__name__)

EMBED_BATCH = 25  # papers per embedding request (free-tier quotas count texts per minute)


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


# ─── Relevance ranking (Step 4.2) ────────────────────────────────────────


async def _approved_plan(session: AsyncSession, project_id: uuid.UUID) -> tuple[Project, IdeaPlan]:
    project = await session.get(Project, project_id)
    if project is None:
        raise LookupError("Project not found")
    await session.refresh(project, ["research_state", "stages"])
    if reason := blocker(project):
        raise LiteratureBlockedError(reason)
    plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
    if plan is None:
        raise LiteratureBlockedError("The project has no research plan")
    return project, plan


async def rank_papers(
    project_id: uuid.UUID,
    session_factory: SessionFactory,
    *,
    top_n: int,
    embeddings: EmbeddingService | None = None,
    llm: LLMService | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Embed → similarity to every RQ → LLM judgement of the `top_n` most similar (and must-include) papers."""
    embedder = embeddings or registry.embeddings_factory()
    async with session_factory() as session:
        _, plan = await _approved_plan(session, project_id)
        papers = await project_papers(session, project_id)
        to_embed = [p for p in papers if p.embedding is None]
        texts = [ranking.paper_text(p.title, p.abstract) for p in to_embed]
    # Embed in small batches, saving each, so a quota error never loses finished work
    for start in range(0, len(to_embed), EMBED_BATCH):
        if progress:
            await progress(5 + 25 * start // max(len(to_embed), 1), f"Embedding papers ({start}/{len(to_embed)})")
        chunk = to_embed[start : start + EMBED_BATCH]
        vectors = await embedder.embed_documents(texts[start : start + EMBED_BATCH], titles=[p.title for p in chunk])
        async with session_factory() as session:
            for paper, vector in zip(chunk, vectors, strict=True):
                row = await session.get(Paper, paper.id)
                if row is not None:
                    row.embedding = vector
            await session.commit()
    rqs = await ranking.rq_vectors(plan, embedder)
    basis = ranking.rq_fingerprint(plan)

    async with session_factory() as session:
        rows = {p.id: p for p in await project_papers(session, project_id)}
        for paper in rows.values():
            if paper.embedding is None:
                continue
            per_rq = ranking.similarities(list(paper.embedding), rqs)
            paper.rq_similarity = per_rq
            paper.similarity = max(per_rq.values(), default=None)
            if paper.ranked_for != basis:  # judged against other research questions: no longer valid
                paper.relevance_score = paper.relevance_reason = paper.ranked_for = None
                paper.relevance_rqs = []
        await session.commit()
        by_similarity = sorted(rows.values(), key=lambda p: p.similarity or -1.0, reverse=True)
        chosen = {p.id for p in by_similarity[:top_n]} | {p.id for p in rows.values() if p.must_include}
        candidates = [
            ranking.Candidate(str(p.id), p.title, p.abstract, p.year, p.venue)
            for p in by_similarity
            if p.id in chosen and p.relevance_score is None
        ]
    reused = len(chosen) - len(candidates)
    if progress:
        await progress(35, f"AI is judging {len(candidates)} papers ({reused} already judged)")
    judged = await ranking.rerank(candidates, plan, llm or registry.llm_factory(session_factory)(), project_id)

    async with session_factory() as session:
        for key, judgement in judged.items():
            judged_paper = await session.get(Paper, uuid.UUID(key))
            if judged_paper is None or judged_paper.project_id != project_id:
                continue
            judged_paper.relevance_score = judgement.score / 10
            judged_paper.relevance_reason = judgement.reason
            judged_paper.relevance_rqs = judgement.rq_ids
            judged_paper.ranked_for = basis
        await session.commit()
    log.info("papers_ranked", project_id=str(project_id), embedded=len(to_embed), judged=len(judged), reused=reused)
    return {
        "papers": len(rows),
        "embedded": len(to_embed),
        "judged": len(judged),
        "reused": reused,
        "unjudged": len(candidates) - len(judged),
    }


# ─── Selection ───────────────────────────────────────────────────────────


def _relevance_order(paper: Paper) -> tuple[int, float, float]:
    """Judged papers first (by the LLM's score), then by similarity."""
    judged = paper.relevance_score is not None
    return (int(judged), paper.relevance_score or 0.0, paper.similarity or -1.0)


def set_flags(paper: Paper, *, selected: bool | None, must_include: bool | None) -> None:
    """Must-include implies selected; deselecting drops must-include."""
    if must_include is not None:
        paper.must_include = must_include
        if must_include:
            paper.selected = True
    if selected is not None:
        paper.selected = selected
        if not selected:
            paper.must_include = False


async def select_top(session: AsyncSession, project_id: uuid.UUID, k: int, min_score: float | None) -> int:
    """Select the k most relevant papers (must-include ones always stay selected); deselect the rest."""
    papers = sorted(await project_papers(session, project_id), key=_relevance_order, reverse=True)
    eligible = [
        p for p in papers if min_score is None or (p.relevance_score is not None and p.relevance_score >= min_score)
    ]
    keep = {p.id for p in eligible[:k]}
    for paper in papers:
        paper.selected = paper.must_include or paper.id in keep
    await session.flush()
    return sum(p.selected for p in papers)


# ─── Full text (Step 4.3) ────────────────────────────────────────────────


async def papers_to_parse(
    session: AsyncSession, project_id: uuid.UUID, paper_ids: list[uuid.UUID] | None, force: bool
) -> list[uuid.UUID]:
    """The given papers, or every selected paper not parsed yet (all selected ones with `force`)."""
    stmt = select(Paper.id).where(Paper.project_id == project_id)
    if paper_ids:
        stmt = stmt.where(Paper.id.in_(paper_ids))
    else:
        stmt = stmt.where(Paper.selected.is_(True))
        if not force:
            stmt = stmt.where(Paper.parse_status != ParseStatus.PARSED)
    return list(
        await session.scalars(stmt.order_by(Paper.must_include.desc(), Paper.relevance_score.desc().nulls_last()))
    )


async def run_parsing(
    project_id: uuid.UUID,
    session_factory: SessionFactory,
    paper_ids: list[uuid.UUID],
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    outcomes = await parsing.parse_papers(paper_ids, session_factory, registry.parsing_factory(), progress)
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.status.value] = counts.get(outcome.status.value, 0) + 1
    log.info("papers_parsed", project_id=str(project_id), **counts)
    result: dict[str, Any] = {"papers": len(outcomes), **counts}
    if counts.get(ParseStatus.PARSED.value):
        result.update(await index_chunks(project_id, session_factory, progress=progress, start=80))
    return result


async def index_chunks(
    project_id: uuid.UUID, session_factory: SessionFactory, *, progress: Progress | None = None, start: int = 5
) -> dict[str, Any]:
    """Embed passages that aren't searchable yet (Step 4.4). A quota/network failure leaves them pending
    for "Index passages" to retry; it never undoes the parsing."""

    async def scaled(percent: int, message: str) -> None:
        if progress is not None:
            await progress(start + (99 - start) * percent // 100, message)

    try:
        indexed = await retrieval.embed_pending_chunks(
            project_id, session_factory, registry.embeddings_factory(), progress=scaled
        )
        return {"indexed": indexed}
    except Exception as exc:  # noqa: BLE001 - reported in the job result; the chunks stay pending
        log.warning("chunk_indexing_failed", project_id=str(project_id), error=str(exc)[:300])
        return {"indexed": 0, "index_error": f"Indexing for search failed ({type(exc).__name__}); try again."}
