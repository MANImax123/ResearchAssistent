"""Step 4.1: academic API clients (recorded responses), retries/caching, identifiers, deduplication."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.academic.arxiv import ArxivClient, build_query
from app.services.academic.crossref import CrossrefClient
from app.services.academic.dedup import Deduplicator, merge
from app.services.academic.http import MemoryCache, SourceError
from app.services.academic.models import (
    Author,
    PaperMetadata,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
    parse_identifier,
)
from app.services.academic.openalex import OpenAlexClient, abstract_from_index
from app.services.academic.semantic_scholar import SemanticScholarClient
from app.services.academic.sources import lookup

FIXTURES = Path(__file__).parent / "fixtures" / "academic"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Recorder:
    """httpx transport that answers from a script and records every request."""

    def __init__(self, *responses: httpx.Response | str) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return response if isinstance(response, httpx.Response) else httpx.Response(200, text=response)


async def no_sleep(_: float) -> None:
    return None


def client(cls: type, recorder: Recorder, **kwargs: Any) -> Any:
    return cls(transport=httpx.MockTransport(recorder), sleep=no_sleep, **kwargs)


# ─── Normalisation from recorded responses ──────────────────────────────


async def test_openalex_search_normalises_works() -> None:
    rec = Recorder(fixture("openalex_search.json"))
    oa = client(OpenAlexClient, rec, email="me@example.org")

    papers = await oa.search("graph neural network binding affinity", limit=3, year_from=2019, year_to=2024)

    params = rec.requests[0].url.params
    assert params["mailto"] == "me@example.org" and params["per-page"] == "3"
    assert "from_publication_date:2019-01-01" in params["filter"]
    assert "to_publication_date:2024-12-31" in params["filter"]
    graphdta = papers[0]
    assert graphdta.title.startswith("GraphDTA: predicting drug–target binding affinity")
    assert (graphdta.doi, graphdta.openalex_id) == ("10.1093/bioinformatics/btaa921", "W3096561213")
    assert graphdta.year == 2020
    assert graphdta.venue == "Bioinformatics" and graphdta.citation_count == 1232
    assert [a.name for a in graphdta.authors][:2] == ["Thin Nguyen", "Hang Le"]  # repeated authors removed
    assert len(graphdta.authors) == len({a.name for a in graphdta.authors})
    assert graphdta.abstract and graphdta.abstract.startswith("SUMMARY: The development of new drugs")
    assert papers[1].pdf_url and papers[1].pdf_url.endswith(".pdf")  # bioRxiv preprint is open access
    assert all(p.sources == ["openalex"] for p in papers)


def test_openalex_abstract_is_rebuilt_from_inverted_index() -> None:
    assert abstract_from_index({"graphs": [1], "Learning": [0], "molecules.": [3], "on": [2]}) == (
        "Learning graphs on molecules."
    )


async def test_crossref_search_and_doi_lookup() -> None:
    rec = Recorder(fixture("crossref_search.json"), fixture("crossref_work.json"))
    cr = client(CrossrefClient, rec, email="me@example.org")

    papers = await cr.search("graph neural network binding affinity", limit=4, year_from=2019)
    assert "mailto:me@example.org" in rec.requests[0].headers["user-agent"]
    assert "type:journal-article" in rec.requests[0].url.params["filter"]
    first = papers[0]
    assert first.title.startswith("GraphscoreDTA") and first.doi == "10.1093/bioinformatics/btad340"
    assert first.year == 2023 and first.venue == "Bioinformatics"
    assert first.abstract and "<jats" not in first.abstract and first.abstract.startswith("Motivation")

    paper = await cr.lookup("doi", "10.1093/bioinformatics/btaa921")
    assert rec.requests[1].url.path == "/works/10.1093/bioinformatics/btaa921"
    assert paper is not None and paper.title.startswith("GraphDTA") and paper.citation_count == 1238


def test_crossref_skips_supplementary_components() -> None:
    from app.services.academic.crossref import parse_work

    assert parse_work({"title": ["Supplementary table"], "type": "component", "DOI": "10.1/x.s001"}) is None


async def test_arxiv_search_and_id_lookup() -> None:
    rec = Recorder(fixture("arxiv_search.xml"), fixture("arxiv_id.xml"))
    ax = client(ArxivClient, rec)

    papers = await ax.search("graph neural network for binding affinity", limit=3, year_from=2020)
    query = rec.requests[0].url.params["search_query"]
    assert query.startswith("all:graph AND all:neural AND all:network AND all:binding AND all:affinity")
    assert "submittedDate:[202001010000 TO 210012312359]" in query
    first = papers[0]
    assert first.arxiv_id == "2008.12473" and first.doi == "10.1371/journal.pcbi.1009284"
    assert first.pdf_url == "https://arxiv.org/pdf/2008.12473v1" and first.venue == "arXiv"
    assert papers[2].venue == "PMLR 267, 2025"  # journal_ref beats "arXiv"

    paper = await ax.lookup("arxiv", "2003.06505")
    assert rec.requests[1].url.params["id_list"] == "2003.06505"
    assert paper is not None and paper.title.startswith("AutoGluon-Tabular") and paper.year == 2020


def test_arxiv_query_keeps_phrases_and_drops_stopwords() -> None:
    assert build_query('"message passing" for the molecules', None, None) == 'all:"message passing" AND all:molecules'


async def test_semantic_scholar_search_and_lookup() -> None:
    recorded = fixture("s2_search.json")
    # The paper endpoint returns the same object as a search hit
    rec = Recorder(recorded, json.dumps(json.loads(recorded)["data"][0]))
    s2 = client(SemanticScholarClient, rec, api_key="k")

    papers = await s2.search("graph neural network binding affinity", limit=4, year_from=2019, year_to=2025)
    assert rec.requests[0].headers["x-api-key"] == "k" and rec.requests[0].url.params["year"] == "2019-2025"
    assert len(papers) == 4 and all(p.s2_id and p.sources == ["semantic_scholar"] for p in papers)
    gnnseq = papers[0]
    assert gnnseq.title.startswith("GNNSeq: A Sequence-Based Graph Neural Network")
    assert (gnnseq.doi, gnnseq.year, gnnseq.venue) == ("10.3390/ph18030329", 2025, "Pharmaceuticals")
    assert gnnseq.s2_id == "bc89f5c056abfc791bd5606dd7a739f064fc087a" and gnnseq.abstract
    assert gnnseq.pdf_url == "https://doi.org/10.3390/ph18030329"
    assert papers[1].pdf_url is None  # closed access: S2 reports an empty url

    paper = await s2.lookup("doi", "10.3390/ph18030329")
    assert rec.requests[1].url.path.endswith("/paper/DOI:10.3390/ph18030329")
    assert paper is not None and paper.doi == "10.3390/ph18030329"


# ─── Retries, rate limits, caching ──────────────────────────────────────


async def test_rate_limit_is_retried_then_succeeds() -> None:
    limited = httpx.Response(429, headers={"retry-after": "1"})
    rec = Recorder(limited, httpx.Response(503), fixture("crossref_work.json"))
    cr = client(CrossrefClient, rec)

    paper = await cr.lookup("doi", "10.1093/bioinformatics/btaa921")

    assert paper is not None and len(rec.requests) == 3


async def test_persistent_rate_limit_raises_source_error() -> None:
    rec = Recorder(httpx.Response(429))
    s2 = client(SemanticScholarClient, rec, max_retries=2)

    with pytest.raises(SourceError) as err:
        await s2.search("anything", limit=5)

    assert err.value.source == "semantic_scholar" and err.value.message == "rate limited"
    assert len(rec.requests) == 3


async def test_not_found_is_none_and_responses_are_cached() -> None:
    rec = Recorder(httpx.Response(404), fixture("crossref_work.json"))
    cache = MemoryCache()
    cr = client(CrossrefClient, rec, cache=cache)

    assert await cr.lookup("doi", "10.9999/missing") is None
    first = await cr.lookup("doi", "10.1093/bioinformatics/btaa921")
    again = await cr.lookup("doi", "10.1093/bioinformatics/btaa921")

    assert first == again and len(rec.requests) == 2  # the repeat came from the cache
    assert len(cache.data) == 1  # 404s aren't cached


# ─── Identifiers ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1093/bioinformatics/btaa921", ("doi", "10.1093/bioinformatics/btaa921")),
        ("https://doi.org/10.1093/Bioinformatics/BTAA921.", ("doi", "10.1093/bioinformatics/btaa921")),
        ("doi:10.1145/3447548.3467311", ("doi", "10.1145/3447548.3467311")),
        ("2003.06505", ("arxiv", "2003.06505")),
        ("arXiv:2003.06505v2", ("arxiv", "2003.06505")),
        ("https://arxiv.org/abs/2107.10670v1", ("arxiv", "2107.10670")),
        ("https://arxiv.org/pdf/2107.10670", ("arxiv", "2107.10670")),
        ("10.48550/arXiv.2003.06505", ("arxiv", "2003.06505")),
        ("cs/0112017", ("arxiv", "cs/0112017")),
        ("graph neural networks", None),
    ],
)
def test_parse_identifier(raw: str, expected: tuple[str, str] | None) -> None:
    assert parse_identifier(raw) == expected


def test_normalisers() -> None:
    assert normalize_doi("DOI: 10.1000/ABC") == "10.1000/abc" and normalize_doi("nope") is None
    assert normalize_arxiv_id("http://arxiv.org/abs/2008.12473v1") == "2008.12473"
    title = normalize_title("GraphDTA: Predicting <i>drug–target</i> binding!")
    assert title == "graphdta predicting drug target binding"


# ─── Deduplication ──────────────────────────────────────────────────────


def paper(title: str, **fields: Any) -> PaperMetadata:
    return PaperMetadata(title=title, **fields)


def test_same_doi_merges_and_fills_gaps() -> None:
    a = paper("GraphDTA", doi="10.1/x", sources=["crossref"], found_by=["Q1"], citation_count=10)
    b = paper("GraphDTA.", doi="10.1/x", openalex_id="W1", abstract="Longer abstract.", sources=["openalex"],
              found_by=["Q2"], citation_count=12, authors=[Author(name="A")])  # fmt: skip
    dedup = Deduplicator()
    dedup.add(a)
    dedup.add(b)

    assert len(dedup.papers) == 1
    merged = dedup.papers[0]
    assert (merged.openalex_id, merged.abstract, merged.citation_count) == ("W1", "Longer abstract.", 12)
    assert merged.sources == ["crossref", "openalex"] and merged.found_by == ["Q1", "Q2"]


def test_preprint_merges_with_published_version_by_title() -> None:
    """Real case from the recorded OpenAlex response: GraphDTA on bioRxiv (2019) and in Bioinformatics (2020)."""
    import json as _json

    from app.services.academic.openalex import parse_work

    works = [parse_work(w) for w in _json.loads(fixture("openalex_search.json"))["results"]]
    preprint, journal = works[1], works[0]
    assert preprint and journal and preprint.doi != journal.doi
    dedup = Deduplicator()
    dedup.add(preprint)
    dedup.add(journal)

    assert len(dedup.papers) == 1
    merged = dedup.papers[0]
    assert merged.doi == "10.1093/bioinformatics/btaa921" and merged.year == 2020 and merged.venue == "Bioinformatics"
    assert merged.pdf_url and "biorxiv" in merged.pdf_url  # the preprint's open-access PDF is kept


def test_arxiv_record_merges_with_doi_record_by_title() -> None:
    """Real case: SIGN (KDD 2021) from OpenAlex (ACM DOI) and from arXiv (no DOI)."""
    from app.services.academic.arxiv import parse_feed
    from app.services.academic.openalex import parse_work

    oa = parse_work(json.loads(fixture("openalex_search.json"))["results"][2])
    ax = parse_feed(fixture("arxiv_search.xml"))[1]
    assert oa and oa.doi and ax.doi is None
    dedup = Deduplicator([oa])
    dedup.add(ax)

    assert dedup.new_papers == [] and dedup.updated_existing == {0}
    assert dedup.papers[0].arxiv_id == "2107.10670" and dedup.papers[0].sources == ["openalex", "arxiv"]


def test_preprint_published_years_later_still_merges() -> None:
    """Live case: bioRxiv 2022 preprint, Pharmaceuticals 2024 article; Crossref returned both."""
    title = "Do molecular fingerprints identify diverse active drugs in large-scale virtual screening? (no)"
    dedup = Deduplicator()
    dedup.add(paper(title, doi="10.1101/2022.09.20.508800", year=2022))
    dedup.add(paper(title.title(), doi="10.3390/ph17080992", year=2024, venue="Pharmaceuticals"))
    assert len(dedup.papers) == 1 and dedup.papers[0].doi == "10.3390/ph17080992" and dedup.papers[0].year == 2024
    # Two published versions years apart are different papers
    dedup.add(paper(title, doi="10.9999/later", year=2027))
    assert len(dedup.papers) == 2


def test_different_published_dois_stay_separate() -> None:
    conf = paper("Graph networks for affinity", doi="10.1145/1", year=2021)
    journal = paper("Graph Networks for Affinity", doi="10.1093/2", year=2022)
    dedup = Deduplicator()
    dedup.add(conf)
    dedup.add(journal)
    assert len(dedup.papers) == 2


def test_fuzzy_title_needs_close_years_and_similar_text() -> None:
    dedup = Deduplicator()
    dedup.add(paper("Deep learning for protein-ligand binding affinity prediction: a survey", year=2022))
    dedup.add(paper("Deep Learning for Protein–Ligand Binding Affinity Prediction - A Survey", year=2023))
    dedup.add(paper("Deep learning for protein-ligand binding affinity prediction: a survey", year=2015))
    dedup.add(paper("Deep learning for protein-protein interaction prediction: a survey", year=2022))
    assert [p.year for p in dedup.papers] == [2022, 2015, 2022]


def test_merge_prefers_real_venue_over_arxiv() -> None:
    merged = merge(paper("T", venue="arXiv"), paper("T", venue="KDD"))
    assert merged.venue == "KDD"


# ─── Lookup across sources ──────────────────────────────────────────────


class StubClient:
    def __init__(self, source: str, result: PaperMetadata | None | Exception) -> None:
        self.source = source
        self.result = result

    async def search(self, query: str, **kwargs: Any) -> list[PaperMetadata]:
        return []

    async def lookup(self, kind: str, identifier: str) -> PaperMetadata | None:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_lookup_merges_sources_and_survives_one_failing() -> None:
    clients: dict[Any, Any] = {
        "crossref": StubClient("crossref", paper("GraphDTA", doi="10.1/x", sources=["crossref"])),
        "openalex": StubClient(
            "openalex", paper("GraphDTA", doi="10.1/x", openalex_id="W1", abstract="Abs.", sources=["openalex"])
        ),  # fmt: skip
        "semantic_scholar": StubClient("semantic_scholar", SourceError("semantic_scholar", "rate limited")),
    }
    found = await lookup("doi", "10.1/x", clients)
    assert found is not None and found.openalex_id == "W1" and found.abstract == "Abs."

    clients = {"crossref": StubClient("crossref", SourceError("crossref", "HTTP 503"))}
    with pytest.raises(SourceError):
        await lookup("doi", "10.1/x", clients)
    assert await lookup("doi", "10.1/x", {"crossref": StubClient("crossref", None)}) is None
