"""The Literature Survey section and the literature analysis report (Step 4.10).

`load_inputs` feeds the Literature stage agent; `check_citations` verifies that every `[@key]` in the
section is a paper of the project with a verified identifier; `report` compiles a downloadable
Markdown report from the library, profiles, comparison, contradictions and gaps (no LLM involved).
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.idea.agent import plan_from_state
from app.agents.literature.survey import SurveyGap, SurveyInputs, SurveyPaper
from app.db.models import (
    Contradiction,
    ManuscriptTable,
    Paper,
    PaperProfile,
    Project,
    ResearchGap,
)
from app.db.models.enums import GapStatus, SectionType
from app.services import comparison
from app.services.llm import SessionFactory

CITATION = re.compile(r"\[@([A-Za-z0-9_:.\-/]+)\]")


def verified(paper: Paper) -> bool:
    """A verified external id: needed before the paper can be cited (A4.1)."""
    return any((paper.doi, paper.s2_id, paper.arxiv_id, paper.openalex_id))


def notes(paper: Paper, profile: PaperProfile | None) -> str:
    """What the writer may say about a paper: its analysis profile, or its abstract."""
    if profile is None:
        return f"Abstract: {paper.abstract or '(none)'}"
    lines = [
        f"Problem: {profile.problem}" if profile.problem else "",
        f"Method: {profile.method}" if profile.method else "",
        f"Datasets: {'; '.join(profile.dataset)}" if profile.dataset else "",
        f"Metrics: {'; '.join(profile.metrics)}" if profile.metrics else "",
        f"Results: {profile.results}" if profile.results else "",
        f"Limitations: {'; '.join(profile.limitations)}" if profile.limitations else "",
        f"Tools: {'; '.join(profile.tech_stack)}" if profile.tech_stack else "",
    ]
    return "\n".join(line for line in lines if line)


def _gap_papers(gap: ResearchGap) -> list[uuid.UUID]:
    ids = [p for s in (gap.evidence or {}).get("signals", []) for p in s.get("paper_ids", [])]
    return [uuid.UUID(p) for p in dict.fromkeys(ids)]


def table_block(table: ManuscriptTable, papers: dict[uuid.UUID, Paper]) -> str | None:
    """An inserted comparison table, rebuilt from its stored cells (citations only for verified papers)."""
    data = table.data or {}
    header, rows, ids = data.get("columns") or [], data.get("rows") or [], data.get("paper_ids") or []
    if not header or len(rows) != len(ids):
        return None
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for pid, row in zip(ids, rows, strict=True):
        paper = papers.get(uuid.UUID(pid))
        first = f"{row[0]} [@{pid}]" if paper is not None and verified(paper) else row[0]
        lines.append("| " + " | ".join([first, *row[1:]]) + " |")
    return f"**Table {table.number}.** {table.caption}\n\n" + "\n".join(lines)


async def load_inputs(
    session_factory: SessionFactory, project_id: uuid.UUID, selected: list[str], gap_ids: list[str]
) -> SurveyInputs:
    async with session_factory() as session:
        order = {pid: i for i, pid in enumerate(selected)}
        rows = list(
            await session.scalars(select(Paper).where(Paper.project_id == project_id, Paper.selected.is_(True)))
        )
        rows.sort(key=lambda p: (order.get(str(p.id), len(order)), p.title))
        papers = [p for p in rows if verified(p)]
        profiles = {
            p.paper_id: p
            for p in await session.scalars(
                select(PaperProfile).where(PaperProfile.paper_id.in_([p.id for p in papers]))
            )
        }
        accepted = list(
            await session.scalars(
                select(ResearchGap)
                .where(ResearchGap.project_id == project_id, ResearchGap.status == GapStatus.ACCEPTED)
                .order_by(ResearchGap.created_at)
            )
        )
        gaps = accepted or list(
            await session.scalars(
                select(ResearchGap)
                .where(ResearchGap.project_id == project_id, ResearchGap.status == GapStatus.CANDIDATE)
                .order_by(ResearchGap.created_at)
            )
        )
        tables = list(
            await session.scalars(
                select(ManuscriptTable)
                .where(
                    ManuscriptTable.project_id == project_id,
                    ManuscriptTable.section_type == SectionType.LITERATURE_SURVEY,
                )
                .order_by(ManuscriptTable.number)
            )
        )
    by_id = {p.id: p for p in rows}
    return SurveyInputs(
        papers=[SurveyPaper(p.id, p.title, p.year, notes(p, profiles.get(p.id))) for p in papers],
        gaps=[SurveyGap(g.statement, _gap_papers(g)) for g in gaps],
        gaps_accepted=bool(accepted),
        tables=[block for t in tables if (block := table_block(t, by_id))],
    )


async def check_citations(session: AsyncSession, project_id: uuid.UUID, text: str) -> dict[str, Any]:
    """Every [@key] must be a paper of this project with a verified identifier."""
    keys = list(dict.fromkeys(CITATION.findall(text)))
    ids = {}
    for key in keys:
        try:
            ids[key] = uuid.UUID(key)
        except ValueError:
            continue
    papers = (
        {p.id: p for p in await session.scalars(select(Paper).where(Paper.project_id == project_id,
                                                                    Paper.id.in_(list(ids.values()))))}
        if ids else {}
    )  # fmt: skip
    cited, unresolved = [], []
    for key in keys:
        paper = papers.get(ids[key]) if key in ids else None
        if paper is None or not verified(paper):
            unresolved.append(key)
        else:
            cited.append(paper)
    paragraphs = [
        p for p in re.split(r"\n\s*\n", text) if p.strip() and not p.lstrip().startswith(("#", "|", "**Table"))
    ]
    uncited = sum(1 for p in paragraphs if not CITATION.search(p))
    return {"cited": cited, "unresolved": unresolved, "uncited_paragraphs": uncited, "paragraphs": len(paragraphs)}


def authors(paper: Paper, n: int = 3) -> str:
    names = [a.get("name", "") for a in (paper.authors or []) if isinstance(a, dict)]
    return ", ".join(names[:n]) + (" et al." if len(names) > n else "")


def _cell(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


async def report(session: AsyncSession, project_id: uuid.UUID) -> str:
    """The literature analysis report: library, per-paper analysis, comparison, conflicts, gaps (Markdown)."""
    project = await session.get(Project, project_id)
    assert project is not None
    await session.refresh(project, ["research_state"])
    plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
    all_papers = list(await session.scalars(select(Paper).where(Paper.project_id == project_id)))
    selected = [p for p in all_papers if p.selected]
    selected.sort(key=lambda p: (not p.must_include, -(p.relevance_score or 0), p.title))
    profiles = {
        p.paper_id: p
        for p in await session.scalars(select(PaperProfile).where(PaperProfile.paper_id.in_([p.id for p in selected])))
    }
    conflicts = list(
        await session.scalars(
            select(Contradiction).where(Contradiction.project_id == project_id, Contradiction.status == "open")
        )
    )
    gaps = list(
        await session.scalars(
            select(ResearchGap)
            .where(ResearchGap.project_id == project_id, ResearchGap.status != GapStatus.REJECTED)
            .order_by(ResearchGap.status, ResearchGap.created_at)
        )
    )
    names = {p.id: p.title for p in all_papers}
    years = [p.year for p in selected if p.year]

    out = [
        f"# Literature analysis report — {project.title}",
        "",
        f"_Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by ResearchFlow._",
        "",
        "## Overview",
        "",
        f"- Papers in the library: {len(all_papers)}; selected for the survey: {len(selected)}",
        f"- Selected papers analysed in full text: {len(profiles)}",
        f"- Publication years: {min(years)}–{max(years)}" if years else "- Publication years: unknown",
        f"- Open contradictions: {len(conflicts)}; potential gaps: "
        f"{sum(g.status == GapStatus.ACCEPTED for g in gaps)} accepted, "
        f"{sum(g.status == GapStatus.CANDIDATE for g in gaps)} to review",
    ]
    if plan is not None:
        out += ["", "## Research plan", "", *[f"- **{o.id}.** {o.text}" for o in plan.objectives], ""]
        out += [f"- **{q.id}.** {q.text}" for q in plan.research_questions]

    out += ["", "## Selected papers", "", "| # | Paper | Year | Venue | Identifier |", "|---|---|---|---|---|"]
    for i, p in enumerate(selected, start=1):
        ident = f"DOI {p.doi}" if p.doi else f"arXiv {p.arxiv_id}" if p.arxiv_id else (p.s2_id or p.openalex_id or "—")
        out.append(f"| {i} | {_cell(p.title)} | {p.year or ''} | {_cell(p.venue)} | {_cell(ident)} |")

    out += ["", "## Paper analyses", ""]
    for p in selected:
        profile = profiles.get(p.id)
        out += [f"### {p.title}{f' ({p.year})' if p.year else ''}", ""]
        if authors(p):
            out += [f"_{authors(p)}_", ""]
        out += [notes(p, profile) if profile else "_Not analysed (no full text)._", ""]

    matrix = (await comparison.load(session, project_id)).matrix
    if matrix.get("rows"):
        header, body = comparison.table(matrix, selected)
        out += ["## Comparison", "", "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in body]
        out.append("")

    out += ["## Contradictions", ""]
    out += [
        f"- **{c.topic or 'Conflicting claims'}** — {names.get(c.paper_a_id, '?')}: “{c.claim_a}” vs "
        f"{names.get(c.paper_b_id, '?')}: “{c.claim_b}”"
        + (f" _Possible explanation: {c.explanation}_" if c.explanation else "")
        for c in conflicts
    ] or ["_None open._"]

    out += ["", "## Potential research gaps", ""]
    for g in gaps:
        label = "Accepted" if g.status == GapStatus.ACCEPTED else "To review"
        out.append(f"- **{label} · {g.confidence.value} confidence.** {g.statement}")
        if g.rationale:
            out.append(f"  - {g.rationale}")
        based = [names[p] for p in _gap_papers(g) if p in names]
        if based:
            out.append(f"  - Based on: {'; '.join(based)}")
    if not gaps:
        out.append("_None yet._")
    return "\n".join(out) + "\n"
