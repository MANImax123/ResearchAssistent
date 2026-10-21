"""L1 Research Planning: turn the approved research plan into academic search queries.

Each query names the research questions (RQ1…) and subtopics (S1…) it serves. If the first answer
leaves a question or subtopic uncovered, the model is asked once more with the gap spelled out; the
researcher can edit, add or remove queries afterwards.
"""

import uuid
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.agents.idea.schemas import IdeaPlan
from app.agents.prompts.literature import SEARCH_PLAN
from app.db.models.enums import StageName
from app.services.llm import LLMService

AGENT_NAME = "literature.planning"
MIN_QUERIES, MAX_QUERIES = 6, 12


class PlannedQuery(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    purpose: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    targets: list[str] = Field(default_factory=list, description="RQ ids and subtopic ids (S1…) it serves")


class SearchPlan(BaseModel):
    queries: list[PlannedQuery] = Field(min_length=3, max_length=20)


def subtopic_ids(plan: IdeaPlan) -> dict[str, str]:
    return {f"S{i}": topic for i, topic in enumerate(plan.subtopics, start=1)}


def uncovered(search: SearchPlan, plan: IdeaPlan) -> list[str]:
    targeted = {t.strip().upper() for q in search.queries for t in q.targets}
    wanted = [q.id for q in plan.research_questions] + list(subtopic_ids(plan))
    return [w for w in wanted if w.upper() not in targeted]


def _dedupe(search: SearchPlan) -> list[PlannedQuery]:
    seen: set[str] = set()
    queries = []
    for query in search.queries:
        key = " ".join(sorted(query.text.lower().split()))
        if key not in seen:
            seen.add(key)
            queries.append(query)
    return queries[:MAX_QUERIES]


async def plan_queries(plan: IdeaPlan, llm: LLMService, project_id: uuid.UUID) -> list[PlannedQuery]:
    plan_text = plan.model_dump_json(
        indent=2, include={"refined_idea", "domain", "keywords", "objectives", "research_questions", "scope"}
    )
    subtopics = "\n".join(f"{sid}: {topic}" for sid, topic in subtopic_ids(plan).items()) or "(none)"
    feedback = ""
    result: SearchPlan | None = None
    for _ in range(2):
        prompt = SEARCH_PLAN.render(
            plan=plan_text, subtopics=subtopics, feedback=feedback, min_queries=MIN_QUERIES, max_queries=MAX_QUERIES
        )
        response = await llm.generate_structured(
            prompt,
            SearchPlan,
            agent=AGENT_NAME,
            system=SEARCH_PLAN.system,
            project_id=project_id,
            stage=StageName.LITERATURE,
            prompt_id=SEARCH_PLAN.id,
        )
        result = response.data
        missing = uncovered(result, plan)
        if not missing:
            break
        feedback = (
            f"Your previous queries left these uncovered: {', '.join(missing)}. "
            "Return the full list again, adding queries that serve them.\n\n"
        )
    assert result is not None
    return _dedupe(result)
