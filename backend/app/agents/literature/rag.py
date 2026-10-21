"""L5 "Ask the literature": answer questions from retrieved passages, with chunk-level citations.

The model sees numbered passages [S1]…[Sk] and must cite them. Citations are then checked in code:
markers that don't refer to a retrieved passage are removed and reported, and an answer that cites
nothing (without saying the passages are insufficient) is sent back once for repair. Every citation in
the result therefore points at a chunk that was actually retrieved (A4.2 grounded claims).
"""

import re
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.agents.prompts.literature import ASK
from app.db.models.enums import StageName
from app.services.llm import LLMService
from app.services.retrieval import Retrieved

AGENT_NAME = "literature.ask"
CITATION = re.compile(r"\[S(\d+)\]")
PASSAGE_CHARS = 2200


class AnswerDraft(BaseModel):
    answer: str = Field(description="The answer, every claim followed by [S#] markers")
    insufficient: bool = Field(default=False, description="True if the passages don't (fully) answer it")


@dataclass
class Citation:
    marker: str  # "S3"
    source: Retrieved


@dataclass
class Answer:
    text: str
    citations: list[Citation]  # in order of first use
    insufficient: bool
    removed_markers: list[str] = field(default_factory=list)  # markers the model made up (removed)
    run_id: str | None = None


def _passage(index: int, r: Retrieved) -> str:
    where = ", ".join(x for x in (r.section, f"p. {r.page}" if r.page else None) if x)
    return f"[S{index}] {r.paper_title}{f' ({r.year})' if r.year else ''} — {where}\n{r.text[:PASSAGE_CHARS]}"


def check_citations(text: str, sources: list[Retrieved]) -> tuple[str, list[Citation], list[str]]:
    """Keep only markers for retrieved passages; return (clean text, citations in order, removed)."""
    removed: list[str] = []
    used: dict[int, Citation] = {}

    def keep(match: re.Match[str]) -> str:
        n = int(match.group(1))
        if 1 <= n <= len(sources):
            used.setdefault(n, Citation(f"S{n}", sources[n - 1]))
            return match.group(0)
        removed.append(match.group(0))
        return ""

    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", CITATION.sub(keep, text)).strip()
    return cleaned, list(used.values()), removed


async def answer(question: str, sources: list[Retrieved], llm: LLMService, project_id: uuid.UUID) -> Answer:
    if not sources:
        return Answer("None of the indexed passages match this question.", [], True)
    passages = "\n\n".join(_passage(i, r) for i, r in enumerate(sources, start=1))
    prompt = ASK.render(question=question, passages=passages)
    result = None
    for _ in range(2):
        result = await llm.generate_structured(
            prompt,
            AnswerDraft,
            agent=AGENT_NAME,
            system=ASK.system,
            project_id=project_id,
            stage=StageName.LITERATURE,
            prompt_id=ASK.id,
        )
        text, citations, removed = check_citations(result.data.answer, sources)
        if citations or result.data.insufficient:
            return Answer(text, citations, result.data.insufficient, removed, str(result.run.run_id))
        prompt = (
            f"{ASK.render(question=question, passages=passages)}\n\nYour previous answer cited no passage. Every "
            "claim must end with [S#] markers from the list above; if they can't answer it, say so and set "
            "`insufficient` to true."
        )
    assert result is not None
    text, citations, removed = check_citations(result.data.answer, sources)
    return Answer(text, citations, True, removed, str(result.run.run_id))
