"""Step 4.5: paper profiles with evidence for every field; researcher edits."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.literature.analysis import Passage, ProfileDraft, analyze_paper, ground, select_passages
from app.core.config import get_settings
from app.db.models import Paper, PaperChunk
from app.db.models.enums import PaperOrigin, ParseStatus, StageName
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from tests.helpers import Flow, factory_for
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})

CHUNKS = [
    ("Abstract", "abstract", 1, "We propose SIGN, a structure-aware GNN for protein-ligand binding affinity."),
    ("1 Introduction", "introduction", 1, "Prior 3D CNN methods ignore long-range interactions between atoms."),
    ("4 Model", "methodology", 3, "SIGN uses polar-inspired graph attention layers and pairwise interactive pooling."),
    ("5.1 Settings", "dataset", 6, "We train on PDBbind v2016 and test on the core set and CSAR-HiQ. PyTorch is used."),
    ("5.2 Results", "results", 7, "SIGN reaches RMSE 1.316 and Pearson R 0.797 on the PDBbind core set."),
    ("6 Conclusion", "conclusion", 8, "A limitation is that SIGN needs the 3D complex structure as input."),
    ("A Appendix", "appendix", 11, "Hyper-parameters: 3 layers, hidden size 128."),
]  # fmt: skip


def passages() -> list[Passage]:
    return [Passage(uuid.uuid4(), s, k, p, t) for s, k, p, t in CHUNKS]


def draft(**override: Any) -> dict[str, Any]:
    profile = {
        "problem": {"text": "Predicting protein-ligand binding affinity while capturing long-range interactions.",
                    "evidence": ["C1", "C2"]},
        "method": {"text": "Structure-aware GNN with polar-inspired attention and interactive pooling.",
                   "evidence": ["C3"]},
        "results": {"text": "RMSE 1.316 and Pearson R 0.797 on the PDBbind core set.", "evidence": ["C5"]},
        "datasets": [{"name": "PDBbind v2016", "evidence": ["C4"]}, {"name": "CSAR-HiQ", "evidence": ["C4"]},
                     {"name": "Davis", "evidence": []}, {"name": "pdbbind v2016", "evidence": ["C4"]}],
        "metrics": [{"name": "RMSE", "evidence": ["C5"]}, {"name": "Pearson R", "evidence": ["C5", "C99"]}],
        "limitations": [{"text": "Needs the 3D complex structure as input.", "evidence": ["C6"]}],
        "tech_stack": [{"name": "PyTorch", "evidence": ["C4"]}],
    }  # fmt: skip
    return {**profile, **override}


# ─── Units ──────────────────────────────────────────────────────────────


def test_passages_prioritise_key_sections_within_budget() -> None:
    chosen = select_passages(passages(), budget=40)
    kinds = [p.kind for p in chosen]
    # Abstract, methodology and data come first; introduction and appendix (lowest priority) don't fit
    assert kinds == ["abstract", "methodology", "dataset"]  # and they stay in reading order
    assert len(select_passages(passages())) == 7


def test_values_without_valid_evidence_are_dropped() -> None:
    shown = passages()
    known = {f"C{i}": p.chunk_id for i, p in enumerate(shown, start=1)}
    profile = ground(ProfileDraft.model_validate(draft(problem={"text": "Invented.", "evidence": ["C42"]})), known)

    assert profile.problem is None and "problem" not in profile.evidence  # its only evidence doesn't exist
    assert profile.dataset == ["PDBbind v2016", "CSAR-HiQ"]  # "Davis" had no evidence; a duplicate removed
    assert profile.evidence["dataset"] == [[str(shown[3].chunk_id)], [str(shown[3].chunk_id)]]
    assert profile.evidence["metrics"][1] == [str(shown[4].chunk_id)]  # C99 dropped, C5 kept
    assert profile.evidence["method"] == [str(shown[2].chunk_id)]
    assert profile.dropped == 2


async def test_analysis_prompt_numbers_passages(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    fake = FakeLLM(responses=[draft()])
    profile, shown = await analyze_paper(
        "SIGN", passages(), LLMService(fake, SETTINGS, factory_for(db_session)), project_id
    )
    prompt = fake.calls[0].prompt
    assert prompt.startswith("Paper: SIGN") and "[C1] Abstract (p. 1)" in prompt and "[C3] 4 Model (p. 3)" in prompt
    assert profile.results and profile.run_id and len(shown) == 7


# ─── API flow ───────────────────────────────────────────────────────────


@pytest.fixture
def analyst(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
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


async def parsed_paper(flow: Flow, db_session: AsyncSession, title: str = "SIGN") -> tuple[uuid.UUID, list[uuid.UUID]]:
    paper = Paper(project_id=flow.pid, title=title, origin=PaperOrigin.SEARCH, sources=[], authors=[],
                  selected=True, parse_status=ParseStatus.PARSED, doi=f"10.1000/{uuid.uuid4().hex[:6]}")  # fmt: skip
    db_session.add(paper)
    await db_session.flush()
    ids = []
    for i, (section, kind, page, text) in enumerate(CHUNKS):
        chunk = PaperChunk(paper_id=paper.id, chunk_index=i, section_name=section, section_kind=kind, page=page,
                           text=text)  # fmt: skip
        db_session.add(chunk)
        await db_session.flush()
        ids.append(chunk.id)
    await db_session.commit()
    return paper.id, ids


async def run_analysis(flow: Flow, **body: Any) -> dict[str, Any]:
    resp = await flow.post("/literature/analyze", body)
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


async def test_profile_with_evidence_then_researcher_edits(
    flow: Flow, analyst: FakeLLM, db_session: AsyncSession
) -> None:
    """Acceptance (with fakes): profile fields carry evidence that opens the right passage; editable."""
    await flow.run("idea")
    await flow.act("idea", "approve")
    assert (await flow.post("/literature/analyze", {})).status_code == 409  # nothing parsed yet
    paper_id, chunk_ids = await parsed_paper(flow, db_session)
    analyst.responses.append(draft())

    assert await run_analysis(flow) == {"papers": 1, "analysed": 1}
    url = flow.url(f"/literature/papers/{paper_id}/profile")
    profile = (await flow.client.get(url, headers=flow.headers)).json()

    assert profile["method"]["value"].startswith("Structure-aware GNN")
    method_source = profile["method"]["evidence"][0]
    assert method_source["chunk_id"] == str(chunk_ids[2]) and method_source["section"] == "4 Model"
    assert method_source["page"] == 3 and "polar-inspired" in method_source["text"]
    assert [d["value"] for d in profile["dataset"]] == ["PDBbind v2016", "CSAR-HiQ"]
    assert profile["limitations"][0]["evidence"][0]["section"] == "6 Conclusion"
    assert profile["edited_fields"] == []
    papers = (await flow.client.get(flow.url("/literature/papers"), headers=flow.headers)).json()["items"]
    assert papers[0]["has_profile"] is True
    assert (await flow.client.get(flow.url("/literature"), headers=flow.headers)).json()["profiled_count"] == 1
    assert "Davis" not in str(profile)  # no evidence, never stored

    # The researcher corrects the method, adds a dataset (no evidence) and drops CSAR-HiQ
    other_paper, other_chunks = await parsed_paper(flow, db_session, "Other paper")

    def kept(item: dict[str, Any]) -> dict[str, Any]:
        return {"value": item["value"], "evidence": [e["chunk_id"] for e in item["evidence"]]}

    edit = {
        "problem": profile["problem"]["value"],
        "method": "SIGN: polar-inspired graph attention + pairwise interactive pooling (KDD 2021).",
        "results": profile["results"]["value"],
        "dataset": [
            {"value": "PDBbind v2016", "evidence": [str(chunk_ids[3]), str(other_chunks[3])]},  # foreign id dropped
            {"value": "PDBbind core set", "evidence": []},
        ],
        "metrics": [kept(m) for m in profile["metrics"]],
        "limitations": [{"value": "Needs the 3D complex structure as input.", "evidence": [str(chunk_ids[5])]}],
        "tech_stack": [{"value": "PyTorch", "evidence": [str(chunk_ids[3])]}],
    }  # fmt: skip
    saved = await flow.client.put(url, json=edit, headers=flow.headers)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["edited_fields"] == ["dataset", "method"]
    assert body["method"]["value"].endswith("(KDD 2021).") and body["method"]["evidence"][0]["section"] == "4 Model"
    assert [len(d["evidence"]) for d in body["dataset"]] == [1, 0]

    # Analysing again skips papers with a profile unless forced; forcing replaces the edits
    assert (await flow.post("/literature/analyze", {"paper_ids": [str(uuid.uuid4())]})).status_code == 409
    analyst.responses += [draft(), draft()]
    assert (await run_analysis(flow))["papers"] == 1  # only the other paper
    assert (await run_analysis(flow, force=True))["papers"] == 2
    again = (await flow.client.get(url, headers=flow.headers)).json()
    assert again["edited_fields"] == [] and len(again["dataset"]) == 2
    assert other_paper


async def test_unanalysed_paper_has_no_profile(flow: Flow, analyst: FakeLLM, db_session: AsyncSession) -> None:
    paper_id, _ = await parsed_paper(flow, db_session)
    resp = await flow.client.get(flow.url(f"/literature/papers/{paper_id}/profile"), headers=flow.headers)
    assert resp.status_code == 200 and resp.json() is None  # not analysed yet: a normal state, not an error
    missing = await flow.client.get(flow.url(f"/literature/papers/{uuid.uuid4()}/profile"), headers=flow.headers)
    assert missing.status_code == 404
