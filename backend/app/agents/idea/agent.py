"""Research Idea & Problem agent (Step 3.1): raw idea → structured research plan.

The plan is written into the shared ResearchState (idea, problem, objectives, research_questions,
title) so every later agent works from the same approved plan.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.agents.idea.schemas import IdeaPlan, Objective, ResearchQuestion
from app.agents.prompts.idea import IDEA_PLAN, NO_CONTEXT, feedback_block
from app.db.models.enums import StageName
from app.orchestrator.state import ResearchState
from app.services.llm import LLMService

AGENT_NAME = "idea.plan"

ContextLoader = Callable[[uuid.UUID], Awaitable[str]]


class MissingIdeaError(ValueError):
    pass


def plan_from_state(state: ResearchState | dict[str, Any]) -> IdeaPlan | None:
    """The current plan, if the Idea agent has produced one."""
    idea = state.get("idea") or {}
    problem = state.get("problem") or {}
    if not idea.get("refined_idea"):
        return None
    return IdeaPlan(
        refined_idea=idea["refined_idea"],
        domain=idea.get("domain") or "",
        keywords=idea.get("keywords") or [],
        subtopics=idea.get("subtopics") or [],
        problem_statement=problem.get("statement") or "",
        motivation=problem.get("motivation") or "",
        scope=problem.get("scope") or "",
        expected_contributions=problem.get("contributions") or [],
        objectives=[Objective(**o) for o in state.get("objectives") or []],
        research_questions=[ResearchQuestion(**q) for q in state.get("research_questions") or []],
        research_directions=idea.get("research_directions") or [],
        title_options=idea.get("title_options") or [],
        title=state.get("title"),
    )


def plan_to_state(plan: IdeaPlan, raw_idea: str | None) -> dict[str, Any]:
    """ResearchState updates for a plan (keeps the researcher's raw idea)."""
    return {
        "idea": {
            "raw_idea": raw_idea,
            "refined_idea": plan.refined_idea,
            "domain": plan.domain,
            "keywords": plan.keywords,
            "subtopics": plan.subtopics,
            "research_directions": plan.research_directions,
            "title_options": plan.title_options,
        },
        "problem": {
            "statement": plan.problem_statement,
            "motivation": plan.motivation,
            "scope": plan.scope,
            "contributions": plan.expected_contributions,
        },
        "objectives": [o.model_dump() for o in plan.objectives],
        "research_questions": [q.model_dump() for q in plan.research_questions],
        "title": plan.title,
    }


def render_markdown(plan: IdeaPlan) -> str:
    """Readable summary of the plan (the stage draft shown in reviews and history)."""
    lines = [
        f"## {plan.title or plan.title_options[0]}",
        "",
        plan.refined_idea,
        "",
        f"**Domain:** {plan.domain}  ",
        f"**Keywords:** {', '.join(plan.keywords)}",
        "",
        "### Problem statement",
        plan.problem_statement,
        "",
        "### Motivation",
        plan.motivation,
        "",
        "### Scope",
        plan.scope,
        "",
        "### Objectives",
        *[f"- **{o.id}.** {o.text}" for o in plan.objectives],
        "",
        "### Research questions",
        *[
            f"- **{q.id}** ({q.kind}, serves {', '.join(q.linked_objective_ids)}). {q.text}"
            for q in plan.research_questions
        ],
        "",
        "### Expected contributions",
        *[f"- {c}" for c in plan.expected_contributions],
        "",
        "### Subtopics",
        *[f"- {s}" for s in plan.subtopics],
        "",
        "### Possible research directions",
        *[f"- {d}" for d in plan.research_directions],
    ]
    return "\n".join(lines)


def make_idea_agent(llm_factory: Callable[[], LLMService], load_context: ContextLoader) -> Callable[..., Any]:
    async def idea_agent(state: ResearchState) -> dict[str, Any]:
        idea = state.get("idea") or {}
        raw_idea = (idea.get("raw_idea") or "").strip()
        if not raw_idea:
            raise MissingIdeaError("Add your research idea to the project first")
        project_id = uuid.UUID(state["project_id"])
        context = await load_context(project_id)
        previous = plan_from_state(state)
        venue = state.get("venue") or {}
        prompt = IDEA_PLAN.render(
            raw_idea=raw_idea,
            domain=idea.get("domain") or "(not given)",
            venue=venue.get("name") or "(not decided)",
            context=context or NO_CONTEXT,
            feedback_block=feedback_block(
                previous.model_dump_json(indent=2) if previous else None, state.get("pending_feedback")
            ),
        )
        result = await llm_factory().generate_structured(
            prompt,
            IdeaPlan,
            agent=AGENT_NAME,
            system=IDEA_PLAN.system,
            project_id=project_id,
            stage=StageName.IDEA,
            prompt_id=IDEA_PLAN.id,
        )
        plan = result.data
        if plan.title is None or plan.title not in plan.title_options:
            plan = plan.model_copy(update={"title": plan.title_options[0]})
        return {
            **plan_to_state(plan, raw_idea),
            "stage_outputs": {
                StageName.IDEA.value: {
                    "draft": render_markdown(plan),
                    "agent": AGENT_NAME,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "agent_run_id": str(result.run.run_id),
                    "section_draft": None,  # the plan isn't a paper section; the abstract comes in Step 3.3
                }
            },
        }

    return idea_agent
