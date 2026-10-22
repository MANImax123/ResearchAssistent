"""L9 Knowledge Graph (Step 4.8): the project's literature as a Neo4j graph (B13).

Built from Postgres (the source of truth) and rebuilt on demand: papers (the selection), the methods,
datasets and metrics their profiles name, their limitations, contradictions between them, the
project's objectives and research questions, and the gaps the researcher accepted (Step 4.9).
Entities are normalised before merging, so "BERT", "bert-base" and "BERT (uncased)" are one Method,
and "Root mean square error (RMSE)" is the Metric "RMSE".
"""

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select

from app.agents.idea.agent import plan_from_state
from app.core.logging import get_logger
from app.db.models import Contradiction, Paper, PaperProfile, Project, ResearchGap
from app.db.models.enums import GapStatus
from app.services.contradictions import short_name
from app.services.llm import SessionFactory
from app.services.neo4j import Neo4jClient

log = get_logger(__name__)

EntityKind = Literal["Method", "Dataset", "Metric"]

_ABBREVIATION = re.compile(r"\(([^()]{2,15})\)\s*$")
_VERSION = re.compile(r"^v?\d+(\.\d+)*[a-z]?$")
_DROP: dict[str, set[str]] = {
    # model size/casing/version words don't make a different method
    "Method": {"base", "large", "small", "tiny", "uncased", "cased", "variant", "the", "a", "an"},
    "Dataset": {"the", "dataset", "datasets", "data", "set", "sets", "benchmark", "benchmarks", "database"},
    "Metric": {"the", "score", "metric"},
}


def _singular(word: str) -> str:
    """networks → network; but not analysis, corpus, class, atlas."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "is", "us", "as")) else word


def canonical(kind: EntityKind, name: str) -> str:
    """A merge key: case, punctuation, plurals, filler words and (for methods) version numbers ignored;
    a trailing abbreviation in parentheses stands for the whole name."""
    text = name.strip()
    if (match := _ABBREVIATION.search(text)) and re.search(r"[A-Za-z]", match.group(1)):
        inside = match.group(1).strip()
        # "Root mean square error (RMSE)" → RMSE; but "BERT (uncased)" keeps BERT
        text = inside if inside.upper() == inside or kind == "Metric" else _ABBREVIATION.sub("", text)
    text = re.sub(r"(?<=[a-z])'s\b", "", text.lower())
    tokens = [t for t in re.split(r"[^a-z0-9.]+", text.replace("v.", "v")) if t]
    tokens = [t.strip(".") for t in tokens if t.strip(".") and t not in _DROP[kind]]
    if kind != "Dataset":  # dataset names are proper nouns ("Davis"): never singularise them
        tokens = [_singular(t) for t in tokens]
    if kind == "Method":
        tokens = [t for t in tokens if not _VERSION.fullmatch(t)] or tokens
    else:
        tokens = [t[1:] if re.fullmatch(r"v\d+", t) else t for t in tokens]
    return " ".join(tokens)


@dataclass
class Entities:
    """Surface forms seen per (kind, key); the display name is the most common (then shortest) form."""

    forms: dict[tuple[str, str], Counter[str]] = field(default_factory=lambda: defaultdict(Counter))

    def add(self, kind: EntityKind, name: str) -> str | None:
        key = canonical(kind, name)
        if not key:
            return None
        self.forms[(kind, key)][name.strip().removesuffix("%").strip()] += 1  # "F1%" (a column header) → F1
        return key

    def display(self, kind: str, key: str) -> str:
        forms = self.forms[(kind, key)]
        return sorted(forms.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]


def _evidence(profile: PaperProfile, field_name: str, index: int) -> list[str]:
    items = (profile.evidence or {}).get(field_name) or []
    return list(items[index]) if index < len(items) and isinstance(items[index], list) else []


async def build(project_id: uuid.UUID, session_factory: SessionFactory, client: Neo4jClient) -> dict[str, int]:
    """Replace the project's graph with one built from its current selection, profiles and plan."""
    pid = str(project_id)
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise LookupError("Project not found")
        await session.refresh(project, ["research_state"])
        plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
        papers = list(
            await session.scalars(select(Paper).where(Paper.project_id == project_id, Paper.selected.is_(True)))
        )
        ids = [p.id for p in papers]
        profiles = {
            p.paper_id: p for p in await session.scalars(select(PaperProfile).where(PaperProfile.paper_id.in_(ids)))
        }
        conflicts = list(
            await session.scalars(
                select(Contradiction).where(Contradiction.project_id == project_id, Contradiction.status == "open")
            )
        )

    entities = Entities()
    links: list[dict[str, Any]] = []  # paper → entity
    limitations: list[dict[str, Any]] = []
    for paper in papers:
        profile = profiles.get(paper.id)
        own = short_name(paper.title)
        if own and (key := entities.add("Method", own)):
            links.append({"paper": str(paper.id), "kind": "Method", "key": key, "rel": "USES", "proposed": True,
                          "evidence": []})  # fmt: skip
        if profile is None:
            continue
        for kind, field_name, rel in (("Method", "tech_stack", "USES"), ("Dataset", "dataset", "EVALUATED_ON"),
                                      ("Metric", "metrics", "REPORTS")):  # fmt: skip
            for i, name in enumerate(getattr(profile, field_name) or []):
                if key := entities.add(kind, name):  # type: ignore[arg-type]
                    links.append({"paper": str(paper.id), "kind": kind, "key": key, "rel": rel, "proposed": False,
                                  "evidence": _evidence(profile, field_name, i)})  # fmt: skip
        for i, text in enumerate(profile.limitations or []):
            limitations.append({"paper": str(paper.id), "key": f"{paper.id}:{i}", "text": text,
                                "evidence": _evidence(profile, "limitations", i)})  # fmt: skip

    # Drop duplicate paper→entity links (two surface forms of one method in the same paper)
    unique = {(link["paper"], link["kind"], link["key"], link["rel"]): link for link in links}
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for kind, key in entities.forms:
        by_kind[kind].append({"key": key, "name": entities.display(kind, key)})

    await client.run("MATCH (n {project_id: $pid}) DETACH DELETE n", pid=pid)
    await client.run("CREATE (:Project {project_id: $pid, key: $pid, name: $name})", pid=pid, name=project.title)
    await client.run(
        """UNWIND $papers AS p
           CREATE (:Paper {project_id: $pid, key: p.id, name: p.title, year: p.year, venue: p.venue,
                           relevance: p.relevance, has_profile: p.has_profile})""",
        pid=pid,
        papers=[
            {
                "id": str(p.id),
                "title": p.title,
                "year": p.year,
                "venue": p.venue,
                "relevance": p.relevance_score,
                "has_profile": p.id in profiles,
            }
            for p in papers
        ],  # fmt: skip
    )
    for kind, rows in by_kind.items():
        await client.run(f"UNWIND $rows AS r CREATE (:{kind} {{project_id: $pid, key: r.key, name: r.name}})",
                         pid=pid, rows=rows)  # fmt: skip
    for (kind, rel), group in _grouped(list(unique.values())).items():
        await client.run(
            f"""UNWIND $links AS l
                MATCH (p:Paper {{project_id: $pid, key: l.paper}}), (e:{kind} {{project_id: $pid, key: l.key}})
                CREATE (p)-[:{rel} {{proposed: l.proposed, evidence: l.evidence}}]->(e)""",
            pid=pid,
            links=group,
        )
    await client.run(
        """UNWIND $rows AS r
           MATCH (p:Paper {project_id: $pid, key: r.paper})
           CREATE (p)-[:HAS_LIMITATION {evidence: r.evidence}]
                  ->(:Limitation {project_id: $pid, key: r.key, name: r.text})""",
        pid=pid,
        rows=limitations,
    )
    await client.run(
        """UNWIND $rows AS r
           MATCH (a:Paper {project_id: $pid, key: r.a}), (b:Paper {project_id: $pid, key: r.b})
           CREATE (a)-[:CONTRADICTS {topic: r.topic, explanation: r.explanation, contradiction_id: r.id,
                                     evidence: r.evidence}]->(b)""",
        pid=pid,
        rows=[
            {
                "a": str(c.paper_a_id),
                "b": str(c.paper_b_id),
                "topic": c.topic,
                "explanation": c.explanation,
                "id": str(c.id),
                "evidence": [*(c.evidence or {}).get("a", []), *(c.evidence or {}).get("b", [])],
            }
            for c in conflicts
            if c.paper_a_id in set(ids) and c.paper_b_id in set(ids)
        ],  # fmt: skip
    )
    if plan is not None:
        await client.run(
            "UNWIND $rows AS r CREATE (:Objective {project_id: $pid, key: r.id, name: r.text})",
            pid=pid,
            rows=[{"id": o.id, "text": o.text} for o in plan.objectives],
        )
        await client.run(
            """UNWIND $rows AS r
               CREATE (q:ResearchQuestion {project_id: $pid, key: r.id, name: r.text, kind: r.kind})
               WITH q, r UNWIND r.objectives AS oid
               MATCH (o:Objective {project_id: $pid, key: oid})
               CREATE (q)-[:SUPPORTS]->(o)""",
            pid=pid,
            rows=[
                {"id": q.id, "text": q.text, "kind": q.kind, "objectives": q.linked_objective_ids}
                for q in plan.research_questions
            ],  # fmt: skip
        )
        await client.run(
            """UNWIND $rows AS r
               MATCH (p:Paper {project_id: $pid, key: r.paper}), (q:ResearchQuestion {project_id: $pid, key: r.rq})
               CREATE (p)-[:RELEVANT_TO {score: r.score}]->(q)""",
            pid=pid,
            rows=[
                {"paper": str(p.id), "rq": rq, "score": p.relevance_score}
                for p in papers
                for rq in p.relevance_rqs or []
            ],
        )
    await write_gaps(project_id, client, await accepted_gaps(project_id, session_factory))
    counts = await client.run("MATCH (n {project_id: $pid}) RETURN labels(n)[0] AS label, count(*) AS n", pid=pid)
    edges = await client.run("MATCH (a {project_id: $pid})-[r]->() RETURN count(r) AS n", pid=pid)
    result = {row["label"]: row["n"] for row in counts} | {"edges": edges[0]["n"] if edges else 0}
    log.info("knowledge_graph_built", project_id=pid, **result)
    return result


async def accepted_gaps(project_id: uuid.UUID, session_factory: SessionFactory) -> list[ResearchGap]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(ResearchGap)
                .where(ResearchGap.project_id == project_id, ResearchGap.status == GapStatus.ACCEPTED)
                .order_by(ResearchGap.created_at)
            )
        )


async def write_gaps(project_id: uuid.UUID, client: Neo4jClient, gaps: list[ResearchGap]) -> None:
    """Replace the project's Gap nodes: Objective-[:ADDRESSES]->Gap-[:BASED_ON]->Paper (papers in the graph)."""
    pid = str(project_id)
    await client.run("MATCH (g:Gap {project_id: $pid}) DETACH DELETE g", pid=pid)
    rows = [
        {
            "key": str(g.id),
            "name": g.statement,
            "confidence": g.confidence.value,
            "objectives": list(g.objective_ids or []),
            "papers": sorted({p for s in (g.evidence or {}).get("signals", []) for p in s.get("paper_ids", [])}),
        }
        for g in gaps
    ]
    await client.run(
        """UNWIND $rows AS r
           CREATE (g:Gap {project_id: $pid, key: r.key, name: r.name, confidence: r.confidence})
           WITH g, r
           CALL (g, r) {
               UNWIND r.objectives AS oid
               MATCH (o:Objective {project_id: $pid, key: oid})
               CREATE (o)-[:ADDRESSES]->(g)
           }
           CALL (g, r) {
               UNWIND r.papers AS paper
               MATCH (p:Paper {project_id: $pid, key: paper})
               CREATE (g)-[:BASED_ON]->(p)
           }""",
        pid=pid,
        rows=rows,
    )


async def sync_gaps(project_id: uuid.UUID, session_factory: SessionFactory, client: Neo4jClient) -> None:
    """After a decision: update the Gap nodes without rebuilding the whole graph."""
    await write_gaps(project_id, client, await accepted_gaps(project_id, session_factory))


def _grouped(links: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        out[(link["kind"], link["rel"])].append(link)
    return out


# ─── Reading ─────────────────────────────────────────────────────────────


def node_id(label: str, key: str) -> str:
    return f"{label}:{key}"


async def subgraph(project_id: uuid.UUID, client: Neo4jClient, types: list[str] | None) -> dict[str, Any]:
    """Nodes (optionally only some labels) and the edges between them, for this project only."""
    pid = str(project_id)
    nodes = await client.run(
        """MATCH (n {project_id: $pid})
           WHERE $types IS NULL OR labels(n)[0] IN $types
           OPTIONAL MATCH (n)-[r]-(m {project_id: $pid})
           RETURN labels(n)[0] AS label, n.key AS key, n.name AS name, properties(n) AS props, count(r) AS degree""",
        pid=pid,
        types=types,
    )
    edges = await client.run(
        """MATCH (a {project_id: $pid})-[r]->(b {project_id: $pid})
           WHERE $types IS NULL OR (labels(a)[0] IN $types AND labels(b)[0] IN $types)
           RETURN labels(a)[0] AS sl, a.key AS sk, labels(b)[0] AS tl, b.key AS tk, type(r) AS type,
                  properties(r) AS props""",
        pid=pid,
        types=types,
    )
    return {
        "nodes": [
            {
                "id": node_id(n["label"], n["key"]),
                "type": n["label"],
                "label": n["name"],
                "degree": n["degree"],
                "props": {k: v for k, v in n["props"].items() if k not in {"project_id", "key", "name"}},
            }
            for n in nodes
        ],  # fmt: skip
        "edges": [
            {
                "source": node_id(e["sl"], e["sk"]),
                "target": node_id(e["tl"], e["tk"]),
                "type": e["type"],
                "props": e["props"],
            }
            for e in edges
        ],  # fmt: skip
    }


async def node_details(project_id: uuid.UUID, client: Neo4jClient, label: str, key: str) -> dict[str, Any] | None:
    pid = str(project_id)
    rows = await client.run(
        f"""MATCH (n:{label} {{project_id: $pid, key: $key}})
            OPTIONAL MATCH (n)-[r]-(m {{project_id: $pid}})
            RETURN n.name AS name, properties(n) AS props,
                   collect(CASE WHEN m IS NULL THEN NULL ELSE {{type: type(r), out: startNode(r) = n,
                       label: labels(m)[0], key: m.key, name: m.name, props: properties(r)}} END) AS neighbours""",
        pid=pid,
        key=key,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "id": node_id(label, key),
        "type": label,
        "label": row["name"],
        "props": {k: v for k, v in row["props"].items() if k not in {"project_id", "key", "name"}},
        "neighbours": [
            {
                "id": node_id(m["label"], m["key"]),
                "type": m["label"],
                "label": m["name"],
                "relation": m["type"],
                "outgoing": m["out"],
                "props": m["props"],
            }
            for m in row["neighbours"]
        ],  # fmt: skip
    }
