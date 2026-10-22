"""Potential research gaps (Step 4.9, L10): detect, list, edit, accept or reject.

Gaps are candidates until the researcher accepts them (A4.3); even then the UI calls them potential gaps.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.agents import registry
from app.agents.idea.agent import plan_from_state
from app.api.v1.jobs import DispatcherDep, start_job
from app.api.v1.literature import _ensure_editable, _ensure_idle
from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import Job, Paper, PaperProfile, ResearchGap
from app.db.models.enums import GapConfidence, GapStatus, MemberRole
from app.db.session import request_session_factory
from app.schemas.job import JobRead
from app.schemas.literature import EvidenceRead, GapRead, GapSignalRead, GapUpdate, ObjectiveRef, PaperRef
from app.services import comparison, gaps, knowledge_graph
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/gaps", tags=["research gaps"])
log = get_logger(__name__)


@router.post("/detect", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def detect_gaps(project_id: uuid.UUID, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep) -> Job:
    """L10: propose potential gaps from the selected papers' limitations, conflicts and future work."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    profiled = await db.scalar(
        select(func.count())
        .select_from(PaperProfile)
        .join(Paper, Paper.id == PaperProfile.paper_id)
        .where(Paper.project_id == project_id, Paper.selected.is_(True))
    )
    if not profiled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Analyse at least one selected paper first")
    return await start_job(db, dispatcher, user, "research_gaps", {"project_id": str(project_id)}, project_id)


async def _reads(db: DbSession, rows: list[ResearchGap], objectives: dict[str, str]) -> list[GapRead]:
    signals = [s for r in rows for s in (r.evidence or {}).get("signals", [])]
    paper_ids = {uuid.UUID(p) for s in signals for p in s.get("paper_ids", [])}
    papers = {p.id: p for p in await db.scalars(select(Paper).where(Paper.id.in_(paper_ids)))} if paper_ids else {}
    chunks = await comparison.chunk_lookup(db, {uuid.UUID(c) for s in signals for c in s.get("chunk_ids", [])})

    def signal(s: dict[str, Any]) -> GapSignalRead:
        found_papers = [papers[uuid.UUID(p)] for p in s.get("paper_ids", []) if uuid.UUID(p) in papers]
        found_chunks = [chunks[uuid.UUID(c)] for c in s.get("chunk_ids", []) if uuid.UUID(c) in chunks]
        return GapSignalRead(
            kind=s["kind"],
            text=str(s["text"]),
            papers=[PaperRef(id=p.id, title=p.title, year=p.year) for p in found_papers],
            evidence=[EvidenceRead(chunk_id=c.id, section=c.section_name, kind=c.section_kind, page=c.page,
                                   text=c.text) for c in found_chunks],
        )  # fmt: skip

    return [
        GapRead(
            id=r.id,
            statement=r.statement,
            rationale=r.rationale,
            confidence=r.confidence.value,
            status=r.status.value,
            objectives=[ObjectiveRef(id=o, text=objectives[o]) for o in r.objective_ids or [] if o in objectives],
            signals=[signal(s) for s in (r.evidence or {}).get("signals", [])],
            edited=r.edited,
            decided_at=r.decided_at,
            created_at=r.created_at,
        )  # fmt: skip
        for r in rows
    ]


async def _objectives(db: DbSession, project_id: uuid.UUID, user: CurrentUser) -> dict[str, str]:
    project, _ = await get_project_for_user(db, project_id, user)
    await db.refresh(project, ["research_state"])
    plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
    return {o.id: o.text for o in plan.objectives} if plan else {}


@router.get("", response_model=list[GapRead])
async def list_gaps(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    status_: Annotated[Literal["candidate", "accepted", "rejected", "all"], Query(alias="status")] = "all",
) -> list[GapRead]:
    """Accepted first, then candidates (high confidence first), then rejected."""
    objectives = await _objectives(db, project_id, user)
    stmt = select(ResearchGap).where(ResearchGap.project_id == project_id)
    if status_ != "all":
        stmt = stmt.where(ResearchGap.status == GapStatus(status_))
    rows = list(await db.scalars(stmt))
    order = {GapStatus.ACCEPTED: 0, GapStatus.CANDIDATE: 1, GapStatus.REJECTED: 2}
    confidence = {GapConfidence.HIGH: 0, GapConfidence.MEDIUM: 1, GapConfidence.LOW: 2}
    rows.sort(key=lambda r: (order[r.status], confidence[r.confidence], r.created_at))
    return await _reads(db, rows, objectives)


@router.patch("/{gap_id}", response_model=GapRead)
async def update_gap(
    project_id: uuid.UUID, gap_id: uuid.UUID, body: GapUpdate, user: CurrentUser, db: DbSession
) -> GapRead:
    """Edit a gap's wording, confidence or linked objectives, and/or accept or reject it."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    exists = await db.scalar(
        select(ResearchGap.id).where(ResearchGap.id == gap_id, ResearchGap.project_id == project_id)
    )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gap not found")
    _ensure_editable(project)
    objectives = await _objectives(db, project_id, user)
    changes = body.model_dump(exclude_none=True)
    if "objective_ids" in changes:
        unknown = [o for o in changes["objective_ids"] if o not in objectives]
        if unknown:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown objectives: {', '.join(unknown)}")
        changes["objective_ids"] = list(dict.fromkeys(changes["objective_ids"]))
    if "confidence" in changes:
        changes["confidence"] = GapConfidence(changes["confidence"])
    if "status" in changes:
        changes["status"] = GapStatus(changes["status"])
    gap = await gaps.update(db, project, gap_id, changes, user.id)
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gap not found")
    await db.commit()
    try:  # the graph is a derived view: a failure here is repaired by the next build
        await knowledge_graph.sync_gaps(project_id, request_session_factory(db), registry.graph_factory())
    except Exception as exc:  # noqa: BLE001
        log.warning("gap_graph_sync_failed", project_id=str(project_id), error=str(exc)[:200])
    return (await _reads(db, [gap], objectives))[0]
