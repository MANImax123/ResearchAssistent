"""L6 Paper Analysis: a structured profile of one paper with evidence for every field.

The model reads the paper's passages, numbered [C1]…[Cn] (abstract, methodology, data, results,
conclusion first; appendices last, within a word budget), and returns problem, method, results,
datasets, metrics, limitations and tech stack, each citing passage ids. The code then keeps only
evidence that names a passage it was shown, and drops any value that ends up without evidence:
nothing in a profile is ungrounded (A4.2).
"""

import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from app.agents.prompts.literature import ANALYZE
from app.db.models.enums import StageName
from app.services.llm import LLMService

AGENT_NAME = "literature.analysis"
WORD_BUDGET = 14_000  # passages beyond this are left out (≈ 19k tokens of paper text)
KIND_ORDER = ["abstract", "methodology", "dataset", "results", "limitations", "conclusion", "discussion",
              "introduction", "related_work", "other", "appendix"]  # fmt: skip
TEXT_FIELDS = ("problem", "method", "results")
LIST_FIELDS = ("dataset", "metrics", "limitations", "tech_stack")

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]


class Cited(BaseModel):
    text: Long
    evidence: list[str] = Field(default_factory=list, description="Passage ids, e.g. ['C3']")


class CitedItem(BaseModel):
    name: Short
    evidence: list[str] = Field(default_factory=list)


class ProfileDraft(BaseModel):
    problem: Cited | None = None
    method: Cited | None = None
    results: Cited | None = None
    datasets: list[CitedItem] = Field(default_factory=list)
    metrics: list[CitedItem] = Field(default_factory=list)
    limitations: list[Cited] = Field(default_factory=list)
    tech_stack: list[CitedItem] = Field(default_factory=list)


@dataclass(frozen=True)
class Passage:
    chunk_id: uuid.UUID
    section: str | None
    kind: str | None
    page: int | None
    text: str


@dataclass
class Profile:
    """Grounded profile: values plus the chunk ids that support each (list fields: one list per item)."""

    problem: str | None = None
    method: str | None = None
    results: str | None = None
    dataset: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    dropped: int = 0  # values removed for lack of valid evidence
    run_id: str | None = None


def select_passages(passages: list[Passage], budget: int = WORD_BUDGET) -> list[Passage]:
    """The most informative passages first (by section kind), in reading order within a kind, up to the budget."""
    rank = {kind: i for i, kind in enumerate(KIND_ORDER)}
    ordered = sorted(enumerate(passages), key=lambda ip: (rank.get(ip[1].kind or "other", len(rank)), ip[0]))
    chosen: list[tuple[int, Passage]] = []
    words = 0
    for index, passage in ordered:
        n = len(passage.text.split())
        if words + n > budget:
            continue
        chosen.append((index, passage))
        words += n
    return [p for _, p in sorted(chosen)]  # back to reading order, so [C#] follow the paper


def _ids(evidence: list[str], known: dict[str, uuid.UUID]) -> list[str]:
    return list(dict.fromkeys(str(known[e.strip().upper()]) for e in evidence if e.strip().upper() in known))


def ground(draft: ProfileDraft, known: dict[str, uuid.UUID]) -> Profile:
    """Map [C#] evidence to chunk ids; drop values (and items) without any valid evidence."""
    profile = Profile()
    for name in TEXT_FIELDS:
        value: Cited | None = getattr(draft, name)
        if value is None:
            continue
        ids = _ids(value.evidence, known)
        if ids:
            setattr(profile, name, value.text)
            profile.evidence[name] = ids
        else:
            profile.dropped += 1
    lists: dict[str, list[Any]] = {"dataset": draft.datasets, "metrics": draft.metrics,
                                   "limitations": draft.limitations, "tech_stack": draft.tech_stack}  # fmt: skip
    for name, items in lists.items():
        values: list[str] = []
        evidence: list[list[str]] = []
        seen: set[str] = set()
        for item in items:
            text = item.text if isinstance(item, Cited) else item.name
            ids = _ids(item.evidence, known)
            if not ids or text.lower() in seen:
                profile.dropped += 0 if ids else 1
                continue
            seen.add(text.lower())
            values.append(text)
            evidence.append(ids)
        setattr(profile, name, values)
        if values:
            profile.evidence[name] = evidence
    return profile


async def analyze_paper(
    title: str, passages: list[Passage], llm: LLMService, project_id: uuid.UUID
) -> tuple[Profile, list[Passage]]:
    """Profile one paper. Returns the profile and the passages it was shown (C1… in that order)."""
    shown = select_passages(passages)
    known = {f"C{i}": p.chunk_id for i, p in enumerate(shown, start=1)}
    numbered = "\n\n".join(
        f"[C{i}] {p.section or 'Untitled section'}{f' (p. {p.page})' if p.page else ''}\n{p.text}"
        for i, p in enumerate(shown, start=1)
    )
    result = await llm.generate_structured(
        ANALYZE.render(title=title, passages=numbered),
        ProfileDraft,
        agent=AGENT_NAME,
        system=ANALYZE.system,
        project_id=project_id,
        stage=StageName.LITERATURE,
        prompt_id=ANALYZE.id,
    )
    profile = ground(result.data, known)
    profile.run_id = str(result.run.run_id)
    return profile, shown
