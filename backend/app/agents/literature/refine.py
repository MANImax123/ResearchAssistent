"""L11 (part 1): refine objectives and research questions from the gaps the researcher accepted.

The agent proposes changes; each is checked by applying it to the current plan (the plan's own rules:
ids, links, hypotheses phrased as claims). The researcher accepts them one by one; an accepted change
edits the approved plan, so the orchestrator's staleness rule runs (Step 4.10).
"""

import re
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from app.agents.idea.schemas import IdeaPlan
from app.agents.prompts.literature import REFINE_PLAN
from app.core.logging import get_logger
from app.db.models.enums import StageName
from app.services.llm import LLMService

log = get_logger(__name__)

AGENT_NAME = "literature.refine"
MAX_CHANGES = 6

ChangeKind = Literal["modify_objective", "modify_question", "add_question", "add_objective"]
ItemText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=600)]


class ChangeDraft(BaseModel):
    kind: ChangeKind
    target: str | None = Field(default=None, description="O# or RQ# for modify_*")
    text: ItemText
    question_kind: Literal["question", "hypothesis"] = "question"
    linked_objective_ids: list[str] = Field(default_factory=list, description="add_question: objectives it serves")
    question_text: str | None = Field(default=None, description="add_objective: its first research question")
    gap_ids: list[str] = Field(default_factory=list, description="e.g. ['G1']")
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)] = ""


class ChangeList(BaseModel):
    changes: list[ChangeDraft] = Field(default_factory=list)


@dataclass
class Proposal:
    change: dict[str, Any]
    before: str | None
    rationale: str
    gap_ids: list[str]  # ResearchGap ids


class InvalidChangeError(ValueError):
    pass


def _next_id(prefix: str, ids: list[str]) -> str:
    numbers = [int(m.group(1)) for i in ids if (m := re.fullmatch(rf"{prefix}(\d+)", i))]
    return f"{prefix}{max(numbers, default=0) + 1}"


def apply(plan: IdeaPlan, change: dict[str, Any]) -> tuple[IdeaPlan, list[str]]:
    """The plan with `change` applied and validated like any plan edit, plus the ids of items it created."""
    data = plan.model_dump()
    objectives: list[dict[str, Any]] = data["objectives"]
    questions: list[dict[str, Any]] = data["research_questions"]
    kind, text = change["kind"], change["text"].strip()
    created: list[str] = []
    if kind == "modify_objective":
        item = next((o for o in objectives if o["id"] == change.get("target")), None)
        if item is None:
            raise InvalidChangeError(f"Objective {change.get('target')} doesn't exist")
        item["text"] = text
    elif kind == "modify_question":
        item = next((q for q in questions if q["id"] == change.get("target")), None)
        if item is None:
            raise InvalidChangeError(f"Research question {change.get('target')} doesn't exist")
        item["text"] = text
        item["kind"] = change.get("question_kind") or item["kind"]
    elif kind == "add_question":
        qid = _next_id("RQ", [q["id"] for q in questions])
        questions.append({"id": qid, "text": text, "kind": change.get("question_kind") or "question",
                          "linked_objective_ids": list(change.get("linked_objective_ids") or [])})  # fmt: skip
        created.append(qid)
    elif kind == "add_objective":
        oid = _next_id("O", [o["id"] for o in objectives])
        qid = _next_id("RQ", [q["id"] for q in questions])
        if not (change.get("question_text") or "").strip():
            raise InvalidChangeError("A new objective needs a research question")
        objectives.append({"id": oid, "text": text})
        question = change["question_text"].strip()
        questions.append({"id": qid, "text": question, "kind": change.get("question_kind") or "question",
                          "linked_objective_ids": [oid]})  # fmt: skip
        created += [oid, qid]
    else:
        raise InvalidChangeError(f"Unknown change: {kind}")
    try:
        return IdeaPlan.model_validate(data), created
    except ValidationError as exc:
        reason = "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
        raise InvalidChangeError(reason) from exc


def before_text(plan: IdeaPlan, change: dict[str, Any]) -> str | None:
    items = {o.id: o.text for o in plan.objectives} | {q.id: q.text for q in plan.research_questions}
    return items.get(change.get("target") or "") if change["kind"].startswith("modify_") else None


async def propose(
    plan: IdeaPlan, gaps: list[tuple[str, str]], llm: LLMService, project_id: uuid.UUID
) -> list[Proposal]:
    """Changes responding to the accepted gaps [(gap id, statement)], each valid on the current plan."""
    labels = {f"G{i}": gid for i, (gid, _) in enumerate(gaps, start=1)}
    result = await llm.generate_structured(
        REFINE_PLAN.render(
            objectives="\n".join(f"{o.id}: {o.text}" for o in plan.objectives),
            questions="\n".join(
                f"{q.id} ({q.kind}, serves {', '.join(q.linked_objective_ids)}): {q.text}"
                for q in plan.research_questions
            ),
            gaps="\n".join(f"G{i}: {statement}" for i, (_, statement) in enumerate(gaps, start=1)),
        ),  # fmt: skip
        ChangeList,
        agent=AGENT_NAME,
        system=REFINE_PLAN.system,
        project_id=project_id,
        stage=StageName.LITERATURE,
        prompt_id=REFINE_PLAN.id,
    )
    proposals: list[Proposal] = []
    for draft in result.data.changes:
        gap_ids = [labels[g] for g in dict.fromkeys(x.strip().upper() for x in draft.gap_ids) if g in labels]
        if not gap_ids:
            continue  # every change must answer an accepted gap
        change = draft.model_dump(exclude={"gap_ids", "rationale"}, exclude_none=True)
        change["target"] = (draft.target or "").strip().upper() or None
        if not change["target"]:
            change.pop("target")
        before = before_text(plan, change)
        if before is not None and before.strip() == draft.text.strip():
            continue
        try:
            apply(plan, change)
        except InvalidChangeError as exc:
            log.info("refinement_dropped", kind=draft.kind, reason=str(exc)[:200])
            continue
        proposals.append(Proposal(change, before, draft.rationale, gap_ids))
    return proposals[:MAX_CHANGES]
