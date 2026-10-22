"""Step 4.9: potential research gaps — signals, traceability, hedged wording, decisions, state and graph.

The API flow runs against the real Neo4j service (gaps are written to the graph); nodes are cleaned up.
"""

import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.literature import gaps as agent
from app.core.config import get_settings
from app.db.models import Contradiction, Paper, PaperChunk, PaperProfile, Project
from app.db.models.enums import PaperOrigin, ParseStatus, StageName
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from app.services.neo4j import Neo4jClient
from tests.helpers import Flow, auth_headers
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})


# ─── Agent helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("statement", "hedged"),
    [
        ("It is unclear whether GraphDTA generalises to PDBbind 2016.", True),
        ("Pocket-level protein features may be under-used by sequence-based DTA models.", True),
        ("There appears to be little evidence on cold-target splits among the selected papers.", True),
        ("GraphDTA has not been evaluated on PDBbind 2016.", False),  # a statement of fact
        ("No study has evaluated GraphDTA on PDBbind, which may matter.", False),  # hedge can't rescue a certainty
        ("This confirms that GNNs may overfit Davis.", False),
    ],
)
def test_hedged_wording(statement: str, hedged: bool) -> None:
    assert agent.is_hedged(statement) is hedged


def test_future_work_sentences() -> None:
    text = (
        "Our model reaches CI 0.89. In future work, we plan to model protein pockets explicitly. "
        "We thank the reviewers. Whether the gains hold on cold targets remains an open question."
    )
    assert agent.future_work_sentences(text) == [
        "In future work, we plan to model protein pockets explicitly.",
        "Whether the gains hold on cold targets remains an open question.",
    ]


def test_untested_combinations() -> None:
    uses = {"gcn": {"a"}, "svm": {"c"}, "pytorch": {"c"}, "unknown": {"z"}}
    evaluated = {"davis": {"a", "b"}, "kiba": {"a", "b"}, "tox21": {"c"}, "pdbbind": {"b", "c"}}
    found = agent.untested_combinations(uses, evaluated)
    assert ("gcn", "pdbbind") in found
    assert ("svm", "davis") in found and ("svm", "kiba") in found
    assert not any(m == "pytorch" for m, _ in found)  # software, not a method
    assert agent.by_method(found) == [("gcn", ["pdbbind"]), ("svm", ["davis", "kiba"])]  # ties broken by name
    assert not any(d == "tox21" for _, d in found)  # used by one paper only: not a shared benchmark
    assert not any(m == "unknown" for m, _ in found)  # never evaluated anywhere we know of: no signal


def _signal(kind: agent.SignalKind, papers: list[uuid.UUID]) -> agent.Signal:
    return agent.Signal("X1", kind, "text", papers)


def test_confidence_caps() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert agent.cap_confidence("high", [_signal("limitation", [a]), _signal("future_work", [b])]) == "high"
    assert agent.cap_confidence("high", [_signal("limitation", [a]), _signal("future_work", [a])]) == "medium"
    assert agent.cap_confidence("high", [_signal("contradiction", [a, b])]) == "medium"  # one signal
    assert agent.cap_confidence("medium", [_signal("untested", [a, b]), _signal("untested", [b])]) == "low"
    assert agent.cap_confidence("low", [_signal("limitation", [a]), _signal("future_work", [b])]) == "low"


# ─── API flow ───────────────────────────────────────────────────────────


@pytest.fixture
async def graph(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Neo4jClient]:
    s = get_settings()
    client = Neo4jClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    created: set[str] = set()
    original_run = client.run

    async def run(query: str, **params: Any) -> list[dict[str, Any]]:
        if "pid" in params:
            created.add(params["pid"])
        return await original_run(query, **params)

    client.run = run  # type: ignore[method-assign]
    monkeypatch.setattr(registry, "graph_factory", lambda: client)
    await client.ensure_schema()
    yield client
    for pid in created:
        await original_run("MATCH (n {project_id: $pid}) DETACH DELETE n", pid=pid)


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    idea = FakeLLM(responses=[PLAN])

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    monkeypatch.setattr(
        registry, "agent_factory",
        lambda sf: {StageName.IDEA: make_idea_agent(lambda: LLMService(idea, SETTINGS, sf), no_notes)},
    )  # fmt: skip
    monkeypatch.setattr(registry, "llm_factory", lambda sf: lambda: LLMService(fake, SETTINGS, sf))
    return fake


async def profiled(flow: Flow, db: AsyncSession, title: str, *, methods: list[str], datasets: list[str],
                   limitations: list[str], conclusion: str = "") -> uuid.UUID:  # fmt: skip
    paper = Paper(project_id=flow.pid, title=title, year=2021, origin=PaperOrigin.SEARCH, sources=[], authors=[],
                  selected=True, parse_status=ParseStatus.PARSED)  # fmt: skip
    db.add(paper)
    await db.flush()
    results = PaperChunk(paper_id=paper.id, chunk_index=0, section_name="5 Results", section_kind="results", page=6,
                         text=f"{title} results on {', '.join(datasets)}. {' '.join(limitations)}")  # fmt: skip
    db.add(results)
    if conclusion:
        db.add(PaperChunk(paper_id=paper.id, chunk_index=1, section_name="6 Conclusion", section_kind="conclusion",
                          page=8, text=conclusion))  # fmt: skip
    await db.flush()
    cid = str(results.id)
    db.add(PaperProfile(paper_id=paper.id, method="m", tech_stack=methods, dataset=datasets, metrics=["RMSE"],
                        limitations=limitations,
                        evidence={"dataset": [[cid]] * len(datasets), "tech_stack": [[cid]] * len(methods),
                                  "limitations": [[cid]] * len(limitations)}))  # fmt: skip
    await db.commit()
    return paper.id


def signal_id(prompt: str, kind: str, needle: str) -> str:
    match = re.search(rf"\[([LCUF]\d+)\] \({kind}\) [^\n]*{re.escape(needle)}", prompt)
    assert match, f"no {kind} signal mentioning {needle!r}"
    return match.group(1)


def gaps_response(build: Callable[[str], list[dict[str, Any]]]) -> Callable[[str], str]:
    return lambda prompt: json.dumps({"gaps": build(prompt)})


async def detect(flow: Flow) -> dict[str, Any]:
    resp = await flow.post("/gaps/detect")
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


async def listing(flow: Flow, status: str = "all") -> list[dict[str, Any]]:
    resp = await flow.client.get(flow.url("/gaps"), params={"status": status}, headers=flow.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def test_gaps_are_traceable_hedged_and_decided_by_the_researcher(
    flow: Flow, llm: FakeLLM, graph: Neo4jClient, db_session: AsyncSession
) -> None:
    await flow.run("idea")
    await flow.act("idea", "approve")
    assert (await flow.post("/gaps/detect")).status_code == 409  # nothing analysed yet

    dta = await profiled(flow, db_session, "GraphDTA: affinity with GNNs", methods=["GCN"], datasets=["Davis", "KIBA"],
                         limitations=["Ignores protein structure."],
                         conclusion="CI 0.89. In future work, we plan to model protein pockets.")  # fmt: skip
    sign = await profiled(flow, db_session, "SIGN: structure-aware interactions", methods=["SIGN"],
                          datasets=["PDBbind 2016", "Davis"], limitations=["Needs 3D complexes."])  # fmt: skip
    await profiled(flow, db_session, "DeepDTA: sequences only", methods=["CNN"], datasets=["KIBA", "PDBbind v2016"],
                   limitations=[])  # fmt: skip
    db_session.add(Contradiction(project_id=flow.pid, paper_a_id=dta, paper_b_id=sign, claim_a="Davis has 68 drugs.",
                                 claim_b="Davis has 72 drugs.", evidence={"a": [], "b": []}, topic="dataset: Davis",
                                 explanation="Different versions.", status="open"))  # fmt: skip
    await db_session.commit()

    def first(prompt: str) -> list[dict[str, Any]]:
        assert "O1: Design a GNN" in prompt and "RQ1:" in prompt and "(none)" in prompt  # plan, nothing decided
        untested = signal_id(prompt, "untested", "Method GCN")
        assert "PDBbind 2016" in prompt.split(f"[{untested}]")[1].split("\n")[0]  # "PDBbind v2016" merged
        protein = signal_id(prompt, "limitation", "Ignores protein structure")
        return [
            {"statement": "It is unclear whether GCN-based affinity models such as GraphDTA generalise to "
                          "PDBbind 2016.", "rationale": "GCN is only reported on Davis/KIBA.",
             "sources": [untested, protein], "confidence": "high", "objective_ids": ["O1", "O9"]},
            {"statement": "GraphDTA ignores protein structure entirely.", "sources": [protein],
             "confidence": "high"},
            {"statement": "Something with no traceable source at all, maybe.", "sources": ["Z9"]},
            {"statement": "Explicit modelling of protein pockets may be under-explored among the selected papers.",
             "sources": [signal_id(prompt, "future work", "protein pockets")], "confidence": "medium"},
            {"statement": "Reported Davis results may differ because papers use different dataset versions.",
             "sources": [signal_id(prompt, "contradiction", "68 drugs")], "confidence": "high"},
        ]  # fmt: skip

    reworded = {"statements": [{"number": 1, "statement": "GraphDTA may under-use protein structure when "
                                                          "predicting affinity."}]}  # fmt: skip
    llm.responses = [gaps_response(first), reworded]
    result = await detect(flow)
    assert result["found"] == 4
    assert result["signals"] == {"limitation": 2, "contradiction": 1, "untested": result["signals"]["untested"],
                                 "future_work": 1} and result["signals"]["untested"] >= 1  # fmt: skip
    assert "under-use protein structure" not in llm.calls[0].prompt and "GraphDTA ignores" in llm.calls[1].prompt

    gaps = await listing(flow)
    by_text = {g["statement"].split()[0]: g for g in gaps}
    assert all(g["status"] == "candidate" for g in gaps)
    assert all(agent.is_hedged(g["statement"]) for g in gaps)  # acceptance: none phrased as certain facts
    assert all(g["signals"] for g in gaps)  # acceptance: traceable
    unclear = by_text["It"]
    assert unclear["confidence"] == "high" and [o["id"] for o in unclear["objectives"]] == ["O1"]
    assert {s["kind"] for s in unclear["signals"]} == {"untested", "limitation"}
    limitation = next(s for s in unclear["signals"] if s["kind"] == "limitation")
    assert [p["title"] for p in limitation["papers"]] == ["GraphDTA: affinity with GNNs"]
    assert limitation["evidence"][0]["section"] == "5 Results"
    assert by_text["GraphDTA"]["confidence"] == "medium"  # one paper, one signal: capped
    assert by_text["Reported"]["confidence"] == "medium"
    future = by_text["Explicit"]["signals"][0]
    assert future["kind"] == "future_work" and future["evidence"][0]["section"] == "6 Conclusion"

    # Edit + accept one, reject another
    gid = unclear["id"]
    bad = await flow.client.patch(flow.url(f"/gaps/{gid}"), json={"objective_ids": ["O7"]}, headers=flow.headers)
    assert bad.status_code == 422
    resp = await flow.client.patch(
        flow.url(f"/gaps/{gid}"),
        json={"status": "accepted", "objective_ids": ["O2"], "statement": unclear["statement"] + " (cold targets)"},
        headers=flow.headers,
    )
    assert resp.status_code == 200, resp.text
    accepted = resp.json()
    assert accepted["status"] == "accepted" and accepted["edited"] and accepted["decided_at"]
    assert [o["id"] for o in accepted["objectives"]] == ["O2"]
    rejected = by_text["Reported"]["id"]
    assert (await flow.client.patch(flow.url(f"/gaps/{rejected}"), json={"status": "rejected"},
                                    headers=flow.headers)).status_code == 200  # fmt: skip

    project = await db_session.get(Project, flow.pid, populate_existing=True)
    assert project is not None
    await db_session.refresh(project, ["research_state"])
    assert project.research_state is not None and project.research_state.snapshot["gap_ids"] == [gid]
    pid = str(flow.pid)
    links = await graph.run(
        "MATCH (o:Objective {project_id: $pid})-[:ADDRESSES]->(g:Gap {project_id: $pid}) "
        "OPTIONAL MATCH (g)-[:BASED_ON]->(p:Paper) RETURN o.key AS o, g.key AS g, collect(p.key) AS papers",
        pid=pid,
    )
    assert len(links) == 1 and links[0]["o"] == "O2" and links[0]["g"] == gid
    assert str(dta) in links[0]["papers"]

    # A re-run replaces the undecided candidates, keeps decisions, and doesn't repeat decided gaps
    def second(prompt: str) -> list[dict[str, Any]]:
        assert "(cold targets)" in prompt and "different dataset versions" in prompt  # decided ones listed
        protein = signal_id(prompt, "limitation", "Ignores protein structure")
        return [
            {"statement": "It is unclear whether GCN based affinity models such as GraphDTA generalize to PDBbind "
                          "2016 (cold targets).", "sources": [protein]},
            {"statement": "Structure-free models may underperform on targets with few known binders.",
             "sources": [protein, signal_id(prompt, "limitation", "Needs 3D complexes")], "confidence": "medium"},
        ]  # fmt: skip

    llm.responses = [gaps_response(second)]
    assert (await detect(flow))["found"] == 1
    after = await listing(flow)
    assert [g["status"] for g in after] == ["accepted", "candidate", "rejected"]  # listing order
    assert after[1]["statement"].startswith("Structure-free models")
    assert await graph.run("MATCH (g:Gap {project_id: $pid}) RETURN g.key AS k", pid=pid) == [{"k": gid}]
    shown = await flow.client.get(flow.url("/kg"), params={"types": ["Gap", "Objective"]}, headers=flow.headers)
    assert {(e["source"], e["type"], e["target"]) for e in shown.json()["edges"]} == {
        ("Objective:O2", "ADDRESSES", f"Gap:{gid}")
    }

    # Back to candidate: leaves the state and the graph
    await flow.client.patch(flow.url(f"/gaps/{gid}"), json={"status": "candidate"}, headers=flow.headers)
    await db_session.refresh(project, ["research_state"])
    assert project.research_state is not None and project.research_state.snapshot["gap_ids"] == []
    assert await graph.run("MATCH (g:Gap {project_id: $pid}) RETURN g", pid=pid) == []


async def test_gaps_belong_to_their_project(flow: Flow, llm: FakeLLM, graph: Neo4jClient,
                                            db_session: AsyncSession) -> None:  # fmt: skip
    await flow.run("idea")
    await flow.act("idea", "approve")
    await profiled(flow, db_session, "GraphDTA: affinity", methods=["GCN"], datasets=["Davis"],
                   limitations=["Ignores protein structure."])  # fmt: skip
    llm.responses = [gaps_response(lambda p: [{"statement": "Protein structure may be under-used by GraphDTA.",
                                                "sources": [signal_id(p, "limitation", "protein")]}])]  # fmt: skip
    assert (await detect(flow))["found"] == 1
    [gap] = await listing(flow)
    assert gap["objectives"] == []  # the agent suggested none

    bob = await auth_headers(flow.client, "bob-gaps@example.com")
    assert (await flow.client.get(flow.url("/gaps"), headers=bob)).status_code == 404
    patched = await flow.client.patch(flow.url(f"/gaps/{gap['id']}"), json={"status": "accepted"}, headers=bob)
    assert patched.status_code == 404
    other = (await flow.client.post("/api/v1/projects", json={"title": "Bob's"}, headers=bob)).json()["id"]
    cross = await flow.client.patch(f"/api/v1/projects/{other}/gaps/{gap['id']}", json={"status": "accepted"},
                                    headers=bob)  # fmt: skip
    assert cross.status_code == 404  # a gap id from another project
    missing = await flow.client.patch(flow.url(f"/gaps/{uuid.uuid4()}"), json={"status": "accepted"},
                                      headers=flow.headers)  # fmt: skip
    assert missing.status_code == 404
    rows = await db_session.scalars(select(Paper.id).where(Paper.project_id == uuid.UUID(other)))
    assert list(rows) == []
