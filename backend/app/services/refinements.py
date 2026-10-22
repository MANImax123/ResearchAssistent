"""Plan refinements from accepted gaps (Step 4.10): suggestion runs and the researcher's decisions.

Accepting a refinement edits the approved research plan through the orchestrator, so the staleness
rule applies to every stage already worked on after the Idea stage.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.idea.agent import plan_from_state, plan_to_state, render_markdown
from app.agents.literature import refine as agent
from app.core.logging import get_logger
from app.db.models import Feedback, PlanRefinement, Project, ResearchGap
from app.db.models.enums import FeedbackAction, GapStatus, StageName
from app.orchestrator import service as orchestrator
from app.services.llm import LLMService, SessionFactory

log = get_logger(__name__)


class NoAcceptedGapsError(ValueError):
    pass


async def suggest(project_id: uuid.UUID, session_factory: SessionFactory, llm: LLMService) -> dict[str, Any]:
    """Replace the undecided proposals with new ones for the currently accepted gaps."""
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise LookupError("Project not found")
        await session.refresh(project, ["research_state"])
        plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
        gaps = list(
            await session.scalars(
                select(ResearchGap)
                .where(ResearchGap.project_id == project_id, ResearchGap.status == GapStatus.ACCEPTED)
                .order_by(ResearchGap.created_at)
            )
        )
    if plan is None:
        raise ValueError("The project has no research plan")
    if not gaps:
        raise NoAcceptedGapsError("Accept at least one potential gap first")
    proposals = await agent.propose(plan, [(str(g.id), g.statement) for g in gaps], llm, project_id)
    async with session_factory() as session:
        await session.execute(
            delete(PlanRefinement).where(PlanRefinement.project_id == project_id, PlanRefinement.status == "proposed")
        )
        for p in proposals:
            session.add(
                PlanRefinement(project_id=project_id, change=p.change, before=p.before,
                               rationale=p.rationale or None, gap_ids=p.gap_ids, status="proposed")
            )  # fmt: skip
        await session.commit()
    log.info("plan_refinements_proposed", project_id=str(project_id), gaps=len(gaps), proposed=len(proposals))
    return {"gaps": len(gaps), "proposed": len(proposals)}


async def decide(
    session: AsyncSession,
    session_factory: SessionFactory,
    project: Project,
    refinement: PlanRefinement,
    *,
    accept: bool,
    by: uuid.UUID,
) -> PlanRefinement:
    """Accept (apply to the plan; raises agent.InvalidChangeError if it no longer fits) or reject."""
    if accept:
        await session.refresh(project, ["research_state"])
        snapshot: dict[str, Any] = project.research_state.snapshot if project.research_state else {}
        plan = plan_from_state(snapshot)
        if plan is None:
            raise agent.InvalidChangeError("The project has no research plan")
        updated, created = agent.apply(plan, refinement.change)
        raw_idea = (snapshot.get("idea") or {}).get("raw_idea")
        await orchestrator.amend(
            project.id, StageName.IDEA, plan_to_state(updated, raw_idea), draft=render_markdown(updated),
            session_factory=session_factory,
        )  # fmt: skip
        refinement.applied_ids = created
        note = f"Refined plan from gaps: {refinement.change['kind'].replace('_', ' ')}"
        session.add(Feedback(project_id=project.id, stage=StageName.IDEA, user_id=by, action=FeedbackAction.EDIT,
                             comment=note))  # fmt: skip
    refinement.status = "accepted" if accept else "rejected"
    refinement.decided_by = by
    refinement.decided_at = datetime.now(UTC)
    await session.flush()
    return refinement
