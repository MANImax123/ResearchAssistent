"""L10 Research Gap Detection: *candidate* gaps, never facts (A4.3).

The code gathers signals from the selected papers — limitations (L#), open contradictions (C#),
method × dataset combinations no selected paper reports (U#, from the knowledge graph) and
future-work statements (F#) — and the LLM proposes 3–8 potential gaps citing them. The code keeps
only gaps that cite real signals, caps confidence by how much independent evidence there is, and
makes sure every statement reads as a possibility (rewording, then dropping, any that don't).
"""

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.agents.idea.schemas import IdeaPlan
from app.agents.prompts.literature import GAPS, GAPS_REWORD
from app.db.models.enums import StageName
from app.services.llm import LLMService

AGENT_NAME = "literature.gaps"
MAX_GAPS = 8
SIGNAL_CHARS = 500

SignalKind = Literal["limitation", "contradiction", "untested", "future_work"]
Confidence = Literal["low", "medium", "high"]
PREFIX: dict[SignalKind, str] = {"limitation": "L", "contradiction": "C", "untested": "U", "future_work": "F"}
CONFIDENCE_ORDER: list[Confidence] = ["low", "medium", "high"]

# Wording that frames a statement as a possibility, and wording that asserts certainty (A4.3)
_HEDGED = re.compile(
    r"\b(may|might|could|appears?|seems?|possibl[ey]|potential(ly)?|suggests?|unclear|uncertain|little|limited|"
    r"few|rarely|not yet|open question|remains? (open|unclear|unexplored)|whether|underexplored|under-explored)\b",
    re.IGNORECASE,
)
_CERTAIN = re.compile(
    r"\b(confirm(ed|s)?|proves?|proven|definitely|certainly|undoubtedly|clearly|no (one|study|studies|research|"
    r"work|paper|papers) (has|have)( ever)?|it is (a )?fact|never been)\b",
    re.IGNORECASE,
)
_FUTURE_WORK = re.compile(
    r"\b(future (work|research|studies|directions?)|in the future|remains? (an? )?(open|unexplored|unclear|"
    r"challenging)|further (work|research|investigation|studies) (is|are) (needed|required)|left for future|"
    r"we (plan|intend|hope) to|not (yet )?(been )?(explored|investigated|addressed|studied))\b",
    re.IGNORECASE,
)
FUTURE_WORK_SQL = (
    r"(future (work|research|studies|direction)|in the future|remains? (an? )?(open|unexplored|unclear|challenging)|"
    r"further (work|research|investigation|studies) (is|are) (needed|required)|left for future|"
    r"we (plan|intend|hope) to|not (yet )?(been )?(explored|investigated|addressed|studied))"
)

# Libraries and tools profiles list next to methods (canonical keys): "never run with PyTorch" is no gap
SOFTWARE = {
    "python",
    "pytorch",
    "pytorch geometric",
    "torch",
    "tensorflow",
    "keras",
    "jax",
    "scikit learn",
    "sklearn",
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "seaborn",
    "rdkit",
    "deepchem",
    "open babel",
    "openbabel",
    "biopython",
    "networkx",
    "dgl",
    "cuda",
    "github",
    "github repository",
    "hugging face",
    "huggingface",
    "transformers",
    "r",
    "matlab",
    "java",
    "c++",
    "docker",
    "jupyter",
    "weka",
    "xgboost library",
    "pymol",
    "chimera",
}

Statement = Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=500)]


class GapDraft(BaseModel):
    statement: Statement = Field(description="The potential gap, specific and phrased as a possibility")
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1500)] = ""
    sources: list[str] = Field(default_factory=list, description="Signal ids, e.g. ['L3', 'F1']")
    confidence: Confidence = "low"
    objective_ids: list[str] = Field(default_factory=list, description="Plan objectives it could serve, e.g. ['O2']")


class GapList(BaseModel):
    gaps: list[GapDraft] = Field(default_factory=list)


class Reworded(BaseModel):
    number: int
    statement: Statement


class RewordList(BaseModel):
    statements: list[Reworded] = Field(default_factory=list)


@dataclass
class Signal:
    id: str  # "L3"
    kind: SignalKind
    text: str
    paper_ids: list[uuid.UUID]
    chunk_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class Candidate:
    statement: str
    rationale: str
    confidence: Confidence
    objective_ids: list[str]
    signals: list[Signal]


def number(signals: list[tuple[SignalKind, str, list[uuid.UUID], list[uuid.UUID]]]) -> list[Signal]:
    """Give each signal its prompt id: L1, L2, …, C1, …"""
    counts: dict[str, int] = {}
    out = []
    for kind, text, papers, chunks in signals:
        counts[kind] = counts.get(kind, 0) + 1
        out.append(Signal(f"{PREFIX[kind]}{counts[kind]}", kind, text, papers, chunks))
    return out


def is_hedged(statement: str) -> bool:
    """True when the statement reads as a possibility and asserts nothing as certain."""
    return bool(_HEDGED.search(statement)) and not _CERTAIN.search(statement)


def future_work_sentences(text: str, limit: int = 2) -> list[str]:
    """Sentences that state future work or an open problem."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", " ".join(text.split()))
    return [s for s in sentences if _FUTURE_WORK.search(s) and 30 <= len(s) <= 600][:limit]


def untested_combinations(
    uses: dict[str, set[str]],
    evaluated: dict[str, set[str]],
    *,
    limit: int = 10,
) -> list[tuple[str, str]]:
    """(method, dataset) pairs no selected paper reports, where the dataset is a shared benchmark (≥ 2 papers)
    and we know where the method *was* evaluated (so absence isn't just missing data). Most-used first."""
    evaluated_by_paper: dict[str, set[str]] = {}
    for dataset, papers in evaluated.items():
        for p in papers:
            evaluated_by_paper.setdefault(p, set()).add(dataset)
    found = []
    for method, method_papers in uses.items():
        if method in SOFTWARE or not any(evaluated_by_paper.get(p) for p in method_papers):
            continue
        tested = set().union(*(evaluated_by_paper.get(p, set()) for p in method_papers))
        for dataset, dataset_papers in evaluated.items():
            if len(dataset_papers) >= 2 and dataset not in tested:
                found.append((len(method_papers) * len(dataset_papers), method, dataset))
    found.sort(key=lambda f: (-f[0], f[1], f[2]))
    return [(m, d) for _, m, d in found[:limit]]


def by_method(pairs: list[tuple[str, str]], *, datasets: int = 3, methods: int = 8) -> list[tuple[str, list[str]]]:
    """One signal per method (its untested benchmarks together), most relevant first."""
    grouped: dict[str, list[str]] = {}
    for method, dataset in pairs:
        if len(grouped.setdefault(method, [])) < datasets:
            grouped[method].append(dataset)
    return list(grouped.items())[:methods]


def cap_confidence(confidence: Confidence, signals: list[Signal]) -> Confidence:
    """High needs several independent papers; a gap resting only on untested combinations stays low."""
    papers = {p for s in signals for p in s.paper_ids}
    cap: Confidence = "high"
    if all(s.kind == "untested" for s in signals):
        cap = "low"
    elif len(papers) < 2 or len(signals) < 2:
        cap = "medium"
    return min(confidence, cap, key=CONFIDENCE_ORDER.index)


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, _normal(a), _normal(b)).ratio() >= 0.8


def _plan_lines(plan: IdeaPlan | None) -> tuple[str, str]:
    if plan is None:
        return "(no plan)", "(no plan)"
    objectives = "\n".join(f"{o.id}: {o.text}" for o in plan.objectives)
    questions = "\n".join(f"{q.id}: {q.text}" for q in plan.research_questions)
    return objectives or "(none)", questions or "(none)"


async def propose(
    signals: list[Signal],
    plan: IdeaPlan | None,
    decided: list[str],
    llm: LLMService,
    project_id: uuid.UUID,
) -> list[Candidate]:
    """Potential gaps citing the given signals; only traceable, hedged, new ones are returned."""
    objectives, questions = _plan_lines(plan)
    by_id = {s.id: s for s in signals}
    result = await llm.generate_structured(
        GAPS.render(
            objectives=objectives,
            questions=questions,
            decided="\n".join(f"- {d}" for d in decided) or "(none)",
            signals="\n".join(f"[{s.id}] ({s.kind.replace('_', ' ')}) {s.text[:SIGNAL_CHARS]}" for s in signals),
        ),
        GapList,
        agent=AGENT_NAME,
        system=GAPS.system,
        project_id=project_id,
        stage=StageName.LITERATURE,
        prompt_id=GAPS.id,
    )
    objective_ids = {o.id for o in plan.objectives} if plan else set()
    candidates: list[Candidate] = []
    for draft in result.data.gaps:
        cited = [by_id[i] for i in dict.fromkeys(s.strip().upper() for s in draft.sources) if i in by_id]
        if not cited:
            continue  # untraceable
        if any(similar(draft.statement, d) for d in decided) or any(
            similar(draft.statement, c.statement) for c in candidates
        ):
            continue
        candidates.append(
            Candidate(
                statement=draft.statement,
                rationale=draft.rationale,
                confidence=cap_confidence(draft.confidence, cited),
                objective_ids=[o for o in dict.fromkeys(x.strip().upper() for x in draft.objective_ids)
                               if o in objective_ids],
                signals=cited,
            )
        )  # fmt: skip
    candidates = await _hedge(candidates, llm, project_id)
    return candidates[:MAX_GAPS]


async def _hedge(candidates: list[Candidate], llm: LLMService, project_id: uuid.UUID) -> list[Candidate]:
    """Reword statements that read as facts once; drop any that still do."""
    certain = [i for i, c in enumerate(candidates) if not is_hedged(c.statement)]
    if certain:
        result = await llm.generate_structured(
            GAPS_REWORD.render(statements="\n".join(f"{n}. {candidates[i].statement}"
                                                    for n, i in enumerate(certain, start=1))),
            RewordList,
            agent=AGENT_NAME,
            system=GAPS_REWORD.system,
            fast=True,
            project_id=project_id,
            stage=StageName.LITERATURE,
            prompt_id=GAPS_REWORD.id,
        )  # fmt: skip
        for item in result.data.statements:
            if 1 <= item.number <= len(certain):
                candidates[certain[item.number - 1]].statement = item.statement
    return [c for c in candidates if is_hedged(c.statement)]
