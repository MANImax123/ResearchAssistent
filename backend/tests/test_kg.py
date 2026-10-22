"""Step 4.8: knowledge graph — entity normalisation, building from profiles/plan/conflicts, isolation per project.

Runs against the real Neo4j service (every node is tagged with its project id; tests delete theirs).
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.core.config import get_settings
from app.db.models import Contradiction, Paper, PaperProfile
from app.db.models.enums import PaperOrigin, ParseStatus, StageName
from app.services.knowledge_graph import canonical
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from app.services.neo4j import Neo4jClient
from tests.helpers import Flow, auth_headers
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})


@pytest.mark.parametrize(
    ("kind", "names"),
    [
        ("Method", ["BERT", "bert-base", "BERT (uncased)", "BERT-large"]),
        ("Method", ["PyTorch", "PyTorch v1.9.0"]),
        ("Method", ["Graph Neural Networks", "graph neural network"]),
        ("Metric", ["RMSE", "Root mean square error (RMSE)", "root-mean-square error (rmse)"]),
        ("Metric", ["Pearson's R", "Pearson correlation coefficient (Pearson R)"]),
        ("Dataset", ["PDBbind v2016 core set", "PDBbind-2016 Core"]),
        ("Dataset", ["KIBA", "KIBA dataset", "Kiba datasets"]),
    ],
)
def test_entity_variants_merge(kind: str, names: list[str]) -> None:
    assert len({canonical(kind, n) for n in names}) == 1  # type: ignore[arg-type]


def test_distinct_entities_stay_apart() -> None:
    assert canonical("Dataset", "PDBbind v2016") != canonical("Dataset", "PDBbind v2020")
    assert canonical("Dataset", "Davis") == "davis"  # not singularised
    assert canonical("Metric", "MSE") != canonical("Metric", "RMSE")


@pytest.fixture
async def graph(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Neo4jClient]:
    s = get_settings()
    client = Neo4jClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    created: list[str] = []
    original_run = client.run

    async def run(query: str, **params: Any) -> list[dict[str, Any]]:
        if "pid" in params:
            created.append(params["pid"])
        return await original_run(query, **params)

    client.run = run  # type: ignore[method-assign]
    monkeypatch.setattr(registry, "graph_factory", lambda: client)
    await client.ensure_schema()
    yield client
    for pid in set(created):
        await original_run("MATCH (n {project_id: $pid}) DETACH DELETE n", pid=pid)


@pytest.fixture
def idea(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_notes(_: uuid.UUID) -> str:
        return ""

    monkeypatch.setattr(
        registry, "agent_factory",
        lambda sf: {StageName.IDEA: make_idea_agent(lambda: LLMService(FakeLLM(responses=[PLAN]), SETTINGS, sf),
                                                    no_notes)},
    )  # fmt: skip


async def paper(flow: Flow, db: AsyncSession, title: str, *, methods: list[str] = (), datasets: list[str] = (),  # type: ignore[assignment]
                metrics: list[str] = (), limitations: list[str] = (), selected: bool = True,  # type: ignore[assignment]
                rqs: list[str] = (), project_id: uuid.UUID | None = None) -> uuid.UUID:  # type: ignore[assignment]  # fmt: skip
    p = Paper(project_id=project_id or flow.pid, title=title, year=2021, origin=PaperOrigin.SEARCH, sources=[],
              authors=[], selected=selected, parse_status=ParseStatus.PARSED, relevance_score=0.8,
              relevance_rqs=list(rqs))  # fmt: skip
    db.add(p)
    await db.flush()
    if methods or datasets or metrics or limitations:
        db.add(PaperProfile(paper_id=p.id, method="m", tech_stack=list(methods), dataset=list(datasets),
                            metrics=list(metrics), limitations=list(limitations), evidence={}))  # fmt: skip
    await db.commit()
    return p.id


async def build(flow: Flow, project: uuid.UUID | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    url = f"/api/v1/projects/{project or flow.pid}/kg/build"
    resp = await flow.client.post(url, headers=headers or flow.headers)
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=headers or flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


async def test_graph_links_papers_to_methods_datasets_and_plan(
    flow: Flow, graph: Neo4jClient, idea: None, db_session: AsyncSession
) -> None:
    """Acceptance: papers linked to methods/datasets; node details; (isolation in the next test)."""
    await flow.run("idea")
    await flow.act("idea", "approve")
    dta = await paper(flow, db_session, "GraphDTA: affinity with GNNs", methods=["GCN", "PyTorch v1.9"],
                      datasets=["Davis", "KIBA dataset"], metrics=["Mean Square Error (MSE)"],
                      limitations=["Ignores stereochemistry."], rqs=["RQ1"])  # fmt: skip
    cl = await paper(flow, db_session, "GraphCL-DTA: contrastive learning", methods=["PyTorch", "GraphDTA"],
                     datasets=["Davis", "Kiba"], metrics=["MSE", "CI"])  # fmt: skip
    await paper(flow, db_session, "Unselected paper", methods=["SVM"], datasets=["Tox21"], selected=False)
    db_session.add(Contradiction(project_id=flow.pid, paper_a_id=cl, paper_b_id=dta, claim_a="68 drugs.",
                                 claim_b="72 drugs.", evidence={"a": [], "b": []}, topic="dataset: Davis",
                                 explanation="Different versions.", status="open"))  # fmt: skip
    await db_session.commit()

    counts = await build(flow)
    assert counts["Paper"] == 2 and counts["Dataset"] == 2 and counts["Metric"] == 2  # Davis, KIBA; MSE, CI
    assert counts["Method"] == 4  # GraphDTA (own + used by GraphCL-DTA), GCN, PyTorch, GraphCL-DTA
    assert counts["Objective"] == 3 and counts["ResearchQuestion"] == 2 and counts["Limitation"] == 1

    g = (await flow.client.get(flow.url("/kg"), headers=flow.headers)).json()
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    assert labels[f"Paper:{dta}"].startswith("GraphDTA") and "Paper:Unselected" not in str(labels)
    edges = {(e["source"], e["type"], e["target"]) for e in g["edges"]}
    assert (f"Paper:{dta}", "EVALUATED_ON", "Dataset:davis") in edges
    assert (f"Paper:{cl}", "EVALUATED_ON", "Dataset:kiba") in edges  # "KIBA dataset" and "Kiba" merged
    assert (f"Paper:{cl}", "USES", "Method:graphdta") in edges and (f"Paper:{dta}", "USES", "Method:graphdta") in edges
    assert (f"Paper:{cl}", "CONTRADICTS", f"Paper:{dta}") in edges
    assert (f"Paper:{dta}", "RELEVANT_TO", "ResearchQuestion:RQ1") in edges
    assert ("ResearchQuestion:RQ2", "SUPPORTS", "Objective:O3") in edges
    assert labels["Metric:mse"] in {"Mean Square Error (MSE)", "MSE"}

    only = (await flow.client.get(flow.url("/kg"), params={"types": ["Paper", "Dataset"]}, headers=flow.headers)).json()
    assert {n["type"] for n in only["nodes"]} == {"Paper", "Dataset"}
    assert all(e["type"] in {"EVALUATED_ON", "CONTRADICTS"} for e in only["edges"])
    bad = await flow.client.get(flow.url("/kg"), params={"types": ["Secret"]}, headers=flow.headers)
    assert bad.status_code == 422

    details = (await flow.client.get(flow.url("/kg/nodes/Dataset:davis"), headers=flow.headers)).json()
    assert details["label"] == "Davis" and {n["id"] for n in details["neighbours"]} == {f"Paper:{dta}", f"Paper:{cl}"}
    assert all(n["relation"] == "EVALUATED_ON" and not n["outgoing"] for n in details["neighbours"])
    pdetails = (await flow.client.get(flow.url(f"/kg/nodes/Paper:{dta}"), headers=flow.headers)).json()
    assert pdetails["props"]["year"] == 2021 and len(pdetails["neighbours"]) >= 6

    # Rebuilding reflects the current selection
    await flow.client.patch(flow.url(f"/literature/papers/{cl}"), json={"selected": False}, headers=flow.headers)
    assert (await build(flow))["Paper"] == 1


async def test_graphs_are_isolated_per_project(
    flow: Flow, graph: Neo4jClient, idea: None, db_session: AsyncSession
) -> None:
    await paper(flow, db_session, "Mine: a paper", methods=["GCN"], datasets=["Davis"])
    bob = await auth_headers(flow.client, "bob-kg@example.com")
    other = (await flow.client.post("/api/v1/projects", json={"title": "Bob's"}, headers=bob)).json()["id"]
    await paper(flow, db_session, "Theirs: a paper", methods=["GCN"], datasets=["Secret set"],
                project_id=uuid.UUID(other))  # fmt: skip
    await build(flow)
    await build(flow, uuid.UUID(other), bob)

    mine = (await flow.client.get(flow.url("/kg"), headers=flow.headers)).json()
    assert {n["label"] for n in mine["nodes"] if n["type"] == "Paper"} == {"Mine: a paper"}
    assert "Secret set" not in str(mine)
    assert (await flow.client.get(flow.url("/kg/nodes/Dataset:secret"), headers=flow.headers)).status_code == 404
    gcn = (await flow.client.get(flow.url("/kg/nodes/Method:gcn"), headers=flow.headers)).json()
    assert [n["label"] for n in gcn["neighbours"]] == ["Mine: a paper"]  # same entity name, other project unseen
    assert (await flow.client.get(f"/api/v1/projects/{other}/kg", headers=flow.headers)).status_code == 404


def test_display_names_drop_percent_signs() -> None:
    from app.services.knowledge_graph import Entities

    entities = Entities()
    key = entities.add("Metric", "F1%")
    assert key == entities.add("Metric", "F1") and entities.display("Metric", key or "") == "F1"
