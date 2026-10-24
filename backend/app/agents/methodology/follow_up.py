"""Step 5.1: follow-up questions for missing methodology essentials.

The deterministic checklist (`schemas.missing`) decides WHAT may be asked about; the LLM phrases
concrete questions for this project and may add "unclear" questions only by quoting the researcher's
own words. Anything else it returns is dropped, so questions stay about what is actually missing.
"""

import json
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.agents.idea.schemas import IdeaPlan
from app.agents.methodology.schemas import FIELD_LABEL, MethodologyInputData, Missing
from app.agents.prompts.methodology import FOLLOW_UP
from app.db.models.enums import StageName
from app.services.llm import LLMService

AGENT_NAME = "methodology.follow_up"
MAX_QUESTIONS = 8


class QuestionDraft(BaseModel):
    field: str = Field(description="One of the form fields, e.g. 'metrics'")
    kind: Literal["missing", "unclear"] = "missing"
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    quote: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class QuestionList(BaseModel):
    questions: list[QuestionDraft] = Field(default_factory=list)


@dataclass
class Question:
    field: str
    kind: str
    question: str
    reason: str


def _plan_text(plan: IdeaPlan | None) -> str:
    if plan is None:
        return "(no plan)"
    return "\n".join([f"Problem: {plan.problem_statement}", *[f"{o.id}: {o.text}" for o in plan.objectives],
                      *[f"{q.id}: {q.text}" for q in plan.research_questions]])  # fmt: skip


def _normal(text: str) -> str:
    return " ".join(text.lower().split())


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, _normal(a), _normal(b)).ratio() >= 0.8


async def ask(
    data: MethodologyInputData,
    checklist: list[Missing],
    plan: IdeaPlan | None,
    files: str,
    answered: list[tuple[str, str]],
    llm: LLMService,
    project_id: uuid.UUID,
) -> list[Question]:
    """Questions for the missing checklist (plus quoted "unclear" ones); [] when nothing is missing or vague."""
    input_json = data.model_dump_json(indent=1, exclude_defaults=True)
    result = await llm.generate_structured(
        FOLLOW_UP.render(
            plan=_plan_text(plan),
            data=input_json if input_json.strip() not in ("{}", "") else "(empty)",
            files=files or "(none)",
            answered="\n".join(f"Q: {q}\nA: {a}" for q, a in answered) or "(none)",
            missing="\n".join(
                f"- {m.field} ({FIELD_LABEL[m.field]}{', essential' if m.essential else ''}): {m.reason}"
                for m in checklist
            )
            or "(nothing)",
        ),  # fmt: skip
        QuestionList,
        agent=AGENT_NAME,
        system=FOLLOW_UP.system,
        project_id=project_id,
        stage=StageName.METHODOLOGY,
        prompt_id=FOLLOW_UP.id,
    )
    missing_fields = {m.field for m in checklist}
    essential = {m.field for m in checklist if m.essential}
    haystack = _normal(json.dumps(data.model_dump(), ensure_ascii=False))
    kept: list[Question] = []
    for q in result.data.questions:
        field = q.field.strip().lower()
        if field not in FIELD_LABEL:
            continue
        if q.kind == "missing" and field not in missing_fields:
            continue  # the form already covers it
        if q.kind == "unclear" and (len(q.quote) < 3 or _normal(q.quote) not in haystack):
            continue  # "unclear" must point at the researcher's own words
        if any(_similar(q.question, prev) for prev, _ in answered) or any(
            _similar(q.question, k.question) for k in kept
        ):
            continue
        reason = f"{q.reason} (You wrote: “{q.quote}”)" if q.kind == "unclear" and q.quote else q.reason
        kept.append(Question(field, q.kind, q.question, reason.strip()))
    kept.sort(key=lambda k: (k.field not in essential, k.kind != "missing"))
    return kept[:MAX_QUESTIONS]
