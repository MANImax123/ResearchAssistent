"""Step 4.2: relevance ranking (embeddings + LLM rerank), selection, and `selected_paper_ids`."""

import copy
import json
import re
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.idea.schemas import IdeaPlan
from app.agents.literature.ranking import Candidate, rerank, rq_fingerprint, similarities
from app.core.config import get_settings
from app.db.models import Paper
from app.db.models.enums import PaperOrigin, StageName
from app.services.embeddings import EmbeddingService, FakeEmbeddings
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from tests.helpers import Flow, factory_for
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})

# Titles share words with the plan's research questions to different degrees (FakeEmbeddings are bag-of-words)
PAPERS = [
    ("GNN ranks drug candidates better than fingerprints", "Does a GNN rank candidates better than fingerprints? Yes."),
    ("Ranking errors in specific molecular families", "Ranking errors concentrate in specific molecular families."),
    ("Fingerprints for candidate ranking", "Fingerprint baselines rank candidates."),
    ("NMR spectroscopy of proteins", "Nuclear magnetic resonance methods for structural biology."),
    ("Portfolio management in pharma", "Business strategy for drug pipelines."),
]


def judge(prompt: str) -> dict[str, Any]:
    """Fake LLM judge: scores each listed paper by how many plan words its title shares."""
    judgements = []
    for pid, title in re.findall(r"^(P\d+) — (.+?)(?: \(|$)", prompt, flags=re.MULTILINE):
        words = set(title.lower().split()) & {"gnn", "ranking", "ranks", "fingerprints", "molecular", "candidate"}
        rqs = ["RQ1"] if {"gnn", "fingerprints"} & words else (["RQ2"] if "molecular" in words else [])
        judgements.append({"paper": pid, "score": min(10, 3 * len(words)), "rq_ids": [*rqs, "RQ9"],
                           "reason": f"Title mentions {', '.join(sorted(words)) or 'nothing relevant'}."})  # fmt: skip
    return {"judgements": judgements}


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fakes = {"idea": FakeLLM(), "judge": FakeLLM(), "embeddings": FakeEmbeddings()}

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    monkeypatch.setattr(
        registry,
        "agent_factory",
        lambda sf: {StageName.IDEA: make_idea_agent(lambda: LLMService(fakes["idea"], SETTINGS, sf), no_notes)},
    )
    monkeypatch.setattr(registry, "llm_factory", lambda sf: lambda: LLMService(fakes["judge"], SETTINGS, sf))
    monkeypatch.setattr(registry, "embeddings_factory", lambda: EmbeddingService(fakes["embeddings"], SETTINGS))
    return fakes


async def approved_with_papers(flow: Flow, fakes: dict[str, Any], db_session: AsyncSession) -> list[uuid.UUID]:
    fakes["idea"].responses.append(PLAN)
    await flow.run("idea")
    await flow.act("idea", "approve")
    ids = []
    for i, (title, abstract) in enumerate(PAPERS):
        paper = Paper(project_id=flow.pid, title=title, abstract=abstract, doi=f"10.1000/p{i}",
                      origin=PaperOrigin.SEARCH, sources=["openalex"], found_by=["Q1"], authors=[])  # fmt: skip
        db_session.add(paper)
        await db_session.flush()
        ids.append(paper.id)
    await db_session.commit()
    return ids


async def rank(flow: Flow, top_n: int = 3) -> dict[str, Any]:
    resp = await flow.post("/literature/rank", {"top_n": top_n})
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


async def papers(flow: Flow, **params: Any) -> list[dict[str, Any]]:
    resp = await flow.client.get(flow.url("/literature/papers"), params=params, headers=flow.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]  # type: ignore[no-any-return]


async def workspace(flow: Flow) -> dict[str, Any]:
    return (await flow.client.get(flow.url("/literature"), headers=flow.headers)).json()  # type: ignore[no-any-return]


# ─── Units ──────────────────────────────────────────────────────────────


def test_similarity_and_fingerprint() -> None:
    assert similarities([1.0, 0.0], {"RQ1": [1.0, 0.0], "RQ2": [0.0, 1.0]}) == {"RQ1": 1.0, "RQ2": 0.0}
    plan = IdeaPlan.model_validate(PLAN)
    edited = copy.deepcopy(PLAN)
    edited["research_questions"][0]["text"] = "Does the GNN rank candidates better than docking?"
    assert rq_fingerprint(plan) == rq_fingerprint(IdeaPlan.model_validate(PLAN))
    assert rq_fingerprint(plan) != rq_fingerprint(IdeaPlan.model_validate(edited))


async def test_rerank_batches_and_asks_again_for_skipped_papers(
    db_session: AsyncSession, project_id: uuid.UUID
) -> None:
    candidates = [Candidate(f"k{i}", f"GNN paper {i}", None, 2020, None) for i in range(12)]

    def skip_one(prompt: str) -> dict[str, Any]:
        result = judge(prompt)
        if "P1 — GNN paper 0" in prompt:
            result["judgements"] = result["judgements"][1:]  # the model forgets P1 in the first batch
        return result

    fake = FakeLLM(responses=[skip_one, judge, judge])
    llm = LLMService(fake, SETTINGS, factory_for(db_session))
    judged = await rerank(candidates, IdeaPlan.model_validate(PLAN), llm, project_id)

    assert set(judged) == {f"k{i}" for i in range(12)}
    assert len(fake.calls) == 3  # two batches (10 + 2) and one retry for the skipped paper
    retry = next(c.prompt for c in fake.calls if "P1 — GNN paper 0" in c.prompt and "P2 —" not in c.prompt)
    assert "(no abstract available)" in retry
    assert judged["k0"].rq_ids == ["RQ1"]  # unknown RQ ids are dropped


# ─── Ranking flow ───────────────────────────────────────────────────────


async def test_rank_orders_by_relevance_with_reasons(
    flow: Flow, setup: dict[str, Any], db_session: AsyncSession
) -> None:
    """Acceptance (with fakes): on-topic papers come first, each judged one with a reason."""
    await approved_with_papers(flow, setup, db_session)
    setup["judge"].responses.append(judge)

    result = await rank(flow, top_n=3)

    assert result == {"papers": 5, "embedded": 5, "judged": 3, "reused": 0, "unjudged": 0}
    ranked = await papers(flow, sort="relevance")
    assert [p["title"] for p in ranked[:3]] == [PAPERS[0][0], PAPERS[2][0], PAPERS[1][0]]
    top = ranked[0]
    assert top["relevance_score"] == 0.9 and top["relevance_rqs"] == ["RQ1"]
    assert top["relevance_reason"].startswith("Title mentions") and set(top["rq_similarity"]) == {"RQ1", "RQ2"}
    assert top["similarity"] == max(top["rq_similarity"].values())
    unjudged = ranked[3:]
    assert all(p["relevance_score"] is None and p["similarity"] is not None for p in unjudged)
    assert {p["title"] for p in unjudged} == {PAPERS[3][0], PAPERS[4][0]}  # least similar: not sent to the LLM
    prompt = setup["judge"].calls[0].prompt
    assert "RQ1 (question): Does the GNN rank candidates" in prompt and "O3: Analyse failure cases" in prompt

    ws = await workspace(flow)
    assert (ws["judged_count"], ws["unranked_count"]) == (3, 0)
    assert [p["title"] for p in await papers(flow, rq="rq2")] == [PAPERS[1][0]]
    assert len(await papers(flow, min_score=0.5)) == 3

    # Ranking again reuses the judgements and embeddings: no new LLM or embedding calls
    embed_calls = len(setup["embeddings"].calls)
    again = await rank(flow, top_n=3)
    assert again == {"papers": 5, "embedded": 0, "judged": 0, "reused": 3, "unjudged": 0}
    assert len(setup["judge"].calls) == 1 and len(setup["embeddings"].calls) == embed_calls + 2  # just the 2 RQs


async def test_changed_research_questions_invalidate_judgements(
    flow: Flow, setup: dict[str, Any], db_session: AsyncSession
) -> None:
    await approved_with_papers(flow, setup, db_session)
    setup["judge"].responses += [judge, judge]
    await rank(flow, top_n=2)

    await flow.act("idea", "go-back")
    plan = (await flow.client.get(flow.url("/idea"), headers=flow.headers)).json()["plan"]
    plan["research_questions"][1]["text"] = "Ranking errors concentrate in kinase inhibitors."
    assert (await flow.client.put(flow.url("/idea/plan"), json=plan, headers=flow.headers)).status_code == 200
    assert (await flow.post("/literature/rank", {"top_n": 2})).status_code == 409  # plan not approved
    await flow.act("idea", "approve")

    result = await rank(flow, top_n=2)
    assert result["judged"] == 2 and result["reused"] == 0
    assert len(setup["judge"].calls) == 2


async def test_rank_needs_papers(flow: Flow, setup: dict[str, Any]) -> None:
    setup["idea"].responses.append(PLAN)
    await flow.run("idea")
    await flow.act("idea", "approve")
    assert (await flow.post("/literature/rank", {"top_n": 10})).status_code == 409
    assert (await flow.post("/literature/rank", {"top_n": 500})).status_code == 422


# ─── Selection ──────────────────────────────────────────────────────────


async def patch(flow: Flow, paper_id: uuid.UUID, **body: Any) -> dict[str, Any]:
    resp = await flow.client.patch(flow.url(f"/literature/papers/{paper_id}"), json=body, headers=flow.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def snapshot_ids(flow: Flow) -> list[str]:
    return (await flow.state())["state"]["selected_paper_ids"]  # type: ignore[no-any-return]


async def test_selection_persists_and_becomes_selected_paper_ids(
    flow: Flow, setup: dict[str, Any], db_session: AsyncSession
) -> None:
    """Acceptance: selection persists; the selected set becomes `selected_paper_ids`."""
    ids = await approved_with_papers(flow, setup, db_session)
    setup["judge"].responses.append(judge)
    await rank(flow, top_n=5)

    must = await patch(flow, ids[4], must_include=True)  # off-topic, but the researcher insists
    assert must["selected"] is True and must["must_include"] is True
    resp = await flow.post("/literature/selection", {"mode": "top", "k": 2})
    assert resp.json() == {"selected": 3}  # top 2 + the must-include one
    selected = await papers(flow, selected="true", sort="relevance")
    assert {p["id"] for p in selected} == {str(ids[0]), str(ids[2]), str(ids[4])}
    assert await snapshot_ids(flow) == [str(ids[4]), str(ids[0]), str(ids[2])]  # must-include first, then relevance

    await patch(flow, ids[0], selected=False)
    unmarked = await patch(flow, ids[4], selected=False)
    assert unmarked["must_include"] is False
    assert await snapshot_ids(flow) == [str(ids[2])]

    # Only papers the LLM scored ≥ 0.6
    await flow.post("/literature/selection", {"mode": "top", "k": 10, "min_score": 0.6})
    assert {p["title"] for p in await papers(flow, selected="true")} == {PAPERS[0][0], PAPERS[1][0], PAPERS[2][0]}

    # Deleting a selected paper removes it from the selection
    await flow.client.delete(flow.url(f"/literature/papers/{ids[1]}"), headers=flow.headers)
    assert str(ids[1]) not in await snapshot_ids(flow)

    # The literature stage's agent sees the selection in its state
    await flow.run("literature")
    graph_state = (await flow.state())["state"]
    assert graph_state["selected_paper_ids"] == [str(ids[0]), str(ids[2])]

    cleared = await flow.post("/literature/selection", {"mode": "none"})
    assert cleared.json() == {"selected": 0}


async def test_selection_is_locked_once_literature_is_approved(
    flow: Flow, setup: dict[str, Any], db_session: AsyncSession
) -> None:
    ids = await approved_with_papers(flow, setup, db_session)
    await flow.run("literature")
    await flow.act("literature", "approve")
    resp = await flow.client.patch(flow.url(f"/literature/papers/{ids[0]}"), json={"selected": True},
                                   headers=flow.headers)  # fmt: skip
    assert resp.status_code == 409 and "Go back" in resp.json()["detail"]
    assert json.dumps(await snapshot_ids(flow)) == "[]"
