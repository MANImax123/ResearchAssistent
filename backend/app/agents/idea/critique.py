"""Critic agents (Step 3.2): Novelty, Feasibility and Methodology review the research plan in parallel.

Each critic returns a score, strengths, risks and concrete suggestions. Every suggestion names the
plan fields it would change, so a revision can be limited to exactly what the researcher accepted.
"""

import operator
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.agents.idea.schemas import IdeaPlan
from app.agents.prompts.critique import CRITIC_PROMPTS
from app.db.models.enums import StageName
from app.services.llm import LLMService

CriticName = Literal["novelty", "feasibility", "methodology"]
CRITICS: tuple[CriticName, ...] = ("novelty", "feasibility", "methodology")
ID_PREFIX: dict[str, str] = {"novelty": "N", "feasibility": "F", "methodology": "M"}

# Plan fields a suggestion may change (IdeaPlan attribute names)
PlanField = Literal[
    "refined_idea",
    "domain",
    "keywords",
    "subtopics",
    "problem_statement",
    "motivation",
    "scope",
    "expected_contributions",
    "objectives",
    "research_questions",
    "research_directions",
    "title_options",
]
# Objectives and research questions reference each other: changing one may require changing the other
LINKED_FIELDS: frozenset[str] = frozenset({"objectives", "research_questions"})

Point = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=600)]


class Suggestion(BaseModel):
    id: str = Field(default="", description="Leave empty; assigned by the system")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=150)]
    detail: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1500)] = Field(
        description="What exactly to change, naming the objective/RQ ids or quoting the text concerned"
    )
    affects: list[PlanField] = Field(min_length=1, max_length=4, description="Plan fields this would change")

    @field_validator("affects")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class Critique(BaseModel):
    score: int = Field(ge=1, le=5, description="1 = serious problems … 5 = strong")
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=800)]
    strengths: list[Point] = Field(min_length=1, max_length=5)
    risks: list[Point] = Field(min_length=1, max_length=5)
    suggestions: list[Suggestion] = Field(min_length=1, max_length=4)


class CritiqueSet(BaseModel):
    """What's stored in ResearchState["critiques"]."""

    round: int
    generated_at: str
    critiques: dict[CriticName, Critique]
    agent_run_ids: dict[str, str] = {}
    applied: list[str] = []  # suggestion ids applied by revisions

    def suggestions(self) -> dict[str, Suggestion]:
        return {s.id: s for c in self.critiques.values() for s in c.suggestions}


# ─── Parallel critique graph ───────────────────────────────────────────


class CritiqueState(TypedDict, total=False):
    plan_json: str
    project_id: str
    results: Annotated[list[tuple[str, dict[str, Any], str]], operator.add]  # (critic, critique, run id)


CriticFn = Callable[[str, str], Awaitable[tuple[Critique, str]]]  # (plan_json, project_id) → (critique, run id)


def build_critique_graph(critics: dict[str, CriticFn]) -> Any:
    """START fans out to every critic (run concurrently), results are merged by the list reducer."""
    graph = StateGraph(CritiqueState)
    for name, fn in critics.items():

        async def node(state: CritiqueState, _name: str = name, _fn: CriticFn = fn) -> dict[str, Any]:
            critique, run_id = await _fn(state["plan_json"], state["project_id"])
            return {"results": [(_name, critique.model_dump(mode="json"), run_id)]}

        graph.add_node(name, node)
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    return graph.compile()


def make_llm_critic(name: CriticName, llm_factory: Callable[[], LLMService]) -> CriticFn:
    prompt = CRITIC_PROMPTS[name]

    async def critic(plan_json: str, project_id: str) -> tuple[Critique, str]:
        result = await llm_factory().generate_structured(
            prompt.render(plan=plan_json),
            Critique,
            agent=f"idea.critic.{name}",
            system=prompt.system,
            project_id=uuid.UUID(project_id),
            stage=StageName.IDEA,
            prompt_id=prompt.id,
        )
        return result.data, str(result.run.run_id)

    return critic


async def run_critiques(
    plan: IdeaPlan, project_id: uuid.UUID, critics: dict[str, CriticFn], previous_round: int = 0
) -> CritiqueSet:
    """Run all critics in parallel; number their suggestions N1…, F1…, M1…."""
    graph = build_critique_graph(critics)
    out = await graph.ainvoke(
        {"plan_json": plan.model_dump_json(indent=2), "project_id": str(project_id), "results": []}
    )
    critiques: dict[str, Critique] = {}
    run_ids: dict[str, str] = {}
    for name, data, run_id in out["results"]:
        critique = Critique.model_validate(data)
        for i, suggestion in enumerate(critique.suggestions, start=1):
            suggestion.id = f"{ID_PREFIX[name]}{i}"
        critiques[name] = critique
        run_ids[name] = run_id
    ordered = {name: critiques[name] for name in CRITICS if name in critiques}
    return CritiqueSet(
        round=previous_round + 1,
        generated_at=datetime.now(UTC).isoformat(),
        critiques=ordered,
        agent_run_ids=run_ids,
    )


def allowed_fields(selected: list[Suggestion]) -> set[str]:
    """Fields a revision may change: those named by the selected suggestions (+ linked pairs)."""
    fields: set[str] = {f for s in selected for f in s.affects}
    if fields & LINKED_FIELDS:
        fields |= LINKED_FIELDS
    return fields


def merge_revision(current: IdeaPlan, revised: IdeaPlan, fields: set[str]) -> IdeaPlan:
    """Take only `fields` from the revision; everything else stays exactly as it was."""
    data = current.model_dump()
    revised_data = revised.model_dump()
    for field in fields:
        data[field] = revised_data[field]
    # A title the researcher wrote stays; an AI-suggested one follows the new options
    if (
        "title_options" in fields
        and current.title in current.title_options
        and data["title"] not in data["title_options"]
    ):
        data["title"] = data["title_options"][0]
    return IdeaPlan.model_validate(data)
