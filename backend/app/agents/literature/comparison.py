"""L7 Comparative Analysis: a comparison matrix of the selected papers.

Built-in columns (Method, Dataset, Metric, Result, Limitation) are short cells condensed by the LLM
from each paper's evidence-backed profile (4.5), or from its abstract when there is no full text.
Cells inherit the profile field's evidence; a cell may not contain a number its source doesn't
(otherwise the source text itself is used). Custom columns are answered per paper from that paper's
own passages, citing them; unanswered cells stay empty.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from app.agents.prompts.literature import COMPARE, COMPARE_COLUMN
from app.db.models.enums import StageName
from app.services.llm import LLMService

AGENT_NAME = "literature.comparison"
BATCH = 6
BUILTIN = ("method", "dataset", "metric", "result", "limitation")
LABELS = {"method": "Method", "dataset": "Dataset", "metric": "Metric", "result": "Result", "limitation": "Limitation"}
# Which profile field (4.5) each built-in column summarises
PROFILE_FIELD = {"method": "method", "dataset": "dataset", "metric": "metrics", "result": "results",
                 "limitation": "limitations"}  # fmt: skip
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

Cell = Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)]


class RowDraft(BaseModel):
    paper: str = Field(description="The paper id, e.g. P2")
    method: Cell = ""
    dataset: Cell = ""
    metric: Cell = ""
    result: Cell = ""
    limitation: Cell = ""


class MatrixDraft(BaseModel):
    rows: list[RowDraft]


class ColumnAnswer(BaseModel):
    paper: str
    value: Cell = ""
    evidence: list[str] = Field(default_factory=list, description="Passage ids, e.g. ['P2-C3']")


class ColumnDraft(BaseModel):
    answers: list[ColumnAnswer]


@dataclass
class PaperFacts:
    """What the matrix is built from for one paper: its profile, or just its abstract."""

    paper_id: uuid.UUID
    title: str
    year: int | None
    profile: dict[str, Any] | None  # field -> value(s); None if not analysed
    evidence: dict[str, Any] = field(default_factory=dict)  # profile evidence (4.5 layout)
    abstract: str | None = None

    def source_text(self, column: str) -> str:
        if self.profile is None:
            return self.abstract or ""
        value = self.profile.get(PROFILE_FIELD[column])
        return "; ".join(value) if isinstance(value, list) else (value or "")

    def cell_evidence(self, column: str) -> list[str]:
        if self.profile is None:
            return []
        ev = self.evidence.get(PROFILE_FIELD[column], [])
        flat = [i for item in ev for i in item] if ev and isinstance(ev[0], list) else list(ev)
        return list(dict.fromkeys(flat))


def grounded(cell: str, source: str) -> bool:
    """Every number in the cell appears in the source text (no invented figures)."""
    have = {n.replace(",", ".") for n in NUMBER.findall(source)}
    return all(n.replace(",", ".") in have for n in NUMBER.findall(cell))


def _describe(index: int, facts: PaperFacts) -> str:
    head = f"P{index} — {facts.title}{f' ({facts.year})' if facts.year else ''}"
    if facts.profile is None:
        return f"{head}\nAbstract only: {facts.abstract or '(none)'}"
    lines = [f"{LABELS[c]}: {facts.source_text(c) or '(not stated)'}" for c in BUILTIN]
    return head + "\n" + "\n".join(lines)


@dataclass
class BuiltCell:
    value: str
    source: str  # "profile" | "abstract"
    evidence: list[str]


async def build_rows(
    papers: list[PaperFacts], llm: LLMService, project_id: uuid.UUID
) -> dict[uuid.UUID, dict[str, BuiltCell]]:
    """Concise built-in cells for each paper (batched); ungrounded or missing cells fall back to the source."""
    rows: dict[uuid.UUID, dict[str, BuiltCell]] = {}
    for start in range(0, len(papers), BATCH):
        batch = papers[start : start + BATCH]
        result = await llm.generate_structured(
            COMPARE.render(papers="\n\n".join(_describe(i, f) for i, f in enumerate(batch, start=1))),
            MatrixDraft,
            agent=AGENT_NAME,
            system=COMPARE.system,
            fast=True,
            project_id=project_id,
            stage=StageName.LITERATURE,
            prompt_id=COMPARE.id,
        )
        drafts = {d.paper.strip().upper(): d for d in result.data.rows}
        for i, facts in enumerate(batch, start=1):
            draft = drafts.get(f"P{i}")
            cells: dict[str, BuiltCell] = {}
            for column in BUILTIN:
                source = facts.source_text(column)
                proposed = getattr(draft, column, "") if draft else ""
                if facts.profile is not None and not source:
                    continue  # the profile says nothing here: leave the cell empty
                value = proposed if proposed and grounded(proposed, source) else ""
                if not value and facts.profile is not None:
                    value = source[:300]
                if value:
                    kind = "profile" if facts.profile is not None else "abstract"
                    cells[column] = BuiltCell(value, kind, facts.cell_evidence(column))
            rows[facts.paper_id] = cells
    return rows


@dataclass(frozen=True)
class ColumnPassage:
    chunk_id: uuid.UUID
    section: str | None
    page: int | None
    text: str


async def fill_column(
    question: str,
    papers: list[tuple[uuid.UUID, str, list[ColumnPassage]]],
    llm: LLMService,
    project_id: uuid.UUID,
) -> dict[uuid.UUID, BuiltCell]:
    """Answer a custom column for each paper from its own passages; only cited answers are kept."""
    filled: dict[uuid.UUID, BuiltCell] = {}
    for start in range(0, len(papers), BATCH):
        batch = papers[start : start + BATCH]
        known: dict[str, uuid.UUID] = {}
        blocks = []
        for i, (_, title, passages) in enumerate(batch, start=1):
            lines = [f"P{i} — {title}"]
            for j, p in enumerate(passages, start=1):
                pid = f"P{i}-C{j}"
                known[pid] = p.chunk_id
                lines.append(f"[{pid}] {p.section or ''}{f' (p. {p.page})' if p.page else ''}: {p.text[:1200]}")
            blocks.append("\n".join(lines))
        result = await llm.generate_structured(
            COMPARE_COLUMN.render(column=question, papers="\n\n".join(blocks)),
            ColumnDraft,
            agent=AGENT_NAME,
            system=COMPARE_COLUMN.system,
            fast=True,
            project_id=project_id,
            stage=StageName.LITERATURE,
            prompt_id=COMPARE_COLUMN.id,
        )
        for answer in result.data.answers:
            index = answer.paper.strip().upper().removeprefix("P")
            if not index.isdigit() or not (1 <= int(index) <= len(batch)) or not answer.value:
                continue
            prefix = f"P{index}-"
            ids = [str(known[e.strip().upper()]) for e in answer.evidence
                   if e.strip().upper().startswith(prefix) and e.strip().upper() in known]  # fmt: skip
            if ids:
                filled[batch[int(index) - 1][0]] = BuiltCell(answer.value, "ai", list(dict.fromkeys(ids)))
    return filled
