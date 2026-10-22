"""Step 4.7: contradiction detection — grouping, evidence from the right paper, dismissals persist."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.literature.contradictions import GroupPaper, Passage, detect, groups, normalize
from app.core.config import get_settings
from app.db.models import Paper, PaperChunk, PaperProfile
from app.db.models.enums import PaperOrigin, ParseStatus, StageName
from app.services.embeddings import EmbeddingService, FakeEmbeddings
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from tests.helpers import Flow, factory_for
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})
EMBEDDINGS = EmbeddingService(FakeEmbeddings(), SETTINGS)


def conflict(a: str, b: str, ev_a: list[str], ev_b: list[str], **extra: Any) -> dict[str, Any]:
    return {"paper_a": a, "paper_b": b, "claim_a": f"{a} reports RMSE 1.32 for GraphDTA on PDBbind core.",
            "claim_b": f"{b} reports RMSE 1.56 for GraphDTA on PDBbind core.", "evidence_a": ev_a, "evidence_b": ev_b,
            "explanation": "Different PDBbind versions.", "explanation_kind": "dataset_version", **extra}  # fmt: skip


# ─── Units ──────────────────────────────────────────────────────────────


def test_grouping_by_shared_dataset_then_metric() -> None:
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    found = groups(
        {
            a: (["PDBbind v2016 core set"], ["RMSE"]),
            b: (["PDBbind-2016 Core"], ["RMSE", "Pearson R"]),
            c: (["Davis"], ["Pearson R"]),
            d: (["KIBA"], ["CI"]),
        }
    )
    assert normalize("PDBbind v2016 core set") == normalize("PDBbind-2016 Core") == "pdbbind 2016 core"
    assert [(g.topic, set(g.paper_ids)) for g in found] == [
        ("dataset: PDBbind v2016 core set", {a, b}),
        ("metric: Pearson R", {b, c}),  # RMSE would only repeat the a–b pair
    ]


async def test_only_claims_backed_by_their_own_papers_survive(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    papers = [
        GroupPaper(uuid.uuid4(), f"Paper {i}", 2020 + i,
                   [Passage(uuid.uuid4(), "Results", 5, f"Paper {i} results text.") for _ in range(2)])
        for i in range(1, 4)
    ]  # fmt: skip
    fake = FakeLLM(
        responses=[
            {"contradictions": [
                conflict("P1", "P2", ["P1-C1"], ["P2-C2"]),  # valid
                conflict("P1", "P3", ["P2-C1"], ["P3-C1"]),  # claim A cites paper 2's passage
                conflict("P2", "P2", ["P2-C1"], ["P2-C2"]),  # same paper
                conflict("P1", "P3", ["P1-C1"], []),  # claim B without evidence
                conflict("P1", "P9", ["P1-C1"], ["P9-C1"]),  # unknown paper
            ]}
        ]
    )  # fmt: skip
    found = await detect("dataset: PDBbind", papers, LLMService(fake, SETTINGS, factory_for(db_session)), project_id)

    assert len(found) == 1
    f = found[0]
    assert (f.paper_a, f.paper_b) == (papers[0].paper_id, papers[1].paper_id)
    assert f.evidence_a == [str(papers[0].passages[0].chunk_id)] and f.evidence_b == [
        str(papers[1].passages[1].chunk_id)
    ]
    assert f.explanation_kind == "dataset_version" and f.topic == "dataset: PDBbind"
    prompt = fake.calls[0].prompt
    assert "Topic: dataset: PDBbind" in prompt and "[P3-C2] Results (p. 5)" in prompt


# ─── API flow ───────────────────────────────────────────────────────────


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
    monkeypatch.setattr(registry, "embeddings_factory", lambda: EMBEDDINGS)
    return fake


async def profiled(flow: Flow, db_session: AsyncSession, title: str, datasets: list[str]) -> uuid.UUID:
    paper = Paper(project_id=flow.pid, title=title, year=2021, origin=PaperOrigin.SEARCH, sources=[], authors=[],
                  selected=True, parse_status=ParseStatus.PARSED)  # fmt: skip
    db_session.add(paper)
    await db_session.flush()
    text = f"{title} reports RMSE on {', '.join(datasets)}."
    [vector] = await EMBEDDINGS.embed_documents([text])
    chunk = PaperChunk(paper_id=paper.id, chunk_index=0, section_name="5 Results", section_kind="results", page=7,
                       text=text, embedding=vector)  # fmt: skip
    db_session.add(chunk)
    await db_session.flush()
    cid = str(chunk.id)
    evidence = {"results": [cid], "dataset": [[cid]] * len(datasets), "metrics": [[cid]]}
    db_session.add(PaperProfile(paper_id=paper.id, method="GNN.", results="RMSE 1.3.", dataset=datasets,
                                metrics=["RMSE"], limitations=[], tech_stack=[], evidence=evidence))  # fmt: skip
    await db_session.commit()
    return paper.id


async def detect_job(flow: Flow) -> dict[str, Any]:
    resp = await flow.post("/literature/contradictions/detect")
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


async def listing(flow: Flow, status: str = "open") -> list[dict[str, Any]]:
    resp = await flow.client.get(
        flow.url("/literature/contradictions"), params={"status": status}, headers=flow.headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def test_contradictions_with_evidence_and_persistent_dismissals(
    flow: Flow, llm: FakeLLM, db_session: AsyncSession
) -> None:
    """Acceptance (with fakes): contradictions show both evidence passages; dismissals persist."""
    await flow.run("idea")
    await flow.act("idea", "approve")
    await profiled(flow, db_session, "GraphDTA", ["PDBbind core"])
    assert (await flow.post("/literature/contradictions/detect")).status_code == 409  # one profiled paper only
    await profiled(flow, db_session, "SIGN", ["PDBbind core"])
    await profiled(flow, db_session, "HBGSA", ["PDBbind core"])

    two = {"contradictions": [conflict("P1", "P2", ["P1-C1"], ["P2-C1"]),
                              conflict("P2", "P3", ["P2-C1"], ["P3-C1"], explanation_kind="data_split")]}  # fmt: skip
    llm.responses.append(two)
    assert await detect_job(flow) == {"groups": 1, "papers": 3, "found": 2, "suppressed": 0}

    found = await listing(flow)
    assert len(found) == 2
    first = found[0]
    assert first["topic"] == "dataset: PDBbind core" and first["status"] == "open"
    assert first["a"]["title"] != first["b"]["title"]
    assert first["a"]["evidence"][0]["section"] == "5 Results" and first["a"]["evidence"][0]["page"] == 7
    assert first["b"]["evidence"][0]["text"].endswith("reports RMSE on PDBbind core.")
    assert first["explanation"] == "Different PDBbind versions."

    # Dismiss one: it leaves the open list, keeps its reason, and isn't reported again by a new run
    dismissed = await flow.post(f"/literature/contradictions/{first['id']}/dismiss", {"reason": "Different splits"})
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    llm.responses.append(two)
    assert (await detect_job(flow))["suppressed"] == 1
    assert len(await listing(flow)) == 1
    gone = await listing(flow, "dismissed")
    assert [(g["id"], g["dismiss_reason"]) for g in gone] == [(first["id"], "Different splits")]
    assert len(await listing(flow, "all")) == 2

    restored = await flow.post(f"/literature/contradictions/{first['id']}/restore")
    assert restored.json()["status"] == "open" and restored.json()["dismiss_reason"] is None
    assert (await flow.post(f"/literature/contradictions/{uuid.uuid4()}/dismiss", {})).status_code == 404


def test_short_names_come_from_titles_with_a_method_name() -> None:
    from app.services.contradictions import short_name

    assert short_name("GraphDTA: Predicting drug–target binding affinity with graph neural networks") == "GraphDTA"
    assert short_name("PLANET v2.0: A comprehensive protein-ligand affinity model") == "PLANET v2.0"
    assert short_name("A point cloud-based deep learning strategy for affinity prediction") is None
    assert short_name("Deep learning for drug discovery: a review of graph-based methods") is None


async def test_cross_references_and_no_embeddings(
    flow: Flow, llm: FakeLLM, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passages where one paper names another's method are shown; detection still runs without embeddings."""
    from app.services.contradictions import cross_references

    await flow.run("idea")
    await flow.act("idea", "approve")
    a = await profiled(flow, db_session, "GraphDTA: affinity with GNNs", ["Davis"])
    b = await profiled(flow, db_session, "GraphCL-DTA: contrastive learning", ["Davis"])
    mention = PaperChunk(paper_id=b, chunk_index=1, section_name="3.5 Comparison", section_kind="results", page=10,
                         text="Baselines: GraphDTA reaches MSE 0.263 on Davis in our runs.")  # fmt: skip
    db_session.add(mention)
    await db_session.commit()

    row = "Davis | GraphDTA (Nguyen et al.) | 0.241 | 0.887"
    table = PaperChunk(paper_id=b, chunk_index=9, section_name="Table 5: Comparison with state-of-the-art",
                       section_kind="results", page=10, text=row)  # fmt: skip
    db_session.add(table)
    await db_session.commit()
    found = await cross_references(db_session, b, ["GraphDTA: affinity with GNNs"])
    assert [(c.id, name) for c, name in found] == [(table.id, "GraphDTA"), (mention.id, "GraphDTA")]  # tables first

    class Broken:
        async def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("429 quota")

    monkeypatch.setattr(registry, "embeddings_factory", lambda: Broken())
    llm.responses.append({"contradictions": []})
    assert (await detect_job(flow))["groups"] == 1
    prompt = llm.calls[-1].prompt
    assert "GraphDTA (Nguyen et al.) | 0.241" in prompt and a


def test_long_passages_are_excerpted_around_the_mention() -> None:
    from app.services.contradictions import excerpt

    table = "Davis | DeepDTA 0.261 | " * 200 + "GraphDTA (Nguyen et al.) | 0.241 | 0.887 | " + "KIBA rows | " * 200
    cut = excerpt(table, "GraphDTA")
    assert "GraphDTA (Nguyen et al.) | 0.241" in cut and cut.startswith("…") and cut.endswith("…")
    assert len(cut) < 1900 and excerpt("short text", "GraphDTA") == "short text"


def test_explanations_name_the_papers() -> None:
    from app.agents.literature.contradictions import name_papers

    long_title = "A long title without a method name in front of it at all"
    papers = [GroupPaper(uuid.uuid4(), "GraphCL-DTA: contrastive learning", 2024, []),
              GroupPaper(uuid.uuid4(), long_title, 2019, [])]  # fmt: skip
    text = "Paper P1 states 68 drugs, whereas P2 states 72 (see P2-C1); P9 is unknown."
    assert name_papers(text, papers) == (
        "GraphCL-DTA states 68 drugs, whereas “A long title without a method name in front of it…” states 72 "
        "(see P2-C1); P9 is unknown."
    )
