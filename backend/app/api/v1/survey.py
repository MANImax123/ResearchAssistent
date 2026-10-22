"""Step 4.10: refine the plan from accepted gaps; the Literature Survey section and analysis report.

The section itself is written by the Literature stage's agent (run / regenerate / approve through the
stage endpoints); these routes show it with its citation check and serve the report.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from app.agents.literature.refine import InvalidChangeError
from app.api.v1.jobs import DispatcherDep, start_job
from app.api.v1.literature import _ensure_editable, _ensure_idle
from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import Job, PlanRefinement, ResearchGap
from app.db.models.enums import GapStatus, MemberRole, SectionType, StageName
from app.db.session import request_session_factory
from app.orchestrator import service as orchestrator
from app.schemas.job import JobRead
from app.schemas.literature import GapRef, RefinementRead, SurveyCitation, SurveyRead
from app.services import refinements, sections, survey
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/literature", tags=["literature survey"])
log = get_logger(__name__)


@router.post("/refinements/suggest", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def suggest_refinements(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep
) -> Job:
    """L11: propose changes to the objectives and research questions for the accepted gaps."""
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    await _ensure_idle(db, project)
    accepted = await db.scalar(
        select(func.count())
        .select_from(ResearchGap)
        .where(ResearchGap.project_id == project_id, ResearchGap.status == GapStatus.ACCEPTED)
    )
    if not accepted:
        raise HTTPException(status.HTTP_409_CONFLICT, "Accept at least one potential gap first")
    return await start_job(db, dispatcher, user, "literature_refine", {"project_id": str(project_id)}, project_id)


async def _reads(db: DbSession, rows: list[PlanRefinement]) -> list[RefinementRead]:
    gap_ids = {uuid.UUID(g) for r in rows for g in r.gap_ids or []}
    gaps = (
        {g.id: g for g in await db.scalars(select(ResearchGap).where(ResearchGap.id.in_(gap_ids)))} if gap_ids else {}
    )
    return [
        RefinementRead(
            id=r.id,
            kind=r.change["kind"],
            target=r.change.get("target"),
            text=r.change["text"],
            question_kind=r.change.get("question_kind"),
            linked_objective_ids=r.change.get("linked_objective_ids") or [],
            question_text=r.change.get("question_text"),
            before=r.before,
            rationale=r.rationale,
            gaps=[GapRef(id=g.id, statement=g.statement) for i in r.gap_ids or [] if (g := gaps.get(uuid.UUID(i)))],
            status=r.status,
            applied_ids=r.applied_ids or [],
            decided_at=r.decided_at,
            created_at=r.created_at,
        )  # fmt: skip
        for r in rows
    ]


@router.get("/refinements", response_model=list[RefinementRead])
async def list_refinements(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[RefinementRead]:
    await get_project_for_user(db, project_id, user)
    rows = list(await db.scalars(select(PlanRefinement).where(PlanRefinement.project_id == project_id)))
    order = {"proposed": 0, "accepted": 1, "rejected": 2}
    rows.sort(key=lambda r: (order.get(r.status, 3), r.created_at))
    return await _reads(db, rows)


async def _decide(
    project_id: uuid.UUID, refinement_id: uuid.UUID, user: CurrentUser, db: DbSession, *, accept: bool
) -> RefinementRead:
    project, _ = await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    row = await db.scalar(
        select(PlanRefinement).where(PlanRefinement.id == refinement_id, PlanRefinement.project_id == project_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refinement not found")
    _ensure_editable(project)
    if row.status != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This change was already {row.status}")
    try:
        await refinements.decide(db, request_session_factory(db), project, row, accept=accept, by=user.id)
    except InvalidChangeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This change no longer fits the plan: {exc}") from exc
    except (orchestrator.InvalidStageActionError, orchestrator.ReviewPendingError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    return (await _reads(db, [row]))[0]


@router.post("/refinements/{refinement_id}/accept", response_model=RefinementRead)
async def accept_refinement(
    project_id: uuid.UUID, refinement_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> RefinementRead:
    """Apply the change to the approved plan; stages already worked on after it become stale."""
    return await _decide(project_id, refinement_id, user, db, accept=True)


@router.post("/refinements/{refinement_id}/reject", response_model=RefinementRead)
async def reject_refinement(
    project_id: uuid.UUID, refinement_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> RefinementRead:
    return await _decide(project_id, refinement_id, user, db, accept=False)


@router.get("/survey", response_model=SurveyRead)
async def get_survey(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """The Literature Survey section's current text, with its citation check."""
    project, _ = await get_project_for_user(db, project_id, user)
    stage = next(row.status for row in project.stages if row.name == StageName.LITERATURE)
    section = await sections.get_section(db, project_id, SectionType.LITERATURE_SURVEY)
    current = await sections.current_version(db, section) if section is not None else None
    check = await survey.check_citations(db, project_id, current.content if current else "")
    return {
        "stage_status": stage,
        "version": current.number if current else None,
        "content": current.content if current else None,
        "source": current.source.value if current else None,
        "updated_at": current.created_at if current else None,
        "citations": [
            SurveyCitation(paper_id=p.id, title=p.title, year=p.year, authors=survey.authors(p)) for p in check["cited"]
        ],
        "unresolved": check["unresolved"],
        "uncited_paragraphs": check["uncited_paragraphs"],
        "paragraphs": check["paragraphs"],
    }


@router.get("/report")
async def download_report(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """The literature analysis report as a Markdown file."""
    await get_project_for_user(db, project_id, user)
    text = await survey.report(db, project_id)
    await db.commit()  # an empty comparison row may have been created on first access
    return Response(
        content=text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="literature-analysis-report.md"'},
    )
