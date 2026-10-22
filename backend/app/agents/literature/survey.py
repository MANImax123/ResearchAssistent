"""L11 (part 2): the Literature Survey section — the Literature stage's agent (Step 4.10).

Three passes, so the section is organised by theme rather than paper by paper:
  1. outline: the selected papers grouped into 3–6 themes;
  2. one subsection per theme, synthesising across its papers (every paragraph cites);
  3. the opening paragraph and the closing research-gap summary (hedged, from the accepted gaps).
The model cites papers as [P3]; the code turns those into `[@<paper id>]` keys and drops any id that
isn't a selected paper with a verified identifier, so every citation in the section resolves (A4.1).
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.agents.idea.agent import plan_from_state
from app.agents.idea.schemas import IdeaPlan
from app.agents.literature.gaps import is_hedged
from app.agents.prompts.literature import SURVEY_FRAME, SURVEY_OUTLINE, SURVEY_THEME
from app.db.models.enums import StageName
from app.orchestrator.state import ResearchState, StageOutput
from app.services.llm import LLMService

AGENT_NAME = "literature.survey"
NOTES_CHARS = 1800

_REF = re.compile(r"\[\s*(P\d+(?:\s*[,;]\s*P?\d+)*)\s*\]")
_CITES = re.compile(r"\[\s*P\d+")

Paragraph = Annotated[str, StringConstraints(strip_whitespace=True, min_length=40, max_length=3000)]


class Theme(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
    focus: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    papers: list[str] = Field(min_length=1, description="Paper ids, e.g. ['P1', 'P4']")


class Outline(BaseModel):
    themes: list[Theme] = Field(min_length=1, max_length=6)


def _all_cite(paragraphs: list[str]) -> list[str]:
    for i, p in enumerate(paragraphs, start=1):
        if not _CITES.search(p):
            raise ValueError(f"paragraph {i} cites no paper; end its claims with [P#]")
    return paragraphs


class ThemeText(BaseModel):
    paragraphs: list[Paragraph] = Field(min_length=1, max_length=5)

    _cited = field_validator("paragraphs")(_all_cite)


class Frame(BaseModel):
    opening: Paragraph
    gap_summary: list[Paragraph] = Field(min_length=1, max_length=3)

    @field_validator("gap_summary")
    @classmethod
    def _possibilities(cls, paragraphs: list[str]) -> list[str]:
        for i, p in enumerate(paragraphs, start=1):
            if not is_hedged(p):
                raise ValueError(
                    f"gap paragraph {i} states gaps as facts; phrase them as possibilities (may, appears, "
                    "little evidence among the reviewed studies) and avoid 'no study has', 'proves', 'confirmed'"
                )
        return paragraphs


@dataclass
class SurveyPaper:
    id: uuid.UUID
    title: str
    year: int | None
    notes: str  # profile digest (or abstract)


@dataclass
class SurveyGap:
    statement: str
    paper_ids: list[uuid.UUID]


@dataclass
class SurveyInputs:
    papers: list[SurveyPaper]  # selected, verified identifier, in selection order
    gaps: list[SurveyGap]
    gaps_accepted: bool  # False: no gap was accepted yet, these are candidates
    tables: list[str] = field(default_factory=list)  # inserted comparison tables (Markdown blocks)


@dataclass
class SurveyDraft:
    markdown: str
    cited: list[uuid.UUID]
    themes: list[str]
    dropped_citations: int


class NoPapersError(ValueError):
    pass


Loader = Callable[[uuid.UUID, list[str], list[str]], Awaitable[SurveyInputs]]


def to_keys(text: str, labels: dict[str, uuid.UUID]) -> tuple[str, list[uuid.UUID], int]:
    """[P3] / [P1, P4] / [P1; P4] → [@id] chips (one key per bracket); unknown ids are dropped."""
    cited: list[uuid.UUID] = []
    dropped = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal dropped
        keys = []
        for raw in re.split(r"\s*[,;]\s*", match.group(1)):
            label = raw if raw.startswith("P") else f"P{raw}"
            if label in labels:
                keys.append(labels[label])
            else:
                dropped += 1
        cited.extend(keys)
        return "".join(f"[@{k}]" for k in dict.fromkeys(keys))

    out = _REF.sub(replace, text)
    out = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", out))
    return out, cited, dropped


def _plan_text(plan: IdeaPlan | None) -> str:
    if plan is None:
        return "(no plan)"
    return "\n".join(
        [f"Problem: {plan.problem_statement}"]
        + [f"{o.id}: {o.text}" for o in plan.objectives]
        + [f"{q.id}: {q.text}" for q in plan.research_questions]
    )


def _paper_lines(papers: list[tuple[str, SurveyPaper]], *, notes: bool) -> str:
    lines = []
    for label, p in papers:
        head = f"{label} — {p.title}{f' ({p.year})' if p.year else ''}"
        lines.append(f"{head}\n{p.notes[:NOTES_CHARS]}" if notes else head)
    return "\n\n".join(lines) if notes else "\n".join(lines)


def _feedback(text: str | None) -> str:
    return f"The researcher's instructions for this version (follow them closely):\n{text}\n\n" if text else ""


def _fix_outline(outline: Outline, labels: dict[str, uuid.UUID]) -> list[Theme]:
    """Known paper ids only; every paper in some theme (leftovers join the smallest theme)."""
    themes = []
    for t in outline.themes:
        ids = [p for p in dict.fromkeys(x.strip().upper() for x in t.papers) if p in labels]
        if ids:
            themes.append(t.model_copy(update={"papers": ids}))
    if not themes:
        themes = [Theme(title="Related work", papers=list(labels))]
    covered = {p for t in themes for p in t.papers}
    for label in labels:
        if label not in covered:
            min(themes, key=lambda t: len(t.papers)).papers.append(label)
    return themes


async def write(
    inputs: SurveyInputs, plan: IdeaPlan | None, feedback: str | None, llm: LLMService, project_id: uuid.UUID
) -> SurveyDraft:
    if not inputs.papers:
        raise NoPapersError("Select papers with a verified identifier before writing the Literature Survey")
    labelled = [(f"P{i}", p) for i, p in enumerate(inputs.papers, start=1)]
    labels = {label: p.id for label, p in labelled}
    by_label = dict(labelled)
    label_of = {p.id: label for label, p in labelled}
    plan_text = _plan_text(plan)

    async def ask(prompt: str, schema: type[Any], template: Any) -> Any:
        result = await llm.generate_structured(
            prompt, schema, agent=AGENT_NAME, system=template.system, project_id=project_id,
            stage=StageName.LITERATURE, prompt_id=template.id,
        )  # fmt: skip
        return result.data

    outline: Outline = await ask(
        SURVEY_OUTLINE.render(plan=plan_text, papers=_paper_lines(labelled, notes=True), feedback=_feedback(feedback)),
        Outline,
        SURVEY_OUTLINE,
    )
    themes = _fix_outline(outline, labels)

    bodies: list[tuple[str, list[str]]] = []
    for theme in themes:
        text: ThemeText = await ask(
            SURVEY_THEME.render(
                title=theme.title, focus=theme.focus or "(none)", plan=plan_text,
                papers=_paper_lines([(lab, by_label[lab]) for lab in theme.papers], notes=True),
                feedback=_feedback(feedback),
            ),
            ThemeText,
            SURVEY_THEME,
        )  # fmt: skip
        bodies.append((theme.title, text.paragraphs))

    gap_lines = []
    for g in inputs.gaps:
        basis = ", ".join(label_of[p] for p in g.paper_ids if p in label_of) or "no listed paper"
        gap_lines.append(f"- {g.statement} (based on {basis})")
    if gap_lines and not inputs.gaps_accepted:
        gap_lines.insert(0, "(Candidates the researcher has not reviewed yet)")
    frame: Frame = await ask(
        SURVEY_FRAME.render(
            plan=plan_text,
            themes="\n".join(f"{i}. {t.title}" for i, t in enumerate(themes, start=1)),
            papers=_paper_lines(labelled, notes=False),
            gaps="\n".join(gap_lines) or "(none identified: summarise the open problems the limitations suggest)",
            feedback=_feedback(feedback),
        ),
        Frame,
        SURVEY_FRAME,
    )

    parts = [frame.opening]
    for title, paragraphs in bodies:
        parts += [f"### {title}", *paragraphs]
    parts += inputs.tables
    parts += ["### Research gaps", *frame.gap_summary]
    markdown, cited, dropped = to_keys("\n\n".join(parts), labels)
    return SurveyDraft(markdown + "\n", list(dict.fromkeys(cited)), [t for t, _ in bodies], dropped)


def make_literature_agent(llm_factory: Callable[[], LLMService], load: Loader) -> Callable[..., Any]:
    async def literature_agent(state: ResearchState) -> dict[str, Any]:
        project_id = uuid.UUID(state["project_id"])
        inputs = await load(project_id, list(state.get("selected_paper_ids") or []), list(state.get("gap_ids") or []))
        draft = await write(inputs, plan_from_state(state), state.get("pending_feedback"), llm_factory(), project_id)
        output = StageOutput(draft=draft.markdown, agent=AGENT_NAME, generated_at=datetime.now(UTC).isoformat())
        return {"stage_outputs": {StageName.LITERATURE.value: output}}

    return literature_agent
