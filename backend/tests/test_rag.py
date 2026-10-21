"""Step 4.4: chunk embeddings, project-scoped retrieval with MMR and section filter, grounded answers."""

import re
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.literature.rag import answer, check_citations
from app.core.config import get_settings
from app.db.models import Paper, PaperChunk, Project, User
from app.db.models.enums import PaperOrigin
from app.services.embeddings import EmbeddingService, FakeEmbeddings
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from app.services.retrieval import Retrieved, chunk_counts, embed_pending_chunks, mmr, retrieve
from tests.helpers import Flow, factory_for

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})
EMBEDDINGS = EmbeddingService(FakeEmbeddings(), SETTINGS)

PASSAGES = [
    ("GraphDTA", "dataset", "4.1 Datasets", "We evaluate on the Davis and KIBA kinase binding affinity datasets."),
    ("GraphDTA", "methodology", "3 Method", "Drugs are graphs; a graph convolutional network encodes each molecule."),
    ("SIGN", "dataset", "5.1 Experiment Settings", "We use PDBbind core and CSAR-HiQ as binding affinity datasets."),
    ("SIGN", "results", "5.2 Results", "SIGN achieves the lowest RMSE on the PDBbind core set."),
    ("Wheat study", "dataset", "2 Data", "Soil nitrogen measurements were collected from wheat fields."),
]  # fmt: skip


async def add_passages(session: AsyncSession, project_id: uuid.UUID, rows: list[tuple[str, str, str, str]]) -> None:
    papers: dict[str, Paper] = {}
    for i, (title, kind, section, text) in enumerate(rows):
        if title not in papers:
            papers[title] = Paper(project_id=project_id, title=title, origin=PaperOrigin.SEARCH, sources=[],
                                  authors=[], year=2020, doi=f"10.1000/{uuid.uuid4().hex[:8]}")  # fmt: skip
            session.add(papers[title])
            await session.flush()
        session.add(PaperChunk(paper_id=papers[title].id, chunk_index=i, section_name=section, section_kind=kind,
                               text=text, page=i + 1))  # fmt: skip
    await session.commit()


async def other_project(session: AsyncSession) -> uuid.UUID:
    owner = User(email=f"other-{uuid.uuid4().hex[:6]}@example.com", name="Other", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(owner_id=owner.id, title="Someone else's project")
    session.add(project)
    await session.flush()
    return project.id


# ─── Units ──────────────────────────────────────────────────────────────


def source(n: int) -> Retrieved:
    return Retrieved(uuid.uuid4(), uuid.uuid4(), f"Paper {n}", 2020, "Results", "results", n, f"text {n}", 0.8)


def test_citations_outside_the_retrieved_set_are_removed() -> None:
    sources = [source(1), source(2), source(3)]
    text, citations, removed = check_citations(
        "Davis and KIBA are used [S2]. PDBbind too [S7]. Also [S2][S1].", sources
    )

    assert text == "Davis and KIBA are used [S2]. PDBbind too. Also [S2][S1]."
    assert [c.marker for c in citations] == ["S2", "S1"] and citations[0].source is sources[1]
    assert removed == ["[S7]"]


def test_mmr_prefers_diverse_passages() -> None:
    query = [1.0, 0.0, 0.0]
    near = [0.99, 0.141, 0.0]
    duplicate = [0.99, 0.141, 0.0]
    other = [0.8, 0.0, 0.6]
    picked = mmr(query, [(near, source(1)), (duplicate, source(2)), (other, source(3))], k=2, lam=0.5)
    assert [p.paper_title for p in picked] == ["Paper 1", "Paper 3"]


# ─── Indexing & retrieval ───────────────────────────────────────────────


async def test_retrieval_stays_in_the_project_and_honours_filters(
    db_session: AsyncSession, project_id: uuid.UUID
) -> None:
    """Spec test: retrieval returns only same-project chunks."""
    foreign = await other_project(db_session)
    await add_passages(db_session, project_id, PASSAGES)
    await add_passages(db_session, foreign, [("Leak", "dataset", "1 Data", "Binding affinity datasets Davis KIBA.")])
    factory = factory_for(db_session)

    assert await chunk_counts(db_session, project_id) == (0, 5)
    assert await embed_pending_chunks(project_id, factory, EMBEDDINGS) == 5
    assert await embed_pending_chunks(project_id, factory, EMBEDDINGS) == 0  # nothing left to do
    await embed_pending_chunks(foreign, factory, EMBEDDINGS)
    assert await chunk_counts(db_session, project_id) == (5, 0)

    query = await EMBEDDINGS.embed_query("Which binding affinity datasets are used?")
    found = await retrieve(db_session, project_id, query, k=3)
    assert len(found) == 3 and all(r.paper_title != "Leak" for r in found)
    assert found[0].kind == "dataset" and found[0].page is not None and 0 < found[0].similarity <= 1

    only_data = await retrieve(db_session, project_id, query, k=10, section_kinds=["dataset"])
    assert {r.section for r in only_data} == {"4.1 Datasets", "5.1 Experiment Settings", "2 Data"}
    sign = next(r.paper_id for r in found if r.paper_title == "SIGN")
    assert {r.paper_title for r in await retrieve(db_session, project_id, query, paper_ids=[sign])} == {"SIGN"}


# ─── Answers ────────────────────────────────────────────────────────────


async def test_answer_is_grounded_and_repaired_when_uncited(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    """Spec test: the answer cites only retrieved chunks."""
    sources = [source(1), source(2), source(3)]
    fake = FakeLLM(
        responses=[
            {"answer": "Davis, KIBA and PDBbind are used.", "insufficient": False},  # no citations: sent back
            {"answer": "GraphDTA uses Davis and KIBA [S1]; SIGN uses PDBbind [S3] and [S9].", "insufficient": False},
        ]
    )
    result = await answer("Which datasets?", sources, LLMService(fake, SETTINGS, factory_for(db_session)), project_id)

    assert result.text == "GraphDTA uses Davis and KIBA [S1]; SIGN uses PDBbind [S3] and."
    assert [c.source for c in result.citations] == [sources[0], sources[2]] and result.removed_markers == ["[S9]"]
    assert "cited no passage" in fake.calls[1].prompt
    assert "[S1] Paper 1 (2020) — Results, p. 1" in fake.calls[0].prompt


async def test_insufficient_passages_are_said_plainly(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    fake = FakeLLM(responses=[{"answer": "The passages don't mention any wet-lab validation.", "insufficient": True}])
    llm = LLMService(fake, SETTINGS, factory_for(db_session))
    result = await answer("Was it validated in the lab?", [source(1)], llm, project_id)
    assert result.insufficient and result.citations == []
    empty = await answer("Anything?", [], llm, project_id)
    assert empty.insufficient and len(fake.calls) == 1  # no passages: the LLM isn't asked


# ─── API ────────────────────────────────────────────────────────────────


@pytest.fixture
def rag_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(registry, "embeddings_factory", lambda: EMBEDDINGS)
    monkeypatch.setattr(registry, "llm_factory", lambda sf: lambda: LLMService(fake, SETTINGS, sf))
    return fake


async def test_ask_the_literature(flow: Flow, rag_llm: FakeLLM, db_session: AsyncSession) -> None:
    """Acceptance (with fakes): "Which datasets are used…?" → grounded answer with clickable sources."""
    ask = {"question": "Which datasets are used for binding affinity prediction?", "section_kinds": ["dataset"]}
    assert (await flow.post("/literature/ask", ask)).status_code == 409  # nothing indexed yet

    await add_passages(db_session, flow.pid, PASSAGES)
    ws = (await flow.client.get(flow.url("/literature"), headers=flow.headers)).json()
    assert (ws["indexed_chunks"], ws["pending_chunks"]) == (0, 5)
    started = await flow.post("/literature/index")
    assert started.status_code == 409  # Idea not approved: the library can't change yet

    await embed_pending_chunks(flow.pid, factory_for(db_session), EMBEDDINGS)

    def reply(prompt: str) -> dict[str, Any]:
        cited = " ".join(f"[{m}]" for m in sorted(set(re.findall(r"\[(S\d+)\]", prompt))))
        return {"answer": f"The papers use Davis, KIBA, PDBbind and CSAR-HiQ {cited}.", "insufficient": False}

    rag_llm.responses.append(reply)
    resp = await flow.post("/literature/ask", ask)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retrieved"] == 3 and not body["insufficient"]  # only the three dataset passages
    assert [c["marker"] for c in body["citations"]] == ["S1", "S2", "S3"]
    assert {c["section"] for c in body["citations"]} == {"4.1 Datasets", "5.1 Experiment Settings", "2 Data"}
    cited = body["citations"][0]
    assert cited["paper_id"] and cited["page"] and cited["text"] and cited["kind"] == "dataset"
    assert all(f"[{c['marker']}]" in body["answer"] for c in body["citations"])

    bad = await flow.post("/literature/ask", {"question": "?"})
    assert bad.status_code == 422
