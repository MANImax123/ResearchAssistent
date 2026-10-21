"""Step 4.1: search query planning (L1), discovery (L2) with dedup/filters/source failures, manual adds."""

import uuid
from typing import Any

import pymupdf
import pytest

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.idea.schemas import IdeaPlan
from app.agents.literature.planning import SearchPlan, uncovered
from app.core.config import get_settings
from app.db.models.enums import StageName
from app.services.academic.http import SourceError
from app.services.academic.models import Author, PaperMetadata
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from app.services.storage import Storage
from tests.helpers import Flow
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})

QUERIES = {
    "queries": [
        {"text": "graph neural network binding affinity", "purpose": "Core method", "targets": ["RQ1", "S1"]},
        {"text": "molecular fingerprint virtual screening baseline", "purpose": "Baselines", "targets": ["RQ2"]},
        {"text": "drug candidate ranking evaluation", "purpose": "Evaluation", "targets": ["S2", "S3"]},
    ]
}


def meta(title: str, source: str, **fields: Any) -> PaperMetadata:
    return PaperMetadata(title=title, sources=[source], authors=[Author(name="A. Author")], **fields)


class StubSource:
    """A search client answering from a script: {query text: [papers]} or an exception."""

    def __init__(self, source: str, results: dict[str, list[PaperMetadata]] | Exception | None = None,
                 lookups: dict[str, PaperMetadata | Exception | None] | None = None) -> None:  # fmt: skip
        self.source = source
        self.results = results or {}
        self.lookups = lookups or {}
        self.searches: list[tuple[str, dict[str, Any]]] = []

    async def search(self, query: str, **kwargs: Any) -> list[PaperMetadata]:
        self.searches.append((query, kwargs))
        if isinstance(self.results, Exception):
            raise self.results
        return list(self.results.get(query, []))

    async def lookup(self, kind: str, identifier: str) -> PaperMetadata | None:
        result = self.lookups.get(identifier)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def lit(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Idea agent + planning agent on FakeLLMs; academic sources are stubs the test fills in."""
    fakes = {"idea": FakeLLM(), "planning": FakeLLM()}
    clients: dict[str, Any] = {}

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    def llm(name: str, session_factory: Any) -> Any:
        return lambda: LLMService(fakes[name], SETTINGS, session_factory)

    monkeypatch.setattr(
        registry, "agent_factory", lambda sf: {StageName.IDEA: make_idea_agent(llm("idea", sf), no_notes)}
    )
    monkeypatch.setattr(registry, "llm_factory", lambda sf: llm("planning", sf))
    monkeypatch.setattr(registry, "academic_factory", lambda: clients)
    return {"llm": fakes, "clients": clients}


async def approved_plan(flow: Flow, lit: dict[str, Any]) -> None:
    lit["llm"]["idea"].responses.append(PLAN)
    await flow.run("idea")
    await flow.act("idea", "approve")


async def workspace(flow: Flow) -> dict[str, Any]:
    return (await flow.client.get(flow.url("/literature"), headers=flow.headers)).json()  # type: ignore[no-any-return]


async def papers(flow: Flow, **params: Any) -> dict[str, Any]:
    resp = await flow.client.get(flow.url("/literature/papers"), params=params, headers=flow.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def set_queries(flow: Flow, *texts: str) -> list[dict[str, Any]]:
    body = {"queries": [{"text": t} for t in texts]}
    resp = await flow.client.put(flow.url("/literature/queries"), json=body, headers=flow.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def discover(flow: Flow, **body: Any) -> dict[str, Any]:
    resp = await flow.post("/literature/discover", body)
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


# ─── Gating & query planning ────────────────────────────────────────────


async def test_workspace_needs_an_approved_plan(flow: Flow, lit: dict[str, Any]) -> None:
    ws = await workspace(flow)
    assert ws["editable"] is False and ws["blocked_reason"] == "Approve the research plan (Idea stage) first"
    assert (await flow.post("/literature/queries/generate")).status_code == 409
    assert (await flow.post("/literature/papers/lookup", {"identifier": "10.1/x"})).status_code == 409

    await approved_plan(flow, lit)
    ws = await workspace(flow)
    assert ws["editable"] is True and ws["paper_count"] == 0
    assert [q["id"] for q in ws["research_questions"]] == ["RQ1", "RQ2"] and ws["subtopics"]
    assert {s["name"] for s in ws["sources"]} == {"semantic_scholar", "openalex", "arxiv", "crossref"}


async def test_planning_covers_every_question_and_subtopic(flow: Flow, lit: dict[str, Any]) -> None:
    await approved_plan(flow, lit)
    extra = {"text": "graph attention affinity", "targets": ["RQ1"]}
    partial = {"queries": [*QUERIES["queries"][:2], extra]}  # leaves subtopic S2 uncovered
    lit["llm"]["planning"].responses += [partial, QUERIES]

    started = await flow.post("/literature/queries/generate")
    assert started.status_code == 202 and started.json()["type"] == "literature_plan"
    await flow.finish_jobs()

    calls = lit["llm"]["planning"].calls
    assert len(calls) == 2 and "left these uncovered: S2." in calls[1].prompt
    assert "RQ1" in calls[0].prompt and "S1: " in calls[0].prompt
    queries = (await workspace(flow))["queries"]
    assert [(q["label"], q["origin"]) for q in queries] == [("Q1", "ai"), ("Q2", "ai"), ("Q3", "ai")]
    assert queries[0]["targets"] == ["RQ1", "S1"] and queries[0]["purpose"] == "Core method"


def test_uncovered_lists_missing_targets() -> None:
    plan = IdeaPlan.model_validate(PLAN)
    search = SearchPlan.model_validate({"queries": [{"text": "a b c", "targets": ["rq1", "S1"]}] * 3})
    assert uncovered(search, plan) == ["RQ2", *[f"S{i}" for i in range(2, len(plan.subtopics) + 1)]]


async def test_researcher_edits_queries(flow: Flow, lit: dict[str, Any]) -> None:
    await approved_plan(flow, lit)
    lit["llm"]["planning"].responses.append(QUERIES)
    await flow.post("/literature/queries/generate")
    await flow.finish_jobs()
    q1, q2, _ = (await workspace(flow))["queries"]

    body = {
        "queries": [
            {"id": q2["id"], "text": q2["text"], "purpose": q2["purpose"], "targets": q2["targets"]},  # moved up
            {"id": q1["id"], "text": "graph attention binding affinity", "targets": q1["targets"]},  # edited
            {"text": "PDBbind benchmark"},  # new; Q3 removed
        ]
    }
    saved = (await flow.client.put(flow.url("/literature/queries"), json=body, headers=flow.headers)).json()
    assert [(q["label"], q["text"], q["origin"]) for q in saved] == [
        ("Q1", QUERIES["queries"][1]["text"], "ai"),
        ("Q2", "graph attention binding affinity", "user"),
        ("Q3", "PDBbind benchmark", "user"),
    ]
    too_short = {"queries": [{"text": "x"}]}
    bad = await flow.client.put(flow.url("/literature/queries"), json=too_short, headers=flow.headers)
    assert bad.status_code == 422


# ─── Discovery ──────────────────────────────────────────────────────────


GNN = "graph neural network binding affinity"
FP = "fingerprint baseline"


def stub_sources(lit: dict[str, Any]) -> None:
    lit["clients"].update(
        {
            "openalex": StubSource("openalex", {
                GNN: [meta("GraphDTA: predicting binding affinity", "openalex", doi="10.1093/x", year=2020,
                           openalex_id="W1", venue="Bioinformatics", citation_count=1200),
                      meta("SIGN: structure-aware interactive GNN", "openalex", doi="10.1145/y", year=2021)],
                FP: [meta("Extended-connectivity fingerprints", "openalex", doi="10.1021/z", year=2010)],
            }),
            "crossref": StubSource("crossref", {
                GNN: [meta("GraphDTA: Predicting Binding Affinity.", "crossref", doi="10.1093/X", year=2020)],
                FP: [meta("Extended-Connectivity Fingerprints", "crossref", doi="10.1021/z", year=2010),
                     meta("Fingerprints for virtual screening", "crossref", doi="10.1/fp", year=2018)],
            }),
            "arxiv": StubSource("arxiv", {
                GNN: [meta("SIGN: Structure-aware interactive GNN", "arxiv", arxiv_id="2107.10670", year=2021,
                           venue="arXiv", pdf_url="https://arxiv.org/pdf/2107.10670v1")],
            }),
            "semantic_scholar": StubSource("semantic_scholar", SourceError("semantic_scholar", "rate limited")),
        }
    )  # fmt: skip


async def test_discovery_deduplicates_and_reports_each_source(flow: Flow, lit: dict[str, Any]) -> None:
    """Acceptance (with stub sources): deduplicated papers, each with its source badges."""
    await approved_plan(flow, lit)
    stub_sources(lit)
    q1, q2 = await set_queries(flow, GNN, FP)

    result = await discover(flow, year_from=2005)

    assert result["raw"] == 7 and result["added"] == 4  # GraphDTA ×2, SIGN ×2, ECFP ×2, FP-screening
    s2 = result["sources"]["semantic_scholar"]
    assert s2["found"] == 0 and s2["skipped"] is True and s2["errors"] == ["Q1: rate limited", "Q2: rate limited"]
    assert result["sources"]["openalex"] == {"label": "OpenAlex", "found": 3, "errors": [], "skipped": False}
    assert lit["clients"]["arxiv"].searches[0] == (GNN, {"limit": 10, "year_from": 2005, "year_to": None})

    page = await papers(flow, sort="citations")
    assert page["total"] == 4
    graphdta = page["items"][0]
    assert graphdta["title"] == "GraphDTA: predicting binding affinity" and graphdta["doi"] == "10.1093/x"
    assert graphdta["sources"] == ["openalex", "crossref"] and graphdta["found_by"] == ["Q1"]
    assert graphdta["origin"] == "search" and graphdta["has_identifier"] is True
    sign = next(p for p in page["items"] if p["title"].startswith("SIGN"))
    assert sign["sources"] == ["openalex", "arxiv"] and sign["arxiv_id"] == "2107.10670" and sign["pdf_url"]

    queries = {q["label"]: q for q in (await workspace(flow))["queries"]}
    assert queries["Q1"]["last_found"] == 4 and queries["Q2"]["last_found"] == 3 and queries["Q1"]["last_run_at"]

    # Running again adds nothing new: papers already in the library are matched, not duplicated
    again = await discover(flow, year_from=2005)
    assert again["added"] == 0 and (await papers(flow))["total"] == 4
    assert q1["id"] and q2["id"]


async def test_filters_sources_and_cap(flow: Flow, lit: dict[str, Any]) -> None:
    await approved_plan(flow, lit)
    stub_sources(lit)
    await set_queries(flow, GNN, FP)

    result = await discover(flow, sources=["openalex", "crossref"], year_from=2015, venue="bio", max_new=5)
    assert "arxiv" not in result["sources"] and lit["clients"]["arxiv"].searches == []
    titles = [p["title"] for p in (await papers(flow))["items"]]
    assert titles == ["GraphDTA: predicting binding affinity"]  # only one with a venue containing "bio" from 2015 on
    assert result["filtered_out"] == 3

    capped = await discover(flow, sources=["openalex", "crossref"], max_new=5, per_source=5)
    assert capped["added"] == 3 and capped["over_cap"] == 0

    resp = await flow.post("/literature/discover", {"year_from": 2024, "year_to": 2020})
    assert resp.status_code == 422


async def test_one_discovery_at_a_time_and_needs_queries(flow: Flow, lit: dict[str, Any]) -> None:
    await approved_plan(flow, lit)
    assert (await flow.post("/literature/discover", {})).status_code == 409  # no queries yet
    stub_sources(lit)
    await set_queries(flow, GNN)
    assert (await flow.post("/literature/discover", {})).status_code == 202
    assert (await flow.post("/literature/discover", {})).status_code == 409
    assert (await flow.post("/literature/queries/generate")).status_code == 409
    await flow.finish_jobs()


# ─── Library: list, filter, remove ──────────────────────────────────────


async def test_library_filters_and_remove(flow: Flow, lit: dict[str, Any]) -> None:
    await approved_plan(flow, lit)
    stub_sources(lit)
    await set_queries(flow, GNN, FP)
    await discover(flow)

    assert (await papers(flow, source="arxiv"))["total"] == 1
    assert (await papers(flow, q="fingerprint"))["total"] == 2
    assert (await papers(flow, year_from=2019, year_to=2020))["total"] == 1
    by_year = [p["year"] for p in (await papers(flow, sort="year"))["items"]]
    assert by_year == sorted(by_year, reverse=True)

    target = (await papers(flow, q="SIGN"))["items"][0]
    removed = await flow.client.delete(flow.url(f"/literature/papers/{target['id']}"), headers=flow.headers)
    assert removed.status_code == 204
    assert (await papers(flow))["total"] == 3
    missing = await flow.client.delete(flow.url(f"/literature/papers/{uuid.uuid4()}"), headers=flow.headers)
    assert missing.status_code == 404


# ─── Manual adds: DOI / arXiv id / PDF upload ───────────────────────────


async def test_add_by_doi_and_arxiv_id(flow: Flow, lit: dict[str, Any]) -> None:
    await approved_plan(flow, lit)
    graphdta = meta("GraphDTA", "crossref", doi="10.1093/bioinformatics/btaa921", year=2020)
    autogluon = meta("AutoGluon-Tabular", "arxiv", arxiv_id="2003.06505", year=2020)
    lit["clients"].update(
        {
            "crossref": StubSource("crossref", lookups={"10.1093/bioinformatics/btaa921": graphdta}),
            "openalex": StubSource("openalex", lookups={
                "10.1093/bioinformatics/btaa921": meta("GraphDTA", "openalex", doi="10.1093/bioinformatics/btaa921",
                                                       openalex_id="W3096561213", abstract="Abstract text.")}),
            "arxiv": StubSource("arxiv", lookups={"2003.06505": autogluon}),
            "semantic_scholar": StubSource("semantic_scholar", lookups={
                "10.1093/bioinformatics/btaa921": SourceError("semantic_scholar", "rate limited")}),
        }
    )  # fmt: skip

    first = await flow.post(
        "/literature/papers/lookup", {"identifier": "https://doi.org/10.1093/bioinformatics/btaa921"}
    )
    assert first.status_code == 201, first.text
    added = first.json()
    assert added["created"] is True and added["paper"]["origin"] == "manual"
    assert added["paper"]["openalex_id"] == "W3096561213" and added["paper"]["abstract"] == "Abstract text."
    assert added["paper"]["sources"] == ["manual"]

    again = await flow.post("/literature/papers/lookup", {"identifier": "10.1093/BIOINFORMATICS/btaa921"})
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["paper"]["id"] == added["paper"]["id"]

    arxiv = await flow.post("/literature/papers/lookup", {"identifier": "arXiv:2003.06505v1"})
    assert arxiv.status_code == 201 and arxiv.json()["paper"]["arxiv_id"] == "2003.06505"

    assert (await flow.post("/literature/papers/lookup", {"identifier": "graph networks"})).status_code == 422
    assert (await flow.post("/literature/papers/lookup", {"identifier": "10.9999/none"})).status_code == 404
    lit["clients"].clear()
    lit["clients"]["crossref"] = StubSource("crossref", lookups={"10.1000/down": SourceError("crossref", "HTTP 503")})
    down = await flow.post("/literature/papers/lookup", {"identifier": "10.1000/down"})
    assert down.status_code == 503 and "crossref" in down.json()["detail"]


def pdf_bytes(text: str, title: str = "") -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    if title:
        doc.set_metadata({"title": title})
    return doc.tobytes()  # type: ignore[no-any-return]


async def upload(flow: Flow, data: bytes, **form: str) -> Any:
    return await flow.client.post(
        flow.url("/literature/papers/upload"),
        files={"file": ("paper.pdf", data, "application/pdf")},
        data=form,
        headers=flow.headers,
    )


async def test_upload_pdf(flow: Flow, lit: dict[str, Any], storage: Storage) -> None:
    await approved_plan(flow, lit)
    lit["clients"]["crossref"] = StubSource(
        "crossref", lookups={"10.1093/x": meta("GraphDTA predicting affinity", "crossref", doi="10.1093/x")}
    )

    named = await upload(flow, pdf_bytes("Body text of the paper"), title="My lab's unpublished report")
    assert named.status_code == 201, named.text
    paper = named.json()["paper"]
    assert paper["title"] == "My lab's unpublished report" and paper["origin"] == "upload"
    assert paper["pdf_file_id"] and paper["has_identifier"] is False

    from_pdf = await upload(flow, pdf_bytes("A Study of Graph Kernels for Molecules", title=""))
    assert from_pdf.json()["paper"]["title"] == "A Study of Graph Kernels for Molecules"
    meta_title = await upload(flow, pdf_bytes("x", title="Metadata Title of the Paper"))
    assert meta_title.json()["paper"]["title"] == "Metadata Title of the Paper"

    # With a DOI: metadata from the sources; attached to the library entry if the paper is already there
    await flow.post("/literature/papers/lookup", {"identifier": "10.1093/x"})
    attached = await upload(flow, pdf_bytes("full text"), identifier="10.1093/x")
    assert attached.status_code == 200 and attached.json()["created"] is False
    assert attached.json()["paper"]["pdf_file_id"] and attached.json()["paper"]["doi"] == "10.1093/x"

    not_pdf = await flow.client.post(
        flow.url("/literature/papers/upload"), files={"file": ("x.pdf", b"hello", "application/pdf")},
        headers=flow.headers,
    )  # fmt: skip
    assert not_pdf.status_code in (400, 415, 422)
    assert (await papers(flow))["total"] == 4
