"""Literature workspace, Discover (Step 4.1): search queries, discovery runs, the paper library,
and adding papers by DOI / arXiv id or by uploading a PDF."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, or_, select

from app.agents import registry
from app.agents.idea.agent import plan_from_state
from app.api.v1.files import StorageDep, save_upload
from app.api.v1.jobs import DispatcherDep, start_job
from app.core.deps import CurrentUser, DbSession, SettingsDep
from app.core.logging import get_logger
from app.db.models import Job, Paper, Project
from app.db.models.enums import FileKind, JobStatus, MemberRole, PaperOrigin, StageName
from app.schemas.job import JobRead
from app.schemas.literature import (
    DiscoverRequest,
    LiteratureWorkspaceRead,
    LookupRequest,
    LookupResult,
    PaperPage,
    PaperRead,
    QueriesWrite,
    QueryRead,
    SourceStatus,
)
from app.services import literature
from app.services.academic import sources as academic
from app.services.academic.dedup import Deduplicator
from app.services.academic.http import SourceError
from app.services.academic.models import PaperMetadata, parse_identifier
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/literature", tags=["literature"])
log = get_logger(__name__)

LITERATURE_JOBS = ("literature_plan", "literature_discover")
SortKey = Literal["added", "year", "citations", "title"]


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
    count = await db.scalar(select(func.count()).select_from(Paper).where(Paper.project_id == project_id))
    return LiteratureWorkspaceRead(
        status=next(row.status for row in project.stages if row.name == StageName.LITERATURE),
        editable=reason is None,
        blocked_reason=reason,
        queries=[QueryRead.model_validate(q) for q in await literature.list_queries(db, project_id)],
        paper_count=count or 0,
        sources=_source_statuses(settings),
        research_questions=[{"id": q.id, "text": q.text} for q in plan.research_questions] if plan else [],
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
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order: tuple[Any, ...] = {
        "added": (Paper.created_at.desc(), Paper.title),
        "year": (Paper.year.desc().nulls_last(), Paper.title),
        "citations": (Paper.citation_count.desc().nulls_last(), Paper.title),
        "title": (Paper.title,),
    }[sort]
    rows = await db.scalars(stmt.order_by(*order).limit(limit).offset(offset))
    return PaperPage(total=total, items=[PaperRead.model_validate(p) for p in rows])


@router.delete("/papers/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_paper(project_id: uuid.UUID, paper_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    _ensure_editable(project)
    paper = await db.scalar(select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id))
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    await db.delete(paper)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
