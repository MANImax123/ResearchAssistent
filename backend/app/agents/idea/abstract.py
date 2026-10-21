"""Abstract agent (Step 3.3): an early working abstract from the research plan.

Length control: the model is asked for the venue's word range; if the draft misses it (or skips an
objective) it is rewritten with the problems spelled out, up to MAX_ATTEMPTS. The best attempt is
kept even if it is still outside the range, and the result says so; the researcher edits from there.
The abstract is finalised from real results in Step 7.2.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.agents.idea.schemas import IdeaPlan
from app.agents.prompts.abstract import ABSTRACT, ABSTRACT_FIX
from app.db.models.enums import StageName
from app.services.llm import LLMService
from app.services.sections import word_count

AGENT_NAME = "idea.abstract"
MAX_ATTEMPTS = 3


def plan_fingerprint(plan: IdeaPlan) -> str:
    """Identifies the plan content an abstract was written from (the chosen title doesn't count)."""
    content = plan.model_dump_json(exclude={"title", "title_options"})
    return hashlib.sha256(content.encode()).hexdigest()[:16]


class AbstractDraft(BaseModel):
    abstract: Annotated[str, StringConstraints(strip_whitespace=True, min_length=100, max_length=4000)]
    objectives_covered: list[str] = Field(description="Ids of the objectives the abstract addresses, e.g. O1")


@dataclass
class AbstractResult:
    text: str
    word_count: int
    min_words: int
    max_words: int
    within_limit: bool
    attempts: int
    run_ids: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    """One paragraph of plain text, whatever formatting the model added."""
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"^\s*abstract\s*[:.\-—]\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _problems(draft: str, covered: list[str], plan: IdeaPlan, limits: tuple[int, int]) -> list[str]:
    lo, hi = limits
    problems = []
    n = word_count(draft)
    if n < lo:
        problems.append(f"It has {n} words; it must have at least {lo}. Add substance, not filler.")
    elif n > hi:
        problems.append(f"It has {n} words; it must have at most {hi}. Cut {n - hi} or more words.")
    missing = [o.id for o in plan.objectives if o.id not in covered]
    if missing:
        texts = "; ".join(f"{o.id}: {o.text}" for o in plan.objectives if o.id in missing)
        problems.append(f"It does not address these objectives: {texts}")
    return problems


def _distance(n: int, limits: tuple[int, int]) -> int:
    lo, hi = limits
    return max(lo - n, n - hi, 0)


async def write_abstract(
    plan: IdeaPlan, llm: LLMService, project_id: uuid.UUID, limits: tuple[int, int], venue: str | None
) -> AbstractResult:
    plan_json = plan.model_dump_json(indent=2, exclude={"title_options"})
    lo, hi = limits
    prompt = ABSTRACT.render(plan=plan_json, venue=venue or "(not decided)", min_words=lo, max_words=hi)
    prompt_id = ABSTRACT.id
    best: AbstractResult | None = None
    run_ids: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = await llm.generate_structured(
            prompt,
            AbstractDraft,
            agent=AGENT_NAME,
            system=ABSTRACT.system,
            project_id=project_id,
            stage=StageName.IDEA,
            prompt_id=prompt_id,
        )
        run_ids.append(str(result.run.run_id))
        text = _clean(result.data.abstract)
        n = word_count(text)
        problems = _problems(text, result.data.objectives_covered, plan, limits)
        candidate = AbstractResult(text, n, lo, hi, lo <= n <= hi, attempt)
        if best is None or _distance(n, limits) <= _distance(best.word_count, limits):
            best = candidate
        if not problems:
            break
        prompt = ABSTRACT_FIX.render(
            plan=plan_json, previous=text, problems="\n".join(f"- {p}" for p in problems), min_words=lo, max_words=hi
        )
        prompt_id = ABSTRACT_FIX.id
    assert best is not None
    best.attempts = len(run_ids)
    best.run_ids = run_ids
    return best
