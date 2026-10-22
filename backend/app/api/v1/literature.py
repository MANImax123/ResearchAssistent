"""Literature workspace: search queries, discovery runs, the paper library, adding papers by DOI /
arXiv id or PDF (Step 4.1); relevance ranking and the paper selection (Step 4.2); full text: PDF
acquisition, parsing and the parsed document (Step 4.3)."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, or_, select

from app.agents import registry
from app.agents.idea.agent import plan_from_state
from app.agents.literature import rag
from app.api.v1.files import StorageDep, save_upload
from app.api.v1.jobs import DispatcherDep, start_job
from app.core.deps import CurrentUser, DbSession, SettingsDep
from app.core.logging import get_logger
from app.db.models import Contradiction, Job, ManuscriptTable, Paper, PaperChunk, PaperDocument, PaperProfile, Project
from app.db.models.enums import FileKind, JobStatus, MemberRole, PaperOrigin, ParseStatus, SectionType, StageName
from app.db.session import request_session_factory
from app.orchestrator.service import selected_paper_ids
from app.schemas.job import JobRead
from app.schemas.literature import (
    AnalyzeRequest,
    AnswerRead,
    AskRequest,
    CellWrite,
    ClaimSide,
    ColumnCreate,
    ColumnUpdate,
    CompareRequest,
    ComparisonCell,
    ComparisonColumn,
    ComparisonPaper,
    ComparisonRead,
    ComparisonRow,
    ContradictionRead,
    DiscoverRequest,
    DismissRequest,
    EvidenceRead,
    InsertTableRequest,
    InsertTableResult,
    LiteratureWorkspaceRead,
    LookupRequest,
    LookupResult,
    PaperDocumentRead,
    PaperFlags,
    PaperPage,
    PaperRead,
    ParseRequest,
    ProfileRead,
    ProfileValue,
    ProfileWrite,
    QueriesWrite,
    QueryRead,
    RankRequest,
    SelectionRequest,
    SelectionResult,
    SourceRead,
    SourceStatus,
)
from app.services import comparison, contradictions, literature, retrieval, sections
from app.services.academic import sources as academic
from app.services.academic.dedup import Deduplicator
from app.services.academic.http import SourceError
from app.services.academic.models import PaperMetadata, parse_identifier
from app.services.llm import LLMError
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/literature", tags=["literature"])
log = get_logger(__name__)

LITERATURE_JOBS = ("literature_plan", "literature_discover", "literature_rank", "literature_parse", "literature_index",
                   "literature_analyze", "literature_compare", "literature_contradictions",
                   "research_gaps", "literature_refine")  # fmt: skip
SortKey = Literal["relevance", "added", "year", "citations", "title"]


def _source_statuses(settings: Any) -> list[SourceStatus]:
    return [
        SourceStatus(
            name="semantic_scholar",
            label="Semantic Scholar",
            configured=bool(settings.s2_api_key),
            note="API key set" if settings.s2_api_key else "No API key: shared pool, often rate limited",
        ),
        SourceStatus(
            name="openalex",
            label="OpenAlex",
            configured=bool(settings.openalex_email),
            note="Polite pool" if settings.openalex_email else "Set OPENALEX_EMAIL for the polite pool",
        ),
        SourceStatus(name="arxiv", label="arXiv", configured=True, note="Preprints; 1 request every 3 s"),
        SourceStatus(
            name="crossref",
            label="Crossref",
            configured=bool(settings.crossref_email),
            note="Polite pool" if settings.crossref_email else "Set CROSSREF_EMAIL for the polite pool",
        ),
    ]


def _ensure_editable(project: Project) -> None:
    if reason := literature.blocker(project):
        raise HTTPException(status.HTTP_409_CONFLICT, reason)


async def _ensure_idle(db: DbSession, project: Project) -> None:
    _ensure_editable(project)
    busy = await db.scalar(
        select(Job.id).where(
            Job.project_id == project.id,
            Job.type.in_(LITERATURE_JOBS),
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
        )
    )
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "A literature search is already running")


@router.get("", response_model=LiteratureWorkspaceRead)
async def get_workspace(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession, settings: SettingsDep
) -> LiteratureWorkspaceRead:
    project, role = await get_project_for_user(db, project_id, user)
    reason = literature.blocker(project) if role != MemberRole.VIEWER else "You have view-only access"
    plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
    counts = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Paper.selected.is_(True)),
                func.count().filter(Paper.must_include.is_(True)),
                func.count().filter(Paper.relevance_score.is_not(None)),
                func.count().filter(Paper.similarity.is_(None)),
            ).where(Paper.project_id == project_id)
        )
    ).one()
    parse_rows = await db.execute(
        select(Paper.parse_status, func.count())
        .where(Paper.project_id == project_id, Paper.selected.is_(True))
        .group_by(Paper.parse_status)
    )
    indexed, pending = await retrieval.chunk_counts(db, project_id)
    profiled = await db.scalar(
        select(func.count())
        .select_from(PaperProfile)
        .join(Paper, Paper.id == PaperProfile.paper_id)
        .where(Paper.project_id == project_id, Paper.selected.is_(True))
    )
    return LiteratureWorkspaceRead(
        status=next(row.status for row in project.stages if row.name == StageName.LITERATURE),
        editable=reason is None,
        blocked_reason=reason,
        queries=[QueryRead.model_validate(q) for q in await literature.list_queries(db, project_id)],
        paper_count=counts[0],
        selected_count=counts[1],
        must_include_count=counts[2],
        judged_count=counts[3],
        unranked_count=counts[4],
        parse_counts={status.value: n for status, n in parse_rows.all()},
        indexed_chunks=indexed,
        pending_chunks=pending,
        profiled_count=profiled or 0,
        sources=_source_statuses(settings),
        research_questions=[{"id": q.id, "text": q.text} for q in plan.research_questions] if plan else [],
        objectives=[{"id": o.id, "text": o.text} for o in plan.objectives] if plan else [],
        subtopics=list(plan.subtopics) if plan else [],
    )


# ─── Queries ─────────────────────────────────────────────────────────────


@router.put("/queries", response_model=list[QueryRead])
async def save_queries(project_id: uuid.UUID, body: QueriesWrite, user: CurrentUser, db: DbSession) -> list[Any]:
    """Replace the query list with the researcher's edits (relabelled Q1… in this order)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    rows = await literature.replace_queries(db, project_id, [q.model_dump() for q in body.queries])
    await db.commit()
    return [QueryRead.model_validate(r) for r in rows]


@router.post("/queries/generate", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def generate_queries(project_id: uuid.UUID, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep) -> Job:
    """L1: the planning agent proposes queries from the approved research plan (replaces the list)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    if plan_from_state(project.research_state.snapshot if project.research_state else {}) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The project has no research plan")
    return await start_job(db, dispatcher, user, "literature_plan", {"project_id": str(project_id)}, project_id)


@router.post("/discover", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def start_discovery(
    project_id: uuid.UUID, body: DiscoverRequest, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """L2: run the queries on the chosen sources; new, deduplicated papers join the library."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    queries = await literature.list_queries(db, project_id)
    if body.query_ids:
        known = {q.id for q in queries}
        if unknown := [str(q) for q in body.query_ids if q not in known]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown queries: {', '.join(unknown)}")
    elif not queries:
        raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one search query")
    params = {
        "project_id": str(project_id),
        **body.model_dump(mode="json", exclude={"query_ids"}),
        "query_ids": [str(q) for q in body.query_ids] if body.query_ids else None,
    }
    return await start_job(db, dispatcher, user, "literature_discover", params, project_id)


# ─── Paper library ───────────────────────────────────────────────────────


@router.get("/papers", response_model=PaperPage)
async def list_papers(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    source: str | None = None,
    origin: PaperOrigin | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: SortKey = "added",
    selected: bool | None = None,
    rq: Annotated[str | None, Query(max_length=10)] = None,  # papers the LLM said inform this research question
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaperPage:
    await get_project_for_user(db, project_id, user)
    stmt = select(Paper).where(Paper.project_id == project_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Paper.title.ilike(pattern), Paper.venue.ilike(pattern), Paper.doi.ilike(pattern)))
    if source:
        stmt = stmt.where(Paper.sources.contains([source]))
    if origin:
        stmt = stmt.where(Paper.origin == origin)
    if year_from:
        stmt = stmt.where(Paper.year >= year_from)
    if year_to:
        stmt = stmt.where(Paper.year <= year_to)
    if selected is not None:
        stmt = stmt.where(Paper.selected.is_(selected))
    if rq:
        stmt = stmt.where(Paper.relevance_rqs.contains([rq.upper()]))
    if min_score is not None:
        stmt = stmt.where(Paper.relevance_score >= min_score)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order: tuple[Any, ...] = {
        "relevance": (
            Paper.relevance_score.desc().nulls_last(),
            Paper.similarity.desc().nulls_last(),
            Paper.title,
        ),
        "added": (Paper.created_at.desc(), Paper.title),
        "year": (Paper.year.desc().nulls_last(), Paper.title),
        "citations": (Paper.citation_count.desc().nulls_last(), Paper.title),
        "title": (Paper.title,),
    }[sort]
    rows = list(await db.scalars(stmt.order_by(*order).limit(limit).offset(offset)))
    profiled = set(
        await db.scalars(select(PaperProfile.paper_id).where(PaperProfile.paper_id.in_([p.id for p in rows])))
    )
    items = [PaperRead.model_validate(p).model_copy(update={"has_profile": p.id in profiled}) for p in rows]
    return PaperPage(total=total, items=items)


@router.delete("/papers/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_paper(project_id: uuid.UUID, paper_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    paper = await db.scalar(select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id))
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    await db.delete(paper)
    await _mirror_selection(db, project)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── Relevance ranking & selection (Step 4.2) ────────────────────────────


async def _mirror_selection(db: DbSession, project: Project) -> None:
    """Keep ResearchState's `selected_paper_ids` (as stored in the DB mirror) in step with the papers."""
    await db.flush()
    if project.research_state is not None:
        ids = await selected_paper_ids(db, project.id)
        project.research_state.snapshot = {**project.research_state.snapshot, "selected_paper_ids": ids}


@router.post("/rank", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def rank(
    project_id: uuid.UUID, body: RankRequest, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """L3: similarity of every paper to the research questions; the LLM judges the `top_n` most similar."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    if not await db.scalar(select(func.count()).select_from(Paper).where(Paper.project_id == project_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Find some papers first")
    params = {"project_id": str(project_id), "top_n": body.top_n}
    return await start_job(db, dispatcher, user, "literature_rank", params, project_id)


@router.patch("/papers/{paper_id}", response_model=PaperRead)
async def update_paper_flags(
    project_id: uuid.UUID, paper_id: uuid.UUID, body: PaperFlags, user: CurrentUser, db: DbSession
) -> PaperRead:
    """Select / deselect a paper, or mark it "must include" (which also selects it)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    paper = await db.scalar(select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id))
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    literature.set_flags(paper, selected=body.selected, must_include=body.must_include)
    await _mirror_selection(db, project)
    await db.commit()
    return PaperRead.model_validate(paper)


@router.post("/selection", response_model=SelectionResult)
async def bulk_select(
    project_id: uuid.UUID, body: SelectionRequest, user: CurrentUser, db: DbSession
) -> SelectionResult:
    """ "top": select the k most relevant papers (must-include papers stay selected); "none": clear the rest."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    k = body.k if body.mode == "top" else 0
    count = await literature.select_top(db, project_id, k, body.min_score if body.mode == "top" else None)
    await _mirror_selection(db, project)
    await db.commit()
    log.info("papers_selected", project_id=str(project_id), mode=body.mode, selected=count)
    return SelectionResult(selected=count)


async def _resolve(identifier: str) -> PaperMetadata:
    parsed = parse_identifier(identifier)
    if parsed is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a DOI (10.xxxx/…) or an arXiv id (e.g. 2003.06505)"
        )
    kind, value = parsed
    try:
        meta = await academic.lookup(kind, value, registry.academic_factory())
    except SourceError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Could not reach {exc.source}: {exc.message}"
        ) from exc
    if meta is None:
        label = "DOI" if kind == "doi" else "arXiv id"
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No paper found for {label} {value}")
    return meta


async def _match_existing(db: DbSession, project_id: uuid.UUID, meta: PaperMetadata) -> Paper | None:
    papers = await literature.project_papers(db, project_id)
    dedup = Deduplicator([literature.to_meta(p) for p in papers])
    index = dedup.add(meta)
    if index < len(papers):
        literature.apply_meta(papers[index], dedup.papers[index])  # fill in anything the lookup added
        return papers[index]
    return None


@router.post("/papers/lookup", response_model=LookupResult, status_code=status.HTTP_201_CREATED)
async def add_by_identifier(
    project_id: uuid.UUID, body: LookupRequest, user: CurrentUser, db: DbSession, response: Response
) -> LookupResult:
    """Add a paper by DOI or arXiv id (bare or as a URL), with metadata from the academic sources."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    meta = (await _resolve(body.identifier)).model_copy(update={"sources": ["manual"], "found_by": []})
    existing = await _match_existing(db, project_id, meta)
    if existing is not None:
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return LookupResult(paper=PaperRead.model_validate(existing), created=False)
    paper = literature.new_paper(project_id, meta, PaperOrigin.MANUAL)
    db.add(paper)
    await db.commit()
    log.info("paper_added_manually", project_id=str(project_id), paper_id=str(paper.id))
    return LookupResult(paper=PaperRead.model_validate(paper), created=True)


def _pdf_title(data: bytes) -> str | None:
    """Title from the PDF's metadata, else its first substantial line of text."""
    import pymupdf

    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[no-untyped-call]  # untyped library
            title = (doc.metadata or {}).get("title", "").strip()
            if len(title) >= 8:
                return str(title)[:500]
            if doc.page_count:
                for line in doc[0].get_text().splitlines():
                    if len(line.strip()) >= 15:
                        return str(line).strip()[:500]
    except Exception:  # noqa: BLE001 - a damaged PDF just gets no suggested title
        return None
    return None


@router.post("/papers/upload", response_model=LookupResult, status_code=status.HTTP_201_CREATED)
async def upload_paper(
    project_id: uuid.UUID,
    file: UploadFile,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    settings: SettingsDep,
    response: Response,
    title: Annotated[str | None, Form(max_length=500)] = None,
    identifier: Annotated[str | None, Form(max_length=300)] = None,
) -> LookupResult:
    """Upload a paper's PDF. With a DOI / arXiv id its metadata is looked up (and the PDF attached to
    the library entry if the paper is already there); without one it is added under its title and
    can't be cited until an identifier is added."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    meta = None
    if identifier and identifier.strip():
        meta = (await _resolve(identifier)).model_copy(update={"sources": ["upload"], "found_by": []})

    record = await save_upload(db, storage, settings, project_id, user, file, FileKind.PAPER_PDF)
    if meta is None:
        await file.seek(0)
        name = (title or "").strip() or _pdf_title(await file.read()) or record.filename.rsplit(".", 1)[0]
        meta = PaperMetadata(title=name, sources=["upload"])

    existing = await _match_existing(db, project_id, meta)
    if existing is not None:
        existing.pdf_file_id = record.id
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return LookupResult(paper=PaperRead.model_validate(existing), created=False)
    paper = literature.new_paper(project_id, meta, PaperOrigin.UPLOAD)
    paper.pdf_file_id = record.id
    db.add(paper)
    await db.commit()
    log.info("paper_uploaded", project_id=str(project_id), paper_id=str(paper.id), has_id=meta.has_identifier)
    return LookupResult(paper=PaperRead.model_validate(paper), created=True)


# ─── Full text: PDFs, parsing, parsed documents (Step 4.3) ───────────────


async def _paper(db: DbSession, project_id: uuid.UUID, paper_id: uuid.UUID) -> Paper:
    paper = await db.scalar(select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id))
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    return paper


@router.post("/parse", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def parse(
    project_id: uuid.UUID, body: ParseRequest, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """L4: get the full text of the given papers (default: every selected paper not parsed yet)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    ids = await literature.papers_to_parse(db, project_id, body.paper_ids, body.force)
    if not ids:
        detail = "Those papers aren't in this project" if body.paper_ids else "Select papers first (or all are parsed)"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    params = {"project_id": str(project_id), "paper_ids": [str(i) for i in ids]}
    return await start_job(db, dispatcher, user, "literature_parse", params, project_id)


@router.post("/papers/{paper_id}/pdf", response_model=PaperRead)
async def upload_paper_pdf(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    file: UploadFile,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> PaperRead:
    """Attach the PDF for a paper (e.g. one marked "upload needed"); it can then be parsed."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    paper = await _paper(db, project_id, paper_id)
    record = await save_upload(db, storage, settings, project_id, user, file, FileKind.PAPER_PDF)
    paper.pdf_file_id = record.id
    paper.parse_status = ParseStatus.NOT_STARTED
    paper.parse_error = None
    await db.commit()
    log.info("paper_pdf_attached", project_id=str(project_id), paper_id=str(paper_id))
    return PaperRead.model_validate(paper)


@router.get("/papers/{paper_id}/document", response_model=PaperDocumentRead)
async def get_document(project_id: uuid.UUID, paper_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Any:
    """The parsed paper: sections (with kinds and pages), figure/table captions, limitations."""
    await get_project_for_user(db, project_id, user)
    paper = await _paper(db, project_id, paper_id)
    document = await db.get(PaperDocument, paper_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This paper hasn't been parsed yet")
    chunks = await db.scalar(select(func.count()).select_from(PaperChunk).where(PaperChunk.paper_id == paper_id))
    return PaperDocumentRead(
        paper=PaperRead.model_validate(paper),
        parser=document.parser,
        pdf_source=document.pdf_source,
        pdf_url=document.pdf_url,
        page_count=document.page_count,
        abstract=document.abstract,
        sections=document.sections,
        captions=document.captions,
        limitations=document.limitations,
        reference_count=document.reference_count,
        chunk_count=chunks or 0,
    )


# ─── Ask the literature (Step 4.4) ───────────────────────────────────────


@router.post("/index", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def index_passages(project_id: uuid.UUID, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep) -> Job:
    """Embed parsed passages that aren't searchable yet (normally done right after parsing)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    if not (await retrieval.chunk_counts(db, project_id))[1]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Every parsed passage is already indexed")
    return await start_job(db, dispatcher, user, "literature_index", {"project_id": str(project_id)}, project_id)


@router.post("/ask", response_model=AnswerRead)
async def ask(project_id: uuid.UUID, body: AskRequest, user: CurrentUser, db: DbSession) -> AnswerRead:
    """Answer a question from the project's parsed papers; every citation is a retrieved passage."""
    await get_project_for_user(db, project_id, user)
    if not (await retrieval.chunk_counts(db, project_id))[0]:
        raise HTTPException(status.HTTP_409_CONFLICT, "No searchable passages yet: get the full text of some papers")
    try:
        query = await registry.embeddings_factory().embed_query(body.question)
        sources = await retrieval.retrieve(
            db, project_id, query, k=body.k, section_kinds=body.section_kinds, paper_ids=body.paper_ids
        )
        result = await rag.answer(
            body.question, sources, registry.llm_factory(request_session_factory(db))(), project_id
        )
    except LLMError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"The AI service failed: {exc}") from exc
    log.info("literature_asked", project_id=str(project_id), retrieved=len(sources), cited=len(result.citations),
             removed=len(result.removed_markers))  # fmt: skip
    return AnswerRead(
        answer=result.text,
        insufficient=result.insufficient,
        citations=[
            SourceRead(
                marker=c.marker,
                chunk_id=c.source.chunk_id,
                paper_id=c.source.paper_id,
                paper_title=c.source.paper_title,
                year=c.source.year,
                section=c.source.section,
                kind=c.source.kind,
                page=c.source.page,
                text=c.source.text,
                similarity=c.source.similarity,
                pdf_file_id=c.source.pdf_file_id,
            )
            for c in result.citations
        ],  # fmt: skip
        retrieved=len(sources),
        removed_markers=result.removed_markers,
    )


# ─── Paper profiles (Step 4.5) ───────────────────────────────────────────

TEXT_FIELDS = ("problem", "method", "results")
LIST_FIELDS = ("dataset", "metrics", "limitations", "tech_stack")


@router.post("/analyze", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def analyze(
    project_id: uuid.UUID, body: AnalyzeRequest, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """L6: evidence-backed profiles for the given papers (default: selected parsed papers without one)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    ids = await literature.papers_to_analyze(db, project_id, body.paper_ids, body.force)
    if not ids:
        detail = "Those papers are not parsed" if body.paper_ids else "No parsed papers without a profile"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    params = {"project_id": str(project_id), "paper_ids": [str(i) for i in ids]}
    return await start_job(db, dispatcher, user, "literature_analyze", params, project_id)


async def _profile_read(db: DbSession, profile: PaperProfile) -> ProfileRead:
    evidence: dict[str, Any] = profile.evidence or {}
    ids = {uuid.UUID(i) for name in TEXT_FIELDS for i in evidence.get(name, [])}
    ids |= {uuid.UUID(i) for name in LIST_FIELDS for item in evidence.get(name, []) for i in item}
    chunks = {c.id: c for c in await db.scalars(select(PaperChunk).where(PaperChunk.id.in_(ids)))} if ids else {}

    def sources(chunk_ids: list[str]) -> list[EvidenceRead]:
        found = [chunks.get(uuid.UUID(i)) for i in chunk_ids]
        return [
            EvidenceRead(chunk_id=c.id, section=c.section_name, kind=c.section_kind, page=c.page, text=c.text)
            for c in found
            if c is not None
        ]

    def text(name: str) -> ProfileValue | None:
        value = getattr(profile, name)
        return ProfileValue(value=value, evidence=sources(evidence.get(name, []))) if value else None

    def items(name: str) -> list[ProfileValue]:
        per_item = evidence.get(name, [])
        return [
            ProfileValue(value=v, evidence=sources(per_item[i] if i < len(per_item) else []))
            for i, v in enumerate(getattr(profile, name) or [])
        ]

    return ProfileRead(
        paper_id=profile.paper_id,
        problem=text("problem"),
        method=text("method"),
        results=text("results"),
        dataset=items("dataset"),
        metrics=items("metrics"),
        limitations=items("limitations"),
        tech_stack=items("tech_stack"),
        edited_fields=list(profile.edited_fields or []),
        updated_at=profile.updated_at,
    )


@router.get("/papers/{paper_id}/profile", response_model=ProfileRead | None)
async def get_profile(
    project_id: uuid.UUID, paper_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProfileRead | None:
    """The paper's profile, or null if it hasn't been analysed yet (a normal state, not an error)."""
    await get_project_for_user(db, project_id, user)
    await _paper(db, project_id, paper_id)
    profile = await db.get(PaperProfile, paper_id)
    return await _profile_read(db, profile) if profile is not None else None


@router.put("/papers/{paper_id}/profile", response_model=ProfileRead)
async def update_profile(
    project_id: uuid.UUID, paper_id: uuid.UUID, body: ProfileWrite, user: CurrentUser, db: DbSession
) -> ProfileRead:
    """The researcher's corrections. Evidence may only point at this paper's passages; changed fields are
    marked as edited (and keep only the evidence the researcher kept)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    await _paper(db, project_id, paper_id)
    profile = await db.get(PaperProfile, paper_id)
    if profile is None:
        profile = PaperProfile(paper_id=paper_id, evidence={}, edited_fields=[], dataset=[], metrics=[],
                               limitations=[], tech_stack=[])  # fmt: skip
        db.add(profile)
    own = set(await db.scalars(select(PaperChunk.id).where(PaperChunk.paper_id == paper_id)))
    evidence: dict[str, Any] = dict(profile.evidence or {})
    edited = set(profile.edited_fields or [])
    for name in TEXT_FIELDS:
        new = getattr(body, name)
        if new != getattr(profile, name):
            setattr(profile, name, new)
            edited.add(name)
            if not new:
                evidence.pop(name, None)
    for name in LIST_FIELDS:
        new_items = getattr(body, name)
        values = [item.value for item in new_items]
        item_evidence = [[str(e) for e in item.evidence if e in own] for item in new_items]
        if values != list(getattr(profile, name) or []) or item_evidence != evidence.get(name, []):
            setattr(profile, name, values)
            edited.add(name)
        evidence[name] = item_evidence
    profile.evidence = evidence
    profile.edited_fields = sorted(edited)
    await db.commit()
    await db.refresh(profile)
    log.info("paper_profile_edited", paper_id=str(paper_id), fields=sorted(edited))
    return await _profile_read(db, profile)


# ─── Comparison matrix (Step 4.6) ────────────────────────────────────────


async def _comparison_read(db: DbSession, project_id: uuid.UUID) -> ComparisonRead:
    row = await comparison.load(db, project_id)
    matrix = row.matrix
    papers = await comparison.selected_papers(db, project_id)
    profiled = set(
        await db.scalars(select(PaperProfile.paper_id).where(PaperProfile.paper_id.in_([p.id for p in papers])))
    )
    stored = matrix.get("rows", {})
    ids = {uuid.UUID(i) for p in papers for c in stored.get(str(p.id), {}).values() for i in c.get("evidence", [])}
    chunks = await comparison.chunk_lookup(db, ids)
    rows = []
    for paper in papers:
        cells = {}
        for key, cell in stored.get(str(paper.id), {}).items():
            evidence = [chunks[uuid.UUID(i)] for i in cell.get("evidence", []) if uuid.UUID(i) in chunks]
            cells[key] = ComparisonCell(
                value=cell["value"],
                source=cell["source"],
                evidence=[
                    EvidenceRead(chunk_id=c.id, section=c.section_name, kind=c.section_kind, page=c.page, text=c.text)
                    for c in evidence
                ],  # fmt: skip
            )
        rows.append(
            ComparisonRow(
                paper=ComparisonPaper(
                    id=paper.id,
                    title=paper.title,
                    year=paper.year,
                    venue=paper.venue,
                    parse_status=paper.parse_status,
                    has_profile=paper.id in profiled,
                ),  # fmt: skip
                cells=cells,
            )
        )
    builtin = {c["key"] for c in comparison.DEFAULT_COLUMNS}
    uncompared = sum(1 for r in rows if not builtin & set(r.cells))
    generated = matrix.get("generated_at")
    await db.commit()  # load() may have created the row
    return ComparisonRead(
        columns=[ComparisonColumn(**c) for c in matrix["columns"]],
        rows=rows,
        version=row.version,
        generated_at=datetime.fromisoformat(generated) if generated else None,
        uncompared=uncompared,
    )


@router.get("/comparison", response_model=ComparisonRead)
async def get_comparison(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ComparisonRead:
    """The comparison matrix: one row per selected paper."""
    await get_project_for_user(db, project_id, user)
    return await _comparison_read(db, project_id)


@router.post("/comparison/build", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def build_comparison(
    project_id: uuid.UUID, body: CompareRequest, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """L7: fill Method/Dataset/Metric/Result/Limitation from the papers' profiles (or abstracts)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    if not await comparison.selected_papers(db, project_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Select papers first")
    params = {"project_id": str(project_id), "force": body.force}
    return await start_job(db, dispatcher, user, "literature_compare", params, project_id)


@router.post("/comparison/columns", response_model=ComparisonRead, status_code=status.HTTP_201_CREATED)
async def add_comparison_column(
    project_id: uuid.UUID, body: ColumnCreate, user: CurrentUser, db: DbSession
) -> ComparisonRead:
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    try:
        await comparison.add_column(db, project_id, body.label)
    except comparison.ComparisonError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    return await _comparison_read(db, project_id)


@router.patch("/comparison/columns/{key}", response_model=ComparisonRead)
async def update_comparison_column(
    project_id: uuid.UUID, key: str, body: ColumnUpdate, user: CurrentUser, db: DbSession
) -> ComparisonRead:
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    try:
        await comparison.update_column(db, project_id, key, body.label, body.remove)
    except comparison.ComparisonError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    return await _comparison_read(db, project_id)


@router.post("/comparison/columns/{key}/fill", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def fill_comparison_column(
    project_id: uuid.UUID, key: str, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """Answer a custom column for each selected paper from its own passages."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    matrix = (await comparison.load(db, project_id)).matrix
    if not any(c["key"] == key and not c.get("builtin") for c in matrix["columns"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such custom column")
    if not (await retrieval.chunk_counts(db, project_id))[0]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Get the full text of some papers first")
    params = {"project_id": str(project_id), "force": False, "column": key}
    return await start_job(db, dispatcher, user, "literature_compare", params, project_id)


@router.put("/comparison/cells", response_model=ComparisonRead)
async def set_comparison_cell(
    project_id: uuid.UUID, body: CellWrite, user: CurrentUser, db: DbSession
) -> ComparisonRead:
    """The researcher's value for a cell; rebuilds never overwrite it. An empty value clears the cell."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    await _paper(db, project_id, body.paper_id)
    try:
        await comparison.set_cell(db, project_id, body.paper_id, body.key, body.value)
    except comparison.ComparisonError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await db.commit()
    return await _comparison_read(db, project_id)


@router.get("/comparison.csv")
async def export_comparison_csv(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    project, _ = await get_project_for_user(db, project_id, user)
    matrix = (await comparison.load(db, project_id)).matrix
    header, body = comparison.table(matrix, await comparison.selected_papers(db, project_id))
    await db.commit()
    name = re.sub(r"[^A-Za-z0-9]+", "-", project.title).strip("-")[:60] or "project"
    return Response(
        content=comparison.to_csv(header, body).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}-comparison.csv"'},
    )


@router.post("/comparison/insert", response_model=InsertTableResult)
async def insert_comparison(
    project_id: uuid.UUID, body: InsertTableRequest, user: CurrentUser, db: DbSession
) -> InsertTableResult:
    """Append the matrix to the Literature Survey section as a numbered table (a new section version)."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    statuses = {row.name: row.status for row in project.stages}
    if reason := sections.edit_blocker(SectionType.LITERATURE_SURVEY, statuses):
        detail = f"The Literature Survey section can't be edited now: {reason.lower()}"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    papers = await comparison.selected_papers(db, project_id)
    if not papers:
        raise HTTPException(status.HTTP_409_CONFLICT, "Select papers first")
    matrix = (await comparison.load(db, project_id)).matrix
    header, rows = comparison.table(matrix, papers, body.columns)
    number = (
        await db.scalar(select(func.max(ManuscriptTable.number)).where(ManuscriptTable.project_id == project_id)) or 0
    ) + 1
    db.add(
        ManuscriptTable(project_id=project_id, section_type=SectionType.LITERATURE_SURVEY, caption=body.caption,
                        number=number, data={"columns": header, "rows": rows, "paper_ids": [str(p.id) for p in papers]})
    )  # fmt: skip
    section = await sections.get_or_create_section(db, project_id, SectionType.LITERATURE_SURVEY)
    current = await sections.current_version(db, section)
    block = f"**Table {number}.** {body.caption}\n\n{comparison.to_markdown(header, rows, papers)}"
    content = f"{current.content.rstrip()}\n\n{block}\n" if current and current.content.strip() else f"{block}\n"
    version, _ = await sections.save_edit(
        db, section, content, user.id, base_version_id=None, autosave=False,
        change_note=f"Inserted comparison table (Table {number})",
    )  # fmt: skip
    await db.commit()
    log.info("comparison_inserted", project_id=str(project_id), table=number, papers=len(papers))
    return InsertTableResult(table_number=number, section_version=version.number)


# ─── Contradictions (Step 4.7) ───────────────────────────────────────────


@router.post("/contradictions/detect", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def detect_contradictions(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """L8: compare the claims of selected papers that share a dataset or metric."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    profiled = await db.scalar(
        select(func.count())
        .select_from(PaperProfile)
        .join(Paper, Paper.id == PaperProfile.paper_id)
        .where(Paper.project_id == project_id, Paper.selected.is_(True))
    )
    if (profiled or 0) < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, "Analyse at least two selected papers first")
    return await start_job(
        db, dispatcher, user, "literature_contradictions", {"project_id": str(project_id)}, project_id
    )


async def _contradiction_reads(db: DbSession, rows: list[Contradiction]) -> list[ContradictionRead]:
    paper_ids = {r.paper_a_id for r in rows} | {r.paper_b_id for r in rows}
    papers = {p.id: p for p in await db.scalars(select(Paper).where(Paper.id.in_(paper_ids)))} if paper_ids else {}
    chunk_ids = {uuid.UUID(i) for r in rows for side in ("a", "b") for i in (r.evidence or {}).get(side, [])}
    chunks = await comparison.chunk_lookup(db, chunk_ids)

    def side(paper_id: uuid.UUID, claim: str, ids: list[str]) -> ClaimSide:
        paper = papers[paper_id]
        found = [chunks[uuid.UUID(i)] for i in ids if uuid.UUID(i) in chunks]
        return ClaimSide(
            paper_id=paper_id, title=paper.title, year=paper.year, claim=claim,
            evidence=[EvidenceRead(chunk_id=c.id, section=c.section_name, kind=c.section_kind, page=c.page,
                                   text=c.text) for c in found],
        )  # fmt: skip

    return [
        ContradictionRead(
            id=r.id,
            topic=r.topic,
            a=side(r.paper_a_id, r.claim_a, (r.evidence or {}).get("a", [])),
            b=side(r.paper_b_id, r.claim_b, (r.evidence or {}).get("b", [])),
            explanation=r.explanation,
            explanation_kind=r.explanation_kind,
            status=r.status,
            dismiss_reason=r.dismiss_reason,
            dismissed_at=r.dismissed_at,
            created_at=r.created_at,
        )  # fmt: skip
        for r in rows
        if r.paper_a_id in papers and r.paper_b_id in papers
    ]


@router.get("/contradictions", response_model=list[ContradictionRead])
async def list_contradictions(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    status_: Annotated[Literal["open", "dismissed", "all"], Query(alias="status")] = "open",
) -> list[ContradictionRead]:
    await get_project_for_user(db, project_id, user)
    stmt = select(Contradiction).where(Contradiction.project_id == project_id)
    if status_ != "all":
        stmt = stmt.where(Contradiction.status == status_)
    rows = list(await db.scalars(stmt.order_by(Contradiction.topic, Contradiction.created_at)))
    return await _contradiction_reads(db, rows)


async def _set_contradiction(
    project_id: uuid.UUID, contradiction_id: uuid.UUID, user: CurrentUser, db: DbSession, *, dismissed: bool,
    reason: str | None = None,
) -> ContradictionRead:  # fmt: skip
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    row = await contradictions.set_status(db, project_id, contradiction_id, dismissed=dismissed, by=user.id,
                                          reason=reason)  # fmt: skip
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contradiction not found")
    await db.commit()
    return (await _contradiction_reads(db, [row]))[0]


@router.post("/contradictions/{contradiction_id}/dismiss", response_model=ContradictionRead)
async def dismiss_contradiction(
    project_id: uuid.UUID, contradiction_id: uuid.UUID, body: DismissRequest, user: CurrentUser, db: DbSession
) -> ContradictionRead:
    """Not a real conflict (or not relevant): hidden, and not reported again for this pair and topic."""
    return await _set_contradiction(project_id, contradiction_id, user, db, dismissed=True, reason=body.reason)


@router.post("/contradictions/{contradiction_id}/restore", response_model=ContradictionRead)
async def restore_contradiction(
    project_id: uuid.UUID, contradiction_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ContradictionRead:
    return await _set_contradiction(project_id, contradiction_id, user, db, dismissed=False)
