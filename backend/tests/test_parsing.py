"""Step 4.3: PDF acquisition (legal OA only), GROBID TEI parsing, PyMuPDF fallback, chunking, parse flow."""

import uuid
from pathlib import Path
from typing import Any

import httpx
import pymupdf
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.literature.parsing import Tools
from app.core.config import get_settings
from app.db.models import File, Job, Paper, PaperChunk
from app.db.models.enums import JobStatus, PaperOrigin, ParseStatus, StageName
from app.services.embeddings import EmbeddingService, FakeEmbeddings
from app.services.grobid import GrobidClient, GrobidError, parse_tei
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from app.services.pdf import ParsedDocument, Section, chunk_document, classify, parse_with_pymupdf
from app.services.pdf_fetch import PdfFetcher
from app.services.storage import Storage
from tests.helpers import Flow, factory_for
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})
TEI = (Path(__file__).parent / "fixtures" / "grobid" / "sign.tei.xml").read_text(encoding="utf-8")

LOREM = (
    "Graph neural networks learn molecular representations directly from atoms and bonds and are compared with "
    "fingerprint baselines on public binding affinity benchmarks across several protein families. "
)


def make_pdf(*, scanned: bool = False) -> bytes:
    """A small two-page paper with numbered headings, a caption and references."""
    doc = pymupdf.open()
    lines = (
        []
        if scanned
        else ["Abstract", LOREM * 2, "1 Introduction", LOREM * 4, "2 Method", LOREM * 5,
              "Figure 1: Overview of the model.", "3 Results", LOREM * 4, "Table 1: Accuracy on PDBbind.",
              "4 Conclusion", "A limitation of this study is the small number of targets.", "References",
              "[1] A. Author. A paper. 2020.", "[2] B. Author. Another paper. 2021."]
    )  # fmt: skip
    page = doc.new_page()
    y = 50
    for line in lines:
        for start in range(0, len(line), 95):
            if y > 780:
                page, y = doc.new_page(), 50
            page.insert_text((40, y), line[start : start + 95], fontsize=9)
            y += 12
    if scanned:
        page.draw_rect(pymupdf.Rect(50, 50, 300, 300), color=(0, 0, 0))
    return doc.tobytes()  # type: ignore[no-any-return]


# ─── GROBID TEI (recorded from GROBID 0.8.1 on arXiv:2107.10670) ─────────


def test_tei_sections_kinds_captions() -> None:
    doc = parse_tei(TEI)

    assert doc.parser == "grobid" and doc.page_count == 11 and doc.reference_count == 48
    assert doc.title and doc.title.startswith("Structure-aware Interactive Graph Neural Networks")
    assert doc.abstract and doc.abstract.startswith("Drug discovery often relies")
    by_number = {s.number: s for s in doc.sections if s.number}
    assert (by_number["1"].kind, by_number["2"].kind, by_number["4.1"].kind) == (
        "introduction", "related_work", "methodology"
    )  # fmt: skip
    assert by_number["5.1"].kind == "dataset" and by_number["5.2.1"].kind == "results"
    assert by_number["6"].kind == "conclusion" and by_number["4.2"].page == 4
    assert not any(len(s.title) <= 3 for s in doc.sections)  # maths glyphs merged back, not sections
    assert {s.kind for s in doc.sections if s.title.startswith("A.")} == {"appendix"}
    labels = [c.label for c in doc.captions]
    assert labels[:4] == ["Figure 1", "Figure 2", "Figure 3", "Figure 4"] and "Table 2" in labels
    assert not any(c.text.startswith(("Definition", ")")) for c in doc.captions)  # not real figures
    table2 = next(c for c in doc.captions if c.label == "Table 2")
    assert table2.kind == "table" and table2.text.startswith("Performance comparison on PDBbind")


def test_subsections_inherit_from_nearest_ancestor_with_trailing_dots() -> None:
    """Live case: GROBID numbered a paper's sections "2.", "2.3.", "2.3.1." (trailing dots)."""
    divs = "".join(
        f'<div><head n="{n}">{title}</head><p>{LOREM}</p></div>'
        for n, title in [("2.", "Methods"), ("2.3.", "PointNet architecture and training"),
                         ("2.3.1.", "The neural network structures"), ("2.5", "Features for downstream use")]
    )  # fmt: skip
    tei = f'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>{divs}</body></text></TEI>'
    sections = parse_tei(tei).sections
    assert [(s.number, s.kind) for s in sections] == [
        ("2", "methodology"),
        ("2.3", "methodology"),
        ("2.3.1", "methodology"),
        ("2.5", "methodology"),
    ]


@pytest.mark.parametrize(
    ("heading", "kind"),
    [("Introduction", "introduction"), ("I. INTRODUCTION", "introduction"), ("3.2 Datasets", "dataset"),
     ("Experimental Setup", "dataset"), ("Results and Discussion", "results"), ("Discussion", "discussion"),
     ("Limitations and Future Work", "limitations"), ("Conclusions", "conclusion"), ("Appendix B", "appendix"),
     ("A.4 Baseline Descriptions", "appendix"), ("A novel model", "methodology"), ("Acknowledgments", "other")],
)  # fmt: skip
def test_section_classification(heading: str, kind: str) -> None:
    assert classify(heading) == kind


def test_limitations_are_the_papers_own() -> None:
    doc = ParsedDocument(
        "grobid", "T", None,
        [Section("Related work", "To overcome these limitations of prior methods, we propose X.", kind="related_work"),
         Section("Conclusion", "To address the limitations of existing models we built X. One limitation of our "
                 "study is the small number of protein targets considered.", number="6", kind="conclusion", page=9)],
    )  # fmt: skip
    assert doc.limitation_mentions() == [
        {"section": "6 Conclusion", "text": "One limitation of our study is the small number of protein targets "
                                            "considered.", "page": 9}
    ]  # fmt: skip


# ─── PyMuPDF fallback & chunking ────────────────────────────────────────


def test_pymupdf_fallback_finds_sections_captions_references() -> None:
    doc = parse_with_pymupdf(make_pdf())

    assert doc.parser == "pymupdf" and doc.page_count == 1
    assert [(s.heading, s.kind) for s in doc.sections] == [
        ("1 Introduction", "introduction"), ("2 Method", "methodology"), ("3 Results", "results"),
        ("4 Conclusion", "conclusion"),
    ]  # fmt: skip
    assert doc.abstract and doc.abstract.startswith("Graph neural networks learn")
    assert [c.label for c in doc.captions] == ["Figure 1", "Table 1"] and doc.reference_count == 2
    assert doc.limitation_mentions()[0]["section"] == "4 Conclusion"
    assert "[1] A. Author" not in " ".join(s.text for s in doc.sections)  # references aren't body text


def test_chunks_stay_within_sections_with_overlap() -> None:
    words = [f"w{i}" for i in range(1300)]
    doc = ParsedDocument("grobid", None, "Short abstract.", [
        Section("Method", " ".join(words), number="2", page=3), Section("Results", "Small section.", number="3"),
    ])  # fmt: skip
    chunks = chunk_document(doc)

    assert chunks[0].section == "Abstract"
    method = [c for c in chunks if c.section == "2 Method"]
    assert all(len(c.text.split()) <= 600 for c in method) and len(method) == 3
    first, second = method[0].text.split(), method[1].text.split()
    assert first[-60:] == second[:60]  # ~80-token overlap
    assert method[-1].text.split()[-1] == "w1299" and all(c.page == 3 for c in method)
    assert chunks[-1].section == "3 Results" and chunks[-1].text == "Small section."


# ─── PDF acquisition ────────────────────────────────────────────────────

PDF = make_pdf()


def fetcher(routes: dict[str, httpx.Response], email: str = "") -> tuple[PdfFetcher, list[str]]:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        seen.append(url)
        return routes.get(url, httpx.Response(404))

    return PdfFetcher(unpaywall_email=email, transport=httpx.MockTransport(handle), max_bytes=10_000_000), seen


async def test_arxiv_first_then_open_access_then_unpaywall() -> None:
    html = httpx.Response(200, text="<html>landing page</html>", headers={"content-type": "text/html"})
    unpaywall = httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://repo.example.org/p.pdf"}})
    f, seen = fetcher(
        {
            "https://arxiv.org/pdf/2107.10670": httpx.Response(404),
            "https://publisher.example.com/oa": html,
            "https://api.unpaywall.org/v2/10.1/x": unpaywall,
            "https://repo.example.org/p.pdf": httpx.Response(200, content=PDF),
        },
        email="lab@example.org",
    )
    result = await f.fetch(arxiv_id="2107.10670", pdf_url="https://publisher.example.com/oa", doi="10.1/x")

    assert result.data == PDF and result.source == "unpaywall" and result.url == "https://repo.example.org/p.pdf"
    assert result.attempts == ["arxiv: HTTP 404", "open_access: not a PDF (text/html)"]
    assert seen[:3] == [  # Unpaywall is only asked once the known locations failed
        "https://arxiv.org/pdf/2107.10670",
        "https://publisher.example.com/oa",
        "https://api.unpaywall.org/v2/10.1/x",
    ]


async def test_no_unpaywall_without_email_and_nothing_found() -> None:
    f, seen = fetcher({})
    result = await f.fetch(arxiv_id=None, pdf_url=None, doi="10.1/x")
    assert result.data is None and result.attempts == [] and seen == []


async def test_oversized_download_is_refused() -> None:
    big = httpx.Response(200, content=b"%PDF-" + b"0" * 2000)
    f, _ = fetcher({"https://arxiv.org/pdf/1": big})
    f._max_bytes = 1000
    result = await f.fetch(arxiv_id="1", pdf_url=None, doi=None)
    assert result.data is None and result.attempts[0].startswith("arxiv: too large")


async def test_grobid_client_posts_the_pdf_and_reports_errors() -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=TEI) if len(calls) == 1 else httpx.Response(503, text="busy")

    client = GrobidClient("http://grobid:8070", transport=httpx.MockTransport(handle))
    assert (await client.process(PDF)).startswith("<?xml")
    assert calls[0].url.path == "/api/processFulltextDocument" and b"teiCoordinates" in calls[0].content
    with pytest.raises(GrobidError, match="HTTP 503"):
        await client.process(PDF)


# ─── Parse flow through the API ─────────────────────────────────────────


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch, storage: Storage) -> dict[str, Any]:
    state: dict[str, Any] = {"grobid_fails": False, "grobid_calls": 0}

    def grobid(request: httpx.Request) -> httpx.Response:
        state["grobid_calls"] += 1
        return httpx.Response(500, text="crash") if state["grobid_fails"] else httpx.Response(200, text=TEI)

    def web(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://arxiv.org/pdf/2107.10670":
            return httpx.Response(200, content=PDF)
        if url.startswith("https://publisher.example.com"):
            return httpx.Response(200, text="<html>Sign in</html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    built = Tools(
        storage=storage,
        fetcher=PdfFetcher(transport=httpx.MockTransport(web)),
        grobid=GrobidClient("http://grobid:8070", transport=httpx.MockTransport(grobid)),
    )
    monkeypatch.setattr(registry, "parsing_factory", lambda: built)
    monkeypatch.setattr(registry, "embeddings_factory", lambda: EmbeddingService(FakeEmbeddings(), SETTINGS))
    fake = FakeLLM(responses=[PLAN])
    monkeypatch.setattr(
        registry, "agent_factory",
        lambda sf: {StageName.IDEA: make_idea_agent(lambda: LLMService(fake, SETTINGS, sf), _no_notes)},
    )  # fmt: skip
    return state


async def _no_notes(_: uuid.UUID) -> str:
    return ""


async def with_papers(flow: Flow, db_session: AsyncSession) -> dict[str, uuid.UUID]:
    await flow.run("idea")
    await flow.act("idea", "approve")
    specs = {
        "arxiv": {"title": "SIGN", "arxiv_id": "2107.10670", "selected": True},
        "paywalled": {"title": "Closed paper", "doi": "10.1/closed", "pdf_url": "https://publisher.example.com/x",
                      "selected": True},
        "nothing": {"title": "No OA at all", "doi": "10.1/none", "selected": True},
        "unselected": {"title": "Not chosen", "arxiv_id": "2107.10670", "selected": False},
    }  # fmt: skip
    ids = {}
    for key, fields in specs.items():
        paper = Paper(project_id=flow.pid, origin=PaperOrigin.SEARCH, sources=["openalex"], authors=[], **fields)
        db_session.add(paper)
        await db_session.flush()
        ids[key] = paper.id
    await db_session.commit()
    return ids


async def parse(flow: Flow, **body: Any) -> dict[str, Any]:
    resp = await flow.post("/literature/parse", body)
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


async def status_of(flow: Flow) -> dict[str, tuple[str, str | None]]:
    items = (await flow.client.get(flow.url("/literature/papers"), headers=flow.headers)).json()["items"]
    return {p["title"]: (p["parse_status"], p["parse_error"]) for p in items}


async def test_parse_selected_papers(
    flow: Flow, tools: dict[str, Any], db_session: AsyncSession, storage: Storage
) -> None:
    """Acceptance: parse status for selected papers; parsed sections viewable; no OA PDF → upload needed."""
    ids = await with_papers(flow, db_session)

    result = await parse(flow)

    chunk_total = len(chunk_document(parse_tei(TEI)))
    assert result == {"papers": 3, "parsed": 1, "upload_needed": 2, "indexed": chunk_total}  # indexed right away
    statuses = await status_of(flow)
    assert statuses["SIGN"] == ("parsed", None)
    assert statuses["Not chosen"][0] == "not_started"  # not selected: not parsed
    closed = statuses["Closed paper"]
    assert closed[0] == "upload_needed" and "open_access: not a PDF (text/html)" in (closed[1] or "")
    assert statuses["No OA at all"][1] == "No open-access PDF is known for this paper. Upload the PDF to parse it."

    doc = (await flow.client.get(flow.url(f"/literature/papers/{ids['arxiv']}/document"), headers=flow.headers)).json()
    assert doc["parser"] == "grobid" and doc["pdf_source"] == "arxiv" and doc["page_count"] == 11
    assert doc["pdf_url"] == "https://arxiv.org/pdf/2107.10670"
    kinds = {s["kind"] for s in doc["sections"]}
    assert {"introduction", "methodology", "dataset", "results", "conclusion"} <= kinds
    assert any(c["label"] == "Table 2" for c in doc["captions"]) and doc["chunk_count"] > 20
    by_index = select(PaperChunk).where(PaperChunk.paper_id == ids["arxiv"]).order_by(PaperChunk.chunk_index)
    chunks = list(await db_session.scalars(by_index))
    assert chunks[0].section_name == "Abstract" and chunks[0].section_kind == "abstract"
    assert all(c.embedding is not None for c in chunks)  # searchable straight after parsing
    stored = await db_session.scalar(select(File).where(File.project_id == flow.pid))
    assert stored is not None and stored.kind.value == "paper_pdf" and stored.uploaded_by is None
    assert await storage.get_bytes(stored.object_key) == PDF
    ws = (await flow.client.get(flow.url("/literature"), headers=flow.headers)).json()
    assert ws["parse_counts"] == {"parsed": 1, "upload_needed": 2}
    missing = await flow.client.get(flow.url(f"/literature/papers/{ids['nothing']}/document"), headers=flow.headers)
    assert missing.status_code == 404


async def test_upload_needed_then_pdf_uploaded_and_fallback_parser(
    flow: Flow, tools: dict[str, Any], db_session: AsyncSession
) -> None:
    ids = await with_papers(flow, db_session)
    await parse(flow)

    upload = await flow.client.post(
        flow.url(f"/literature/papers/{ids['nothing']}/pdf"),
        files={"file": ("paper.pdf", PDF, "application/pdf")},
        headers=flow.headers,
    )
    assert upload.status_code == 200 and upload.json()["parse_status"] == "not_started"
    tools["grobid_fails"] = True  # GROBID down: PyMuPDF takes over

    result = await parse(flow, paper_ids=[str(ids["nothing"])])
    assert result["parsed"] == 1 and result["indexed"] > 0
    doc = (
        await flow.client.get(flow.url(f"/literature/papers/{ids['nothing']}/document"), headers=flow.headers)
    ).json()
    assert doc["parser"] == "pymupdf" and doc["pdf_source"] == "upload"
    assert [s["heading"] for s in doc["sections"]][:2] == ["1 Introduction", "2 Method"]
    assert doc["limitations"][0]["text"].startswith("A limitation of this study")

    # Default run: only selected papers not parsed yet; force re-parses the parsed ones too
    again = await parse(flow)
    assert again == {"papers": 1, "upload_needed": 1}  # just the paywalled one
    forced = await parse(flow, force=True)
    assert forced["papers"] == 3
    assert (await flow.post("/literature/parse", {"paper_ids": [str(uuid.uuid4())]})).status_code == 409


async def test_scanned_pdf_fails_clearly(flow: Flow, tools: dict[str, Any], db_session: AsyncSession) -> None:
    ids = await with_papers(flow, db_session)
    tools["grobid_fails"] = True
    await flow.client.post(
        flow.url(f"/literature/papers/{ids['nothing']}/pdf"),
        files={"file": ("scan.pdf", make_pdf(scanned=True), "application/pdf")},
        headers=flow.headers,
    )
    await parse(flow, paper_ids=[str(ids["nothing"])])
    status, error = (await status_of(flow))["No OA at all"]
    assert status == "failed" and "scanned" in (error or "")


async def test_interrupted_parse_job_releases_its_papers(flow: Flow, db_session: AsyncSession) -> None:
    from app.workers.recovery import INTERRUPTED, recover_interrupted_jobs

    paper = Paper(project_id=flow.pid, title="Stuck", origin=PaperOrigin.SEARCH, parse_status=ParseStatus.PARSING,
                  sources=[], authors=[])  # fmt: skip
    job = Job(id=uuid.uuid4(), type="literature_parse", project_id=flow.pid, status=JobStatus.RUNNING,
              celery_task_id="dead")  # fmt: skip
    db_session.add_all([paper, job])
    await db_session.flush()

    await recover_interrupted_jobs(factory_for(db_session), active_task_ids=set())

    await db_session.execute(update(Paper).where(Paper.id == paper.id).values())  # no-op to flush state
    row = await db_session.get(Paper, paper.id, populate_existing=True)
    assert row is not None and row.parse_status == ParseStatus.FAILED and row.parse_error == INTERRUPTED
