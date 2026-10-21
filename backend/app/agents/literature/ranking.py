"""L3 Relevance Ranking.

1. Every paper's title + abstract is embedded once (stored on the paper); every research question is
   embedded as a query. Cosine similarity per RQ; a paper's `similarity` is its best RQ match.
2. The `top_n` most similar papers (plus every must-include paper) are judged by the LLM in batches:
   a 0–10 score, the RQs it informs, and a one-sentence reason grounded in the abstract.
Judgements are tied to a fingerprint of the research questions, so re-ranking only asks the LLM about
papers it hasn't judged for the current questions.
"""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.agents.idea.schemas import IdeaPlan
from app.agents.prompts.literature import RERANK
from app.core.logging import get_logger
from app.db.models.enums import StageName
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMService

log = get_logger(__name__)

AGENT_NAME = "literature.ranking"
BATCH_SIZE = 10
CONCURRENCY = 3
ABSTRACT_CHARS = 1500


class Judgement(BaseModel):
    paper: str = Field(description="The paper id, e.g. P3")
    score: int = Field(ge=0, le=10)
    rq_ids: list[str] = Field(default_factory=list)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=400)]


class RerankBatch(BaseModel):
    judgements: list[Judgement]


@dataclass(frozen=True)
class Candidate:
    key: str  # caller's id for the paper
    title: str
    abstract: str | None
    year: int | None
    venue: str | None


def rq_fingerprint(plan: IdeaPlan) -> str:
    """Identifies the research questions papers are judged against (text and ids)."""
    basis = "\n".join(f"{q.id}: {q.text}" for q in plan.research_questions)
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def paper_text(title: str, abstract: str | None) -> str:
    return f"{title}\n\n{(abstract or '')[:ABSTRACT_CHARS]}".strip()


async def rq_vectors(plan: IdeaPlan, embeddings: EmbeddingService) -> dict[str, list[float]]:
    return {q.id: await embeddings.embed_query(q.text) for q in plan.research_questions}


def similarities(vector: list[float], rqs: dict[str, list[float]]) -> dict[str, float]:
    """Cosine similarity per RQ (vectors are unit length, so a dot product)."""
    return {rq: round(sum(a * b for a, b in zip(vector, v, strict=True)), 4) for rq, v in rqs.items()}


def _describe(index: int, c: Candidate) -> str:
    meta = ", ".join(str(x) for x in (c.year, c.venue) if x)
    abstract = (c.abstract or "").strip()[:ABSTRACT_CHARS] or "(no abstract available)"
    return f"P{index} — {c.title}{f' ({meta})' if meta else ''}\nAbstract: {abstract}"


async def _judge_batch(
    batch: list[Candidate], plan: IdeaPlan, llm: LLMService, project_id: uuid.UUID
) -> dict[str, Judgement]:
    ids = {f"P{i}": c for i, c in enumerate(batch, start=1)}
    questions = "\n".join(f"{q.id} ({q.kind}): {q.text}" for q in plan.research_questions)
    objectives = "\n".join(f"{o.id}: {o.text}" for o in plan.objectives)
    papers = "\n\n".join(_describe(i, c) for i, c in enumerate(batch, start=1))
    prompt = RERANK.render(questions=questions, objectives=objectives, papers=papers)
    found: dict[str, Judgement] = {}
    for _ in range(2):  # one more try for any paper the model skipped
        result = await llm.generate_structured(
            prompt,
            RerankBatch,
            agent=AGENT_NAME,
            system=RERANK.system,
            fast=True,
            project_id=project_id,
            stage=StageName.LITERATURE,
            prompt_id=RERANK.id,
        )
        valid_rqs = {q.id for q in plan.research_questions}
        for j in result.data.judgements:
            pid = j.paper.strip().upper()
            if pid in ids and ids[pid].key not in found:
                j.rq_ids = [r for r in dict.fromkeys(x.strip().upper() for x in j.rq_ids) if r in valid_rqs]
                found[ids[pid].key] = j
        missing = [pid for pid, c in ids.items() if c.key not in found]
        if not missing:
            break
        prompt = RERANK.render(
            questions=questions,
            objectives=objectives,
            papers="\n\n".join(_describe(int(pid[1:]), ids[pid]) for pid in missing),
        )
    return found


async def rerank(
    candidates: list[Candidate], plan: IdeaPlan, llm: LLMService, project_id: uuid.UUID
) -> dict[str, Judgement]:
    """LLM judgements for the candidates, keyed by `Candidate.key` (papers it never judged are absent)."""
    batches = [candidates[i : i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    limit = asyncio.Semaphore(CONCURRENCY)

    async def run(batch: list[Candidate]) -> dict[str, Judgement]:
        async with limit:
            return await _judge_batch(batch, plan, llm, project_id)

    judged: dict[str, Judgement] = {}
    for part in await asyncio.gather(*(run(b) for b in batches)):
        judged.update(part)
    if len(judged) < len(candidates):
        log.warning("rerank_incomplete", judged=len(judged), candidates=len(candidates))
    return judged
