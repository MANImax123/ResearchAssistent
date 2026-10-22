"""L8 Contradiction Detection: conflicting claims between papers that study the same thing.

Profiled papers are grouped by a shared dataset (or, failing that, a shared metric). For each group
the LLM compares the papers pairwise, from each paper's result passages plus the passages most
related to the shared topic, and reports claim A vs claim B with evidence and a possible
explanation. The code keeps only contradictions between two different papers of the group whose
evidence comes from the right paper's passages.
"""

import re
import uuid
from dataclasses import dataclass
from itertools import combinations
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.agents.prompts.literature import CONTRADICTIONS
from app.db.models.enums import StageName
from app.services.llm import LLMService

AGENT_NAME = "literature.contradictions"
MAX_GROUP = 6  # papers compared together (15 pairs)
MAX_GROUPS = 8
PASSAGE_CHARS = 2000

ExplanationKind = Literal[
    "different_setup", "dataset_version", "metric_definition", "data_split", "genuine_disagreement", "unclear"
]
Claim = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=600)]


class ContradictionDraft(BaseModel):
    paper_a: str = Field(description="e.g. P1")
    paper_b: str = Field(description="e.g. P3")
    claim_a: Claim
    claim_b: Claim
    evidence_a: list[str] = Field(default_factory=list, description="Passage ids of paper A, e.g. ['P1-C2']")
    evidence_b: list[str] = Field(default_factory=list)
    explanation: Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)] = ""
    explanation_kind: ExplanationKind = "unclear"


class ContradictionList(BaseModel):
    contradictions: list[ContradictionDraft] = Field(default_factory=list)


@dataclass(frozen=True)
class Passage:
    chunk_id: uuid.UUID
    section: str | None
    page: int | None
    text: str


@dataclass
class GroupPaper:
    paper_id: uuid.UUID
    title: str
    year: int | None
    passages: list[Passage]


@dataclass
class Group:
    topic: str
    paper_ids: list[uuid.UUID]


@dataclass
class Found:
    paper_a: uuid.UUID
    paper_b: uuid.UUID
    claim_a: str
    claim_b: str
    evidence_a: list[str]
    evidence_b: list[str]
    explanation: str
    explanation_kind: str
    topic: str


def normalize(name: str) -> str:
    """'PDBbind v2016 core set' and 'PDBbind-2016 Core' compare equal enough for grouping."""
    text = re.sub(r"[^a-z0-9]+", " ", re.sub(r"\bv(?=\d)", "", name.lower()))  # "v2016" → "2016"
    return " ".join(w for w in text.split() if w not in {"the", "dataset", "set", "data", "benchmark", "v"})


def groups(profiles: dict[uuid.UUID, tuple[list[str], list[str]]]) -> list[Group]:
    """Papers sharing a dataset (largest groups first), then a metric for papers not grouped yet."""
    found: list[Group] = []
    covered_pairs: set[frozenset[uuid.UUID]] = set()
    for field_index, label in ((0, "dataset"), (1, "metric")):
        by_name: dict[str, tuple[str, set[uuid.UUID]]] = {}
        for paper_id, fields in profiles.items():
            for name in fields[field_index]:
                key = normalize(name)
                if key:
                    _, ids = by_name.setdefault(key, (name, set()))
                    ids.add(paper_id)
        for shown, ids in sorted(by_name.values(), key=lambda v: -len(v[1])):
            if len(ids) < 2:
                continue
            pairs = {frozenset(p) for p in combinations(sorted(ids), 2)}
            if pairs <= covered_pairs:
                continue  # every pair already compared in another group
            covered_pairs |= pairs
            found.append(Group(f"{label}: {shown}", sorted(ids)[:MAX_GROUP]))
            if len(found) >= MAX_GROUPS:
                return found
    return found


def _paper_block(index: int, paper: GroupPaper) -> tuple[str, dict[str, uuid.UUID]]:
    known: dict[str, uuid.UUID] = {}
    lines = [f"P{index} — {paper.title}{f' ({paper.year})' if paper.year else ''}"]
    for j, p in enumerate(paper.passages, start=1):
        pid = f"P{index}-C{j}"
        known[pid] = p.chunk_id
        lines.append(f"[{pid}] {p.section or ''}{f' (p. {p.page})' if p.page else ''}: {p.text[:PASSAGE_CHARS]}")
    return "\n".join(lines), known


def _ids(evidence: list[str], index: int, known: dict[str, uuid.UUID]) -> list[str]:
    prefix = f"P{index}-"
    return list(dict.fromkeys(str(known[e.strip().upper()]) for e in evidence
                              if e.strip().upper().startswith(prefix) and e.strip().upper() in known))  # fmt: skip


async def detect(topic: str, papers: list[GroupPaper], llm: LLMService, project_id: uuid.UUID) -> list[Found]:
    """Contradictions within one group, each with evidence from both papers' own passages."""
    blocks, known = [], {}
    for i, paper in enumerate(papers, start=1):
        block, ids = _paper_block(i, paper)
        blocks.append(block)
        known.update(ids)
    result = await llm.generate_structured(
        CONTRADICTIONS.render(topic=topic, papers="\n\n".join(blocks)),
        ContradictionList,
        agent=AGENT_NAME,
        system=CONTRADICTIONS.system,
        project_id=project_id,
        stage=StageName.LITERATURE,
        prompt_id=CONTRADICTIONS.id,
    )
    found: list[Found] = []
    for c in result.data.contradictions:
        a, b = (x.strip().upper().removeprefix("P") for x in (c.paper_a, c.paper_b))
        if not (a.isdigit() and b.isdigit()) or a == b:
            continue
        ia, ib = int(a), int(b)
        if not (1 <= ia <= len(papers) and 1 <= ib <= len(papers)):
            continue
        ev_a, ev_b = _ids(c.evidence_a, ia, known), _ids(c.evidence_b, ib, known)
        if not ev_a or not ev_b:
            continue  # each claim must come from its own paper's passages
        found.append(Found(papers[ia - 1].paper_id, papers[ib - 1].paper_id, c.claim_a, c.claim_b, ev_a, ev_b,
                           name_papers(c.explanation, papers), c.explanation_kind, topic))  # fmt: skip
    return found


def name_papers(text: str, papers: list[GroupPaper]) -> str:
    """The prompt's labels ("Paper P1", "P2") replaced with the papers' names for the researcher."""

    def name(match: re.Match[str]) -> str:
        i = int(match.group(1))
        if not 1 <= i <= len(papers):
            return match.group(0)
        title = papers[i - 1].title
        head = title.split(":", 1)[0].strip()
        if head != title and len(head) <= 40:
            return head
        return f"“{title[:50].rstrip()}…”" if len(title) > 50 else f"“{title}”"

    return re.sub(r"\b(?:[Pp]aper\s+)?P(\d+)\b(?!-C)", name, text)
