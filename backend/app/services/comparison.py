"""The literature comparison matrix (Step 4.6): storage, building, edits, CSV and Markdown.

One `comparisons` row per project holds the matrix:
    {"columns": [{"key", "label", "builtin"}],
     "rows": {paper_id: {column_key: {"value", "source", "evidence": [chunk ids]}}},
     "generated_at": iso}
`source` is "profile" / "abstract" / "ai" (written by the agent) or "user" (typed by the researcher).
Paper and Year come live from the paper record. Rows shown are the currently selected papers, so the
matrix always covers the selection; the researcher's cells are never overwritten by a rebuild.
"""

import csv
import io
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.literature import comparison as agent
from app.core.logging import get_logger
from app.db.models import Comparison, Paper, PaperChunk, PaperProfile
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMService, SessionFactory
from app.services.retrieval import retrieve

log = get_logger(__name__)

Progress = Callable[[int, str], Awaitable[None]]
DEFAULT_COLUMNS = [{"key": key, "label": agent.LABELS[key], "builtin": True} for key in agent.BUILTIN]
MAX_COLUMNS = 20


class ComparisonError(ValueError):
    pass


async def load(session: AsyncSession, project_id: uuid.UUID) -> Comparison:
    row = await session.scalar(
        select(Comparison).where(Comparison.project_id == project_id).order_by(Comparison.created_at.desc()).limit(1)
    )
    if row is None:
        row = Comparison(project_id=project_id, matrix={"columns": DEFAULT_COLUMNS, "rows": {}}, version=1)
        session.add(row)
        await session.flush()
    return row


def _save(row: Comparison, matrix: dict[str, Any]) -> None:
    row.matrix = matrix  # reassign so SQLAlchemy sees the JSON change
    row.version += 1


async def selected_papers(session: AsyncSession, project_id: uuid.UUID) -> list[Paper]:
    rows = await session.scalars(
        select(Paper)
        .where(Paper.project_id == project_id, Paper.selected.is_(True))
        .order_by(Paper.must_include.desc(), Paper.relevance_score.desc().nulls_last(), Paper.title)
    )
    return list(rows)


# ─── Building (the agent's cells) ────────────────────────────────────────


async def build(
    project_id: uuid.UUID,
    session_factory: SessionFactory,
    llm: LLMService,
    *,
    force: bool = False,
    progress: Progress | None = None,
) -> dict[str, int]:
    """Fill the built-in columns for selected papers that don't have them yet (all of them with `force`).
    The researcher's own cells are kept either way."""
    async with session_factory() as session:
        matrix = dict((await load(session, project_id)).matrix)
        papers = await selected_papers(session, project_id)
        profiles = {
            p.paper_id: p
            for p in await session.scalars(
                select(PaperProfile).where(PaperProfile.paper_id.in_([x.id for x in papers]))
            )
        }
        await session.commit()
    rows: dict[str, Any] = dict(matrix.get("rows", {}))
    todo = [p for p in papers if force or not any(c in rows.get(str(p.id), {}) for c in agent.BUILTIN)]
    facts = []
    for paper in todo:
        profile = profiles.get(paper.id)
        values = (
            {f: getattr(profile, f) for f in ("problem", "method", "results", "dataset", "metrics", "limitations")}
            if profile
            else None
        )
        facts.append(agent.PaperFacts(paper.id, paper.title, paper.year, values,
                                      (profile.evidence or {}) if profile else {}, paper.abstract))  # fmt: skip
    if progress is not None:
        await progress(20, f"Comparing {len(facts)} papers")
    built = await agent.build_rows(facts, llm, project_id) if facts else {}

    async with session_factory() as session:
        row = await load(session, project_id)
        matrix = dict(row.matrix)
        rows = dict(matrix.get("rows", {}))
        for paper_id, cells in built.items():
            current = dict(rows.get(str(paper_id), {}))
            for key in agent.BUILTIN:
                if current.get(key, {}).get("source") == "user":
                    continue  # never overwrite the researcher
                if key in cells:
                    c = cells[key]
                    current[key] = {"value": c.value, "source": c.source, "evidence": c.evidence}
                else:
                    current.pop(key, None)
            rows[str(paper_id)] = current
        matrix["rows"] = rows
        matrix["generated_at"] = datetime.now(UTC).isoformat()
        _save(row, matrix)
        await session.commit()
    log.info("comparison_built", project_id=str(project_id), papers=len(built))
    return {"papers": len(papers), "compared": len(built)}


async def fill_column(
    project_id: uuid.UUID,
    key: str,
    session_factory: SessionFactory,
    llm: LLMService,
    embedder: EmbeddingService,
    *,
    progress: Progress | None = None,
) -> dict[str, int]:
    """Answer a custom column for every selected paper with full text, from that paper's passages."""
    async with session_factory() as session:
        matrix = dict((await load(session, project_id)).matrix)
        column = next((c for c in matrix["columns"] if c["key"] == key and not c.get("builtin")), None)
        if column is None:
            raise ComparisonError("No such custom column")
        papers = await selected_papers(session, project_id)
        query = await embedder.embed_query(column["label"])
        inputs = []
        for paper in papers:
            found = await retrieve(session, project_id, query, k=4, fetch_k=12, paper_ids=[paper.id])
            if found:
                passages = [agent.ColumnPassage(r.chunk_id, r.section, r.page, r.text) for r in found]
                inputs.append((paper.id, paper.title, passages))
        await session.commit()
    if progress is not None:
        await progress(30, f"Filling “{column['label']}” for {len(inputs)} papers")
    filled = await agent.fill_column(column["label"], inputs, llm, project_id) if inputs else {}

    async with session_factory() as session:
        row = await load(session, project_id)
        matrix = dict(row.matrix)
        rows = dict(matrix.get("rows", {}))
        for paper_id, cell in filled.items():
            current = dict(rows.get(str(paper_id), {}))
            if current.get(key, {}).get("source") != "user":
                current[key] = {"value": cell.value, "source": "ai", "evidence": cell.evidence}
            rows[str(paper_id)] = current
        matrix["rows"] = rows
        _save(row, matrix)
        await session.commit()
    return {"papers": len(papers), "searched": len(inputs), "filled": len(filled)}


# ─── Edits ───────────────────────────────────────────────────────────────


def _slug(label: str, existing: set[str]) -> str:
    base = "c_" + "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_")[:30]
    key, n = base, 2
    while key in existing:
        key, n = f"{base}_{n}", n + 1
    return key


async def add_column(session: AsyncSession, project_id: uuid.UUID, label: str) -> Comparison:
    row = await load(session, project_id)
    matrix = dict(row.matrix)
    columns = list(matrix["columns"])
    if len(columns) >= MAX_COLUMNS:
        raise ComparisonError(f"At most {MAX_COLUMNS} columns")
    if any(c["label"].lower() == label.lower() for c in columns):
        raise ComparisonError("A column with that name exists")
    columns.append({"key": _slug(label, {c["key"] for c in columns}), "label": label, "builtin": False})
    matrix["columns"] = columns
    _save(row, matrix)
    return row


async def update_column(session: AsyncSession, project_id: uuid.UUID, key: str, label: str | None,
                        remove: bool) -> Comparison:  # fmt: skip
    row = await load(session, project_id)
    matrix = dict(row.matrix)
    columns = list(matrix["columns"])
    column = next((c for c in columns if c["key"] == key), None)
    if column is None:
        raise ComparisonError("No such column")
    if remove:
        if column.get("builtin"):
            raise ComparisonError("Built-in columns can be hidden in the table, not deleted")
        columns.remove(column)
        matrix["rows"] = {pid: {k: v for k, v in cells.items() if k != key} for pid, cells in matrix["rows"].items()}
    elif label:
        columns[columns.index(column)] = {**column, "label": label}
    matrix["columns"] = columns
    _save(row, matrix)
    return row


async def set_cell(
    session: AsyncSession, project_id: uuid.UUID, paper_id: uuid.UUID, key: str, value: str
) -> Comparison:
    """The researcher's value for one cell (kept by every rebuild). Clearing a cell removes it."""
    row = await load(session, project_id)
    matrix = dict(row.matrix)
    if key not in {c["key"] for c in matrix["columns"]}:
        raise ComparisonError("No such column")
    rows = dict(matrix.get("rows", {}))
    cells = dict(rows.get(str(paper_id), {}))
    if value.strip():
        previous = cells.get(key, {})
        keep = previous.get("evidence", []) if previous.get("value") == value.strip() else []
        cells[key] = {"value": value.strip(), "source": "user", "evidence": keep}
    else:
        cells.pop(key, None)
    rows[str(paper_id)] = cells
    matrix["rows"] = rows
    _save(row, matrix)
    return row


# ─── Views & exports ─────────────────────────────────────────────────────


def table(
    matrix: dict[str, Any], papers: list[Paper], keys: list[str] | None = None
) -> tuple[list[str], list[list[str]]]:
    """Header and plain-text rows (Paper, Year, then the chosen columns) for CSV / Markdown."""
    columns = [c for c in matrix["columns"] if keys is None or c["key"] in keys]
    header = ["Paper", "Year", *[c["label"] for c in columns]]
    body = []
    for paper in papers:
        cells = matrix.get("rows", {}).get(str(paper.id), {})
        body.append([paper.title, str(paper.year or ""), *[cells.get(c["key"], {}).get("value", "") for c in columns]])
    return header, body


def to_csv(header: list[str], body: list[list[str]]) -> str:
    """UTF-8 with BOM and CRLF, every field quoted when needed: opens correctly in Excel and LibreOffice."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(body)
    return "﻿" + buffer.getvalue()


def _md(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def to_markdown(header: list[str], body: list[list[str]], papers: list[Paper]) -> str:
    """GFM table for the Literature Survey section; each paper cites itself with its [@paper_id] key."""
    lines = ["| " + " | ".join(_md(h) for h in header) + " |", "|" + "---|" * len(header)]
    for paper, row in zip(papers, body, strict=True):
        cells = [f"{_md(row[0])} [@{paper.id}]", *(_md(v) for v in row[1:])]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


async def chunk_lookup(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, PaperChunk]:
    if not ids:
        return {}
    return {c.id: c for c in await session.scalars(select(PaperChunk).where(PaperChunk.id.in_(ids)))}
