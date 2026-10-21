"""Semantic search over a project's parsed papers (Step 4.4, L5).

Chunks are embedded once (`paper_chunks.embedding`, HNSW cosine index). Retrieval always filters by
project, can be limited to section kinds (e.g. only "dataset" passages) or to given papers, takes the
`fetch_k` nearest chunks and re-selects `k` of them with Maximal Marginal Relevance, so the answer
sees several papers and sections instead of five near-identical passages from one.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Paper, PaperChunk
from app.services.embeddings import EmbeddingService
from app.services.llm import SessionFactory

log = get_logger(__name__)

EMBED_BATCH = 25  # chunks per request (Gemini's free tier counts texts per minute)
MMR_LAMBDA = 0.6  # 1 = pure relevance, 0 = pure diversity

Progress = Callable[[int, str], Awaitable[None]]  # (percent of this step, message)


@dataclass
class Retrieved:
    chunk_id: uuid.UUID
    paper_id: uuid.UUID
    paper_title: str
    year: int | None
    section: str | None
    kind: str | None
    page: int | None
    text: str
    similarity: float
    pdf_file_id: uuid.UUID | None = None


async def chunk_counts(session: AsyncSession, project_id: uuid.UUID) -> tuple[int, int]:
    """(indexed, waiting for an embedding) chunks in the project."""
    row = (
        await session.execute(
            select(
                func.count().filter(PaperChunk.embedding.is_not(None)),
                func.count().filter(PaperChunk.embedding.is_(None)),
            )
            .join(Paper, Paper.id == PaperChunk.paper_id)
            .where(Paper.project_id == project_id)
        )
    ).one()
    return int(row[0]), int(row[1])


async def embed_pending_chunks(
    project_id: uuid.UUID,
    session_factory: SessionFactory,
    embedder: EmbeddingService,
    progress: Progress | None = None,
) -> int:
    """Embed every chunk of the project that has no embedding yet; each batch is saved as it completes."""
    async with session_factory() as session:
        pending = list(
            (
                await session.execute(
                    select(PaperChunk.id, PaperChunk.text, PaperChunk.section_name, Paper.title)
                    .join(Paper, Paper.id == PaperChunk.paper_id)
                    .where(Paper.project_id == project_id, PaperChunk.embedding.is_(None))
                    .order_by(PaperChunk.paper_id, PaperChunk.chunk_index)
                )
            ).all()
        )
    for start in range(0, len(pending), EMBED_BATCH):
        if progress is not None:
            await progress(100 * start // len(pending), f"Indexing passages for search ({start}/{len(pending)})")
        batch = pending[start : start + EMBED_BATCH]
        # The title gives a passage its context ("which paper is this about?")
        vectors = await embedder.embed_documents(
            [row.text for row in batch], titles=[f"{row.title} — {row.section_name or ''}" for row in batch]
        )
        async with session_factory() as session:
            for row, vector in zip(batch, vectors, strict=True):
                chunk = await session.get(PaperChunk, row.id)
                if chunk is not None:
                    chunk.embedding = vector
            await session.commit()
    log.info("chunks_embedded", project_id=str(project_id), count=len(pending))
    return len(pending)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def mmr(query: list[float], candidates: list[tuple[list[float], Retrieved]], k: int, lam: float) -> list[Retrieved]:
    """Maximal Marginal Relevance over unit vectors: relevance to the query minus similarity to what's chosen."""
    chosen: list[tuple[list[float], Retrieved]] = []
    remaining = list(candidates)
    while remaining and len(chosen) < k:

        def score(item: tuple[list[float], Retrieved]) -> float:
            redundancy = max((_dot(item[0], c[0]) for c in chosen), default=0.0)
            return lam * _dot(query, item[0]) - (1 - lam) * redundancy

        best = max(remaining, key=score)
        chosen.append(best)
        remaining.remove(best)
    return [item[1] for item in chosen]


async def retrieve(
    session: AsyncSession,
    project_id: uuid.UUID,
    query: list[float],
    *,
    k: int = 8,
    fetch_k: int = 40,
    section_kinds: Sequence[str] | None = None,
    paper_ids: list[uuid.UUID] | None = None,
    lam: float = MMR_LAMBDA,
) -> list[Retrieved]:
    """The k most useful chunks for the query vector, only from this project's papers."""
    distance = PaperChunk.embedding.cosine_distance(query)
    stmt = (
        select(PaperChunk, Paper.title, Paper.year, Paper.pdf_file_id, distance.label("distance"))
        .join(Paper, Paper.id == PaperChunk.paper_id)
        .where(Paper.project_id == project_id, PaperChunk.embedding.is_not(None))
    )
    if section_kinds:
        stmt = stmt.where(PaperChunk.section_kind.in_(section_kinds))
    if paper_ids:
        stmt = stmt.where(PaperChunk.paper_id.in_(paper_ids))
    rows = (await session.execute(stmt.order_by(distance).limit(fetch_k))).all()
    candidates = [
        (
            list(chunk.embedding),
            Retrieved(
                chunk.id,
                chunk.paper_id,
                title,
                year,
                chunk.section_name,
                chunk.section_kind,
                chunk.page,
                chunk.text,
                round(1 - float(dist), 4),
                pdf_file_id,
            ),
        )  # fmt: skip
        for chunk, title, year, pdf_file_id, dist in rows
    ]
    return mmr(query, candidates, k, lam)
