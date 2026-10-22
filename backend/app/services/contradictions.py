"""Contradictions between the selected papers (Step 4.7): detection runs, listing, dismissals.

A run replaces the open contradictions; dismissed ones stay, and a new finding for the same pair of
papers on the same topic as a dismissed one isn't reported again.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.literature import contradictions as agent
from app.core.logging import get_logger
from app.db.models import Contradiction, Paper, PaperChunk, PaperProfile
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMService, SessionFactory
from app.services.retrieval import retrieve

log = get_logger(__name__)

Progress = Callable[[int, str], Awaitable[None]]
PASSAGES_PER_PAPER = 6


def short_name(title: str) -> str | None:
    """The name a paper's method goes by ("GraphDTA" from "GraphDTA: Predicting …"), if its title has one."""
    head = title.split(":", 1)[0].strip()
    if head == title.strip() or not 3 <= len(head) <= 25 or len(head.split()) > 2:
        return None
    return head


def excerpt(text: str, name: str, width: int = 900) -> str:
    """The part of a long passage around the first mention of `name` (tables can be long; the row matters)."""
    at = text.lower().find(name.lower())
    if at < 0 or len(text) <= 2 * width:
        return text
    start, end = max(at - width, 0), min(at + width, len(text))
    return f"{'…' if start else ''}{text[start:end]}{'…' if end < len(text) else ''}"


async def cross_references(
    session: AsyncSession, paper_id: uuid.UUID, other_titles: list[str]
) -> list[tuple[PaperChunk, str]]:
    """Passages of this paper that name another paper's method: where re-run baselines and opposite claims live.
    Tables first, then results/data sections, at most 2 per named method. Returns (passage, name mentioned)."""
    found: list[tuple[PaperChunk, str]] = []
    for name in filter(None, (short_name(t) for t in other_titles)):
        rows = await session.scalars(
            select(PaperChunk)
            .where(PaperChunk.paper_id == paper_id, PaperChunk.text.ilike(f"%{name}%"))
            .order_by(
                PaperChunk.section_name.like("Table %").desc(),  # tables hold the comparable numbers
                PaperChunk.section_kind.in_(["results", "dataset", "discussion"]).desc(),
                PaperChunk.chunk_index,
            )
            .limit(2)
        )
        found += [(c, name) for c in rows if all(c.id != f.id for f, _ in found)]
    return found


def _fingerprint(a: uuid.UUID, b: uuid.UUID, topic: str | None) -> tuple[frozenset[str], str]:
    return frozenset({str(a), str(b)}), agent.normalize(topic or "")


def _flat(evidence: Any) -> list[str]:
    if not evidence:
        return []
    return [i for item in evidence for i in item] if isinstance(evidence[0], list) else list(evidence)


async def detect(
    project_id: uuid.UUID,
    session_factory: SessionFactory,
    llm: LLMService,
    embedder: EmbeddingService,
    *,
    progress: Progress | None = None,
) -> dict[str, int]:
    async with session_factory() as session:
        papers = {
            p.id: p
            for p in await session.scalars(
                select(Paper).where(Paper.project_id == project_id, Paper.selected.is_(True))
            )
        }
        profiles = {
            p.paper_id: p
            for p in await session.scalars(select(PaperProfile).where(PaperProfile.paper_id.in_(list(papers))))
        }
        grouped = agent.groups({pid: (p.dataset or [], p.metrics or []) for pid, p in profiles.items()})
        inputs: list[tuple[agent.Group, list[agent.GroupPaper]]] = []
        for group in grouped:
            try:
                query: list[float] | None = await embedder.embed_query(group.topic.split(": ", 1)[-1])
            except Exception as exc:  # noqa: BLE001 - e.g. embedding quota used up: compare without similarity search
                log.warning("contradiction_topic_embedding_failed", error=str(exc)[:200])
                query = None
            members = []
            for pid in group.paper_ids:
                profile = profiles[pid]
                ids = [uuid.UUID(i) for field in ("results", "metrics", "dataset")
                       for i in _flat((profile.evidence or {}).get(field))]  # fmt: skip
                chunks = {c.id: c for c in await session.scalars(select(PaperChunk).where(PaperChunk.id.in_(ids)))}
                passages = [agent.Passage(c.id, c.section_name, c.page, c.text) for c in chunks.values()][:3]
                others = [papers[o].title for o in group.paper_ids if o != pid]
                for c, name in await cross_references(session, pid, others):
                    if c.id not in chunks:
                        chunks[c.id] = c
                        passages.append(agent.Passage(c.id, c.section_name, c.page, excerpt(c.text, name)))
                if query is not None:
                    for r in await retrieve(session, project_id, query, k=2, fetch_k=8, paper_ids=[pid]):
                        if r.chunk_id not in chunks:
                            passages.append(agent.Passage(r.chunk_id, r.section, r.page, r.text))
                members.append(
                    agent.GroupPaper(pid, papers[pid].title, papers[pid].year, passages[:PASSAGES_PER_PAPER])
                )
            inputs.append((group, members))
        dismissed = {
            _fingerprint(c.paper_a_id, c.paper_b_id, c.topic)
            for c in await session.scalars(
                select(Contradiction).where(Contradiction.project_id == project_id, Contradiction.status == "dismissed")
            )
        }

    found: list[agent.Found] = []
    for i, (group, members) in enumerate(inputs, start=1):
        if progress is not None:
            await progress(10 + 80 * (i - 1) // max(len(inputs), 1), f"Comparing claims on {group.topic}")
        found += await agent.detect(group.topic, members, llm, project_id)

    seen: set[tuple[frozenset[str], str, str]] = set()
    kept = []
    for f in found:
        fingerprint = _fingerprint(f.paper_a, f.paper_b, f.topic)
        key = (*fingerprint, f.claim_a[:80].lower())
        if fingerprint in dismissed or key in seen:
            continue
        seen.add(key)
        kept.append(f)

    async with session_factory() as session:
        await session.execute(
            delete(Contradiction).where(Contradiction.project_id == project_id, Contradiction.status == "open")
        )
        for f in kept:
            session.add(
                Contradiction(
                    project_id=project_id, paper_a_id=f.paper_a, paper_b_id=f.paper_b, claim_a=f.claim_a,
                    claim_b=f.claim_b, evidence={"a": f.evidence_a, "b": f.evidence_b}, explanation=f.explanation,
                    explanation_kind=f.explanation_kind, topic=f.topic, status="open",
                )
            )  # fmt: skip
        await session.commit()
    log.info("contradictions_detected", project_id=str(project_id), groups=len(inputs), found=len(found),
             kept=len(kept))  # fmt: skip
    return {"groups": len(inputs), "papers": len(profiles), "found": len(kept),
            "suppressed": len(found) - len(kept)}  # fmt: skip


async def set_status(
    session: AsyncSession, project_id: uuid.UUID, contradiction_id: uuid.UUID, *, dismissed: bool,
    by: uuid.UUID, reason: str | None,
) -> Contradiction | None:  # fmt: skip
    row = await session.scalar(
        select(Contradiction).where(Contradiction.id == contradiction_id, Contradiction.project_id == project_id)
    )
    if row is None:
        return None
    row.status = "dismissed" if dismissed else "open"
    row.dismissed_by = by if dismissed else None
    row.dismissed_at = datetime.now(UTC) if dismissed else None
    row.dismiss_reason = (reason or None) if dismissed else None
    await session.flush()
    return row
