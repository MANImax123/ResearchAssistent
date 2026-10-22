"""Potential research gaps (Step 4.9): detection runs, listing data, the researcher's decisions.

A run refreshes the knowledge graph, gathers signals from the selected papers and replaces the
undecided candidates; accepted and rejected gaps stay, and the agent is told not to repeat them.
Accepted gaps are mirrored into the research state (`gap_ids`) and the graph (Objective-[:ADDRESSES]->Gap).
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.idea.agent import plan_from_state
from app.agents.literature import gaps as agent
from app.core.logging import get_logger
from app.db.models import Contradiction, Paper, PaperChunk, PaperProfile, Project, ResearchGap, ResearchStateSnapshot
from app.db.models.enums import GapConfidence, GapStatus
from app.orchestrator.service import accepted_gap_ids
from app.services import knowledge_graph
from app.services.llm import LLMService, SessionFactory
from app.services.neo4j import Neo4jClient

log = get_logger(__name__)

Progress = Callable[[int, str], Awaitable[None]]
MAX_LIMITATIONS = 30
MAX_FUTURE_WORK = 24
FUTURE_WORK_PER_PAPER = 3
RawSignal = tuple[agent.SignalKind, str, list[uuid.UUID], list[uuid.UUID]]


def _ids(values: Any) -> list[uuid.UUID]:
    out = []
    for v in values or []:
        try:
            out.append(uuid.UUID(str(v)))
        except ValueError:
            continue
    return out


def _label(paper: Paper) -> str:
    return f"“{paper.title}”{f' ({paper.year})' if paper.year else ''}"


async def _untested(project_id: uuid.UUID, client: Neo4jClient, papers: dict[uuid.UUID, Paper]) -> list[RawSignal]:
    """From the graph: methods never reported on a benchmark other selected papers share."""
    rows = await client.run(
        """MATCH (p:Paper {project_id: $pid})-[r:USES|EVALUATED_ON]->(e {project_id: $pid})
           RETURN p.key AS paper, type(r) AS rel, e.key AS key, e.name AS name, r.evidence AS evidence""",
        pid=str(project_id),
    )
    uses: dict[str, set[str]] = {}
    evaluated: dict[str, set[str]] = {}
    names: dict[tuple[str, str], str] = {}
    evidence: dict[tuple[str, str], list[uuid.UUID]] = {}
    for r in rows:
        target = uses if r["rel"] == "USES" else evaluated
        target.setdefault(r["key"], set()).add(r["paper"])
        names[(r["rel"], r["key"])] = r["name"]
        evidence.setdefault((r["rel"], r["key"]), []).extend(_ids(r["evidence"])[:1])
    signals: list[RawSignal] = []
    for method, datasets in agent.by_method(agent.untested_combinations(uses, evaluated, limit=30)):
        method_papers = [papers[u] for u in _ids(sorted(uses[method])) if u in papers]
        benchmarks = []
        others: dict[uuid.UUID, Paper] = {}
        chunks = evidence.get(("USES", method), [])[:1]
        for dataset in datasets:
            users = [papers[u] for u in _ids(sorted(evaluated[dataset])) if u in papers]
            others.update({p.id: p for p in users})
            benchmarks.append(f"{names[('EVALUATED_ON', dataset)]} ({len(users)} papers)")
            chunks += evidence.get(("EVALUATED_ON", dataset), [])[:1]
        text = (
            f"Method {names[('USES', method)]} (used in {'; '.join(_label(p) for p in method_papers[:3])}) is not "
            f"reported on {', '.join(benchmarks)}, which other selected papers use "
            f"({'; '.join(_label(p) for p in list(others.values())[:3])})."
        )
        ids = list(dict.fromkeys([p.id for p in method_papers] + list(others)))
        signals.append(("untested", text, ids, list(dict.fromkeys(chunks))))
    return signals


async def collect_signals(session: AsyncSession, project_id: uuid.UUID, client: Neo4jClient) -> list[agent.Signal]:
    papers = {
        p.id: p
        for p in await session.scalars(select(Paper).where(Paper.project_id == project_id, Paper.selected.is_(True)))
    }
    profiles = sorted(
        await session.scalars(select(PaperProfile).where(PaperProfile.paper_id.in_(list(papers)))),
        key=lambda pr: (papers[pr.paper_id].created_at, papers[pr.paper_id].title),
    )
    raw: list[RawSignal] = []

    for profile in profiles:
        paper = papers[profile.paper_id]
        evidence = (profile.evidence or {}).get("limitations") or []
        for i, text in enumerate(profile.limitations or []):
            ids = _ids(evidence[i]) if i < len(evidence) and isinstance(evidence[i], list) else []
            raw.append(("limitation", f"{_label(paper)}: {text}", [paper.id], ids))
    raw = raw[:MAX_LIMITATIONS]

    for c in await session.scalars(
        select(Contradiction).where(Contradiction.project_id == project_id, Contradiction.status == "open")
    ):
        if c.paper_a_id in papers and c.paper_b_id in papers:
            text = (
                f"On {c.topic or 'the same topic'}: {_label(papers[c.paper_a_id])} says “{c.claim_a}”, but "
                f"{_label(papers[c.paper_b_id])} says “{c.claim_b}”."
                + (f" Possible explanation: {c.explanation}" if c.explanation else "")
            )
            chunks = _ids((c.evidence or {}).get("a", []))[:2] + _ids((c.evidence or {}).get("b", []))[:2]
            raw.append(("contradiction", text, [c.paper_a_id, c.paper_b_id], chunks))

    raw += await _untested(project_id, client, papers)

    future: list[RawSignal] = []
    rows = await session.scalars(
        select(PaperChunk)
        .where(PaperChunk.paper_id.in_(list(papers)), PaperChunk.text.op("~*")(agent.FUTURE_WORK_SQL))
        .order_by(
            PaperChunk.section_kind.in_(["conclusion", "discussion", "limitations"]).desc(),
            PaperChunk.paper_id,
            PaperChunk.chunk_index,
        )
    )
    per_paper: dict[uuid.UUID, int] = {}
    for chunk in rows:
        for sentence in agent.future_work_sentences(chunk.text):
            if per_paper.get(chunk.paper_id, 0) >= FUTURE_WORK_PER_PAPER or len(future) >= MAX_FUTURE_WORK:
                break
            per_paper[chunk.paper_id] = per_paper.get(chunk.paper_id, 0) + 1
            future.append(("future_work", f"{_label(papers[chunk.paper_id])}: {sentence}", [chunk.paper_id],
                           [chunk.id]))  # fmt: skip
    return agent.number(raw + future)


def _signal_json(s: agent.Signal) -> dict[str, Any]:
    return {"id": s.id, "kind": s.kind, "text": s.text, "paper_ids": [str(p) for p in s.paper_ids],
            "chunk_ids": [str(c) for c in s.chunk_ids]}  # fmt: skip


async def detect(
    project_id: uuid.UUID,
    session_factory: SessionFactory,
    llm: LLMService,
    client: Neo4jClient,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    if progress is not None:
        await progress(10, "Refreshing the knowledge graph")
    await knowledge_graph.build(project_id, session_factory, client)
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise LookupError("Project not found")
        await session.refresh(project, ["research_state"])
        plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
        signals = await collect_signals(session, project_id, client)
        decided = [
            g.statement
            for g in await session.scalars(
                select(ResearchGap).where(
                    ResearchGap.project_id == project_id, ResearchGap.status != GapStatus.CANDIDATE
                )
            )
        ]
    kinds = {k: sum(1 for s in signals if s.kind == k) for k in agent.PREFIX}
    if not signals:
        return {"signals": kinds, "found": 0}
    if progress is not None:
        await progress(40, f"Looking for potential gaps in {len(signals)} signals")
    candidates = await agent.propose(signals, plan, decided, llm, project_id)

    async with session_factory() as session:
        await session.execute(
            delete(ResearchGap).where(ResearchGap.project_id == project_id, ResearchGap.status == GapStatus.CANDIDATE)
        )
        for c in candidates:
            session.add(
                ResearchGap(
                    project_id=project_id, statement=c.statement, rationale=c.rationale or None,
                    evidence={"signals": [_signal_json(s) for s in c.signals]},
                    confidence=GapConfidence(c.confidence), status=GapStatus.CANDIDATE,
                    objective_ids=c.objective_ids,
                )
            )  # fmt: skip
        await session.commit()
    log.info("gaps_detected", project_id=str(project_id), signals=kinds, found=len(candidates))
    return {"signals": kinds, "found": len(candidates)}


async def sync_state(session: AsyncSession, project: Project) -> None:
    """Mirror the accepted gaps into the research state now (graph runs refresh it too)."""
    await session.refresh(project, ["research_state"])
    ids = await accepted_gap_ids(session, project.id)
    if project.research_state is None:
        project.research_state = ResearchStateSnapshot(snapshot={"project_id": str(project.id), "gap_ids": ids})
        return
    if project.research_state.snapshot.get("gap_ids") != ids:
        project.research_state.snapshot = {**project.research_state.snapshot, "gap_ids": ids}
        project.research_state.version += 1


async def update(
    session: AsyncSession,
    project: Project,
    gap_id: uuid.UUID,
    changes: dict[str, Any],
    by: uuid.UUID,
) -> ResearchGap | None:
    """Edit the wording/confidence/objectives and/or decide (accepted, rejected, or back to candidate)."""
    gap = await session.scalar(
        select(ResearchGap).where(ResearchGap.id == gap_id, ResearchGap.project_id == project.id)
    )
    if gap is None:
        return None
    for name in ("statement", "rationale", "confidence", "objective_ids"):
        if name in changes and changes[name] != getattr(gap, name):
            setattr(gap, name, changes[name])
            gap.edited = True
    if "status" in changes and changes["status"] != gap.status:
        gap.status = changes["status"]
        gap.decided_by = None if gap.status == GapStatus.CANDIDATE else by
        gap.decided_at = None if gap.status == GapStatus.CANDIDATE else datetime.now(UTC)
    await session.flush()
    await sync_state(session, project)
    return gap
