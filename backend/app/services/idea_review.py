"""Work on the research plan while the Idea stage awaits review: critique and revise it (Step 3.2),
draft the working abstract (Step 3.3)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.agents import registry
from app.agents.idea.abstract import AGENT_NAME as ABSTRACT_AGENT
from app.agents.idea.abstract import AbstractResult, plan_fingerprint, write_abstract
from app.agents.idea.agent import plan_from_state, plan_to_state, render_markdown
from app.agents.idea.critique import CriticFn, CritiqueSet, allowed_fields, merge_revision, run_critiques
from app.agents.idea.schemas import IdeaPlan
from app.agents.prompts.critique import REVISE
from app.core.logging import get_logger
from app.core.venues import abstract_word_limits
from app.db.models.enums import SectionType, StageName, VersionSource
from app.orchestrator import service as orchestrator
from app.services import sections
from app.services.llm import LLMService, SessionFactory

log = get_logger(__name__)


class PlanNotUnderReviewError(Exception):
    pass


class UnknownSuggestionError(ValueError):
    pass


async def _plan_under_review(project_id: uuid.UUID) -> tuple[IdeaPlan, dict[str, Any]]:
    graph = await orchestrator.read_state(project_id)
    if graph.pending is None or graph.pending.stage != StageName.IDEA.value:
        raise PlanNotUnderReviewError("The research plan must be awaiting review")
    plan = plan_from_state(graph.values)
    if plan is None:
        raise PlanNotUnderReviewError("Generate a research plan first")
    return plan, graph.values


async def critique_plan(
    project_id: uuid.UUID, session_factory: SessionFactory, critics: dict[str, CriticFn] | None = None
) -> CritiqueSet:
    """Run the three critics in parallel on the current plan and store the round in the shared state."""
    plan, values = await _plan_under_review(project_id)
    previous = values.get("critiques") or {}
    critiques = await run_critiques(
        plan, project_id, critics or registry.critic_factory(session_factory), previous.get("round", 0)
    )
    decision = {"action": "update", "fields": {"critiques": critiques.model_dump(mode="json")}}
    await orchestrator.review(project_id, decision, session_factory=session_factory, agents={})
    log.info("plan_critiqued", project_id=str(project_id), round=critiques.round,
             scores={k: v.score for k, v in critiques.critiques.items()})  # fmt: skip
    return critiques


async def revise_plan(
    project_id: uuid.UUID,
    suggestion_ids: list[str],
    session_factory: SessionFactory,
    *,
    by: str | None = None,
    llm: LLMService | None = None,
) -> IdeaPlan:
    """Revise the plan applying ONLY the selected suggestions; other fields are kept byte-for-byte."""
    plan, values = await _plan_under_review(project_id)
    if not values.get("critiques"):
        raise UnknownSuggestionError("Ask the critics first")
    critiques = CritiqueSet.model_validate(values["critiques"])
    available = critiques.suggestions()
    unknown = [s for s in suggestion_ids if s not in available]
    if unknown:
        raise UnknownSuggestionError(f"Unknown suggestions: {', '.join(unknown)}")
    selected = [available[s] for s in dict.fromkeys(suggestion_ids)]
    fields = allowed_fields(selected)

    listing = "\n".join(f"- {s.id} [changes: {', '.join(s.affects)}] {s.title}: {s.detail}" for s in selected)
    result = await (llm or registry.llm_factory(session_factory)()).generate_structured(
        REVISE.render(plan=plan.model_dump_json(indent=2), suggestions=listing, fields=", ".join(sorted(fields))),
        IdeaPlan,
        agent="idea.revise",
        system=REVISE.system,
        project_id=project_id,
        stage=StageName.IDEA,
        prompt_id=REVISE.id,
    )
    revised = merge_revision(plan, result.data, fields)

    critiques.applied = sorted({*critiques.applied, *(s.id for s in selected)})
    raw_idea = (values.get("idea") or {}).get("raw_idea")
    decision = {
        "action": "update",
        "by": by,
        "fields": {**plan_to_state(revised, raw_idea), "critiques": critiques.model_dump(mode="json")},
        "draft": render_markdown(revised),
    }
    await orchestrator.review(project_id, decision, session_factory=session_factory, agents={})
    log.info("plan_revised", project_id=str(project_id), applied=[s.id for s in selected], fields=sorted(fields))
    return revised


async def draft_abstract(
    project_id: uuid.UUID, session_factory: SessionFactory, *, llm: LLMService | None = None
) -> AbstractResult:
    """Write the working abstract from the plan under review and save it as a new Abstract version.

    Approving the Idea stage approves the plan and this abstract together (the section follows the stage).
    """
    plan, values = await _plan_under_review(project_id)
    venue = (values.get("venue") or {}).get("name")
    limits = abstract_word_limits(venue)
    result = await write_abstract(plan, llm or registry.llm_factory(session_factory)(), project_id, limits, venue)
    meta = {
        "word_count": result.word_count,
        "min_words": result.min_words,
        "max_words": result.max_words,
        "within_limit": result.within_limit,
        "attempts": result.attempts,
        "venue": venue,
        "plan_fingerprint": plan_fingerprint(plan),
        "generated_at": datetime.now(UTC).isoformat(),
        "agent_run_ids": result.run_ids,
    }
    decision = {"action": "update", "fields": {"abstract_draft": result.text, "abstract_meta": meta}}
    await orchestrator.review(project_id, decision, session_factory=session_factory, agents={})

    async with session_factory() as session:
        section = await sections.get_or_create_section(session, project_id, SectionType.ABSTRACT)
        await sections.add_version(
            session,
            section,
            result.text,
            VersionSource.AI,
            change_note=f"Drafted by {ABSTRACT_AGENT} agent ({result.word_count} words)",
            agent_run_id=uuid.UUID(result.run_ids[-1]),
        )
        await session.commit()
    log.info("abstract_drafted", project_id=str(project_id), words=result.word_count,
             within_limit=result.within_limit, attempts=result.attempts)  # fmt: skip
    return result
