import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AddPaper } from "@/components/literature/AddPaper";
import { AskPanel } from "@/components/literature/AskPanel";
import { DiscoverPanel, DiscoveryReport } from "@/components/literature/DiscoverPanel";
import { FullTextPanel } from "@/components/literature/FullTextPanel";
import { PaperTable } from "@/components/literature/PaperTable";
import { QueryEditor } from "@/components/literature/QueryEditor";
import { RankPanel } from "@/components/literature/RankPanel";
import {
  authorLine,
  paperLink,
  sectionAnchor,
  splitCitations,
  type LiteratureWorkspace,
  type Paper,
  type SearchQuery,
  type SourceStatus,
} from "@/lib/literature";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

const QUERIES: SearchQuery[] = [
  { id: "q1", label: "Q1", text: "graph neural network binding affinity", purpose: "Core method", targets: ["RQ1", "S1"],
    origin: "ai", last_run_at: new Date().toISOString(), last_found: 37 },
  { id: "q2", label: "Q2", text: "fingerprint baselines", purpose: null, targets: ["RQ2"], origin: "user",
    last_run_at: null, last_found: null },
]; // prettier-ignore

const SOURCES: SourceStatus[] = [
  { name: "semantic_scholar", label: "Semantic Scholar", configured: false, note: "No API key: shared pool, often rate limited" },
  { name: "openalex", label: "OpenAlex", configured: true, note: "Polite pool" },
  { name: "arxiv", label: "arXiv", configured: true, note: "Preprints" },
  { name: "crossref", label: "Crossref", configured: true, note: "Polite pool" },
]; // prettier-ignore

function paper(over: Partial<Paper>): Paper {
  return {
    id: "p1", title: "GraphDTA", authors: [{ name: "Thin Nguyen" }], year: 2020, venue: "Bioinformatics",
    abstract: "Abstract text.", doi: "10.1093/bioinformatics/btaa921", s2_id: null, arxiv_id: null,
    openalex_id: "W1", url: null, pdf_url: null, pdf_file_id: null, citation_count: 1232, origin: "search",
    sources: ["openalex", "crossref"], found_by: ["Q1"], selected: false, parse_status: "not_started",
    created_at: new Date().toISOString(), has_identifier: true, must_include: false, similarity: null,
    rq_similarity: {}, relevance_score: null, relevance_reason: null, relevance_rqs: [], parse_error: null, has_profile: false, ...over,
  }; // prettier-ignore
}

describe("QueryEditor", () => {
  it("shows targets and run results, and saves edits in order", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <QueryEditor queries={QUERIES} targetText={{ RQ1: "Does a GNN rank better?" }} canEdit busy={false}
                   onSave={onSave} onSuggest={vi.fn()} />,
    ); // prettier-ignore

    expect(screen.getByText("RQ1")).toHaveAttribute("title", "Does a GNN rank better?");
    expect(screen.getByText(/37 results/)).toBeInTheDocument();
    expect(screen.getByText("Yours")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save queries" })).not.toBeInTheDocument();

    const first = screen.getByLabelText("Query 1");
    await userEvent.clear(first);
    await userEvent.type(first, "graph attention affinity");
    await userEvent.click(screen.getByRole("button", { name: "Remove query 2" }));
    await userEvent.click(screen.getByRole("button", { name: "Add query" }));
    await userEvent.type(screen.getByLabelText("Query 2"), "PDBbind");
    await userEvent.click(screen.getByRole("button", { name: "Save queries" }));

    expect(onSave).toHaveBeenCalledWith([
      { id: "q1", text: "graph attention affinity", purpose: "Core method", targets: ["RQ1", "S1"] },
      { id: undefined, text: "PDBbind", purpose: null, targets: [] },
    ]);
  });

  it("asks before replacing queries with suggestions", async () => {
    const onSuggest = vi.fn();
    const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
    vi.stubGlobal("confirm", confirm);
    render(<QueryEditor queries={QUERIES} targetText={{}} canEdit busy={false} onSave={vi.fn()} onSuggest={onSuggest} />);

    await userEvent.click(screen.getByRole("button", { name: "Suggest new queries" }));
    expect(onSuggest).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Suggest new queries" }));
    expect(onSuggest).toHaveBeenCalledOnce();
  });

  it("is read-only for viewers", () => {
    render(<QueryEditor queries={QUERIES} targetText={{}} canEdit={false} busy={false} onSave={vi.fn()} onSuggest={vi.fn()} />);
    expect(screen.getByLabelText("Query 1")).toBeDisabled();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("DiscoverPanel", () => {
  it("sends the chosen sources and filters", async () => {
    const onRun = vi.fn();
    render(<DiscoverPanel sources={SOURCES} queryCount={2} canRun busy={false} summary={null} onRun={onRun} />);

    expect(screen.getByText("No API key: shared pool, often rate limited")).toHaveClass("text-amber-700");
    await userEvent.click(screen.getByRole("checkbox", { name: /Semantic Scholar/ }));
    await userEvent.type(screen.getByLabelText("From year"), "2019");
    await userEvent.type(screen.getByLabelText("Venue contains"), "bioinformatics");
    await userEvent.click(screen.getByRole("button", { name: "Search 2 queries" }));

    expect(onRun).toHaveBeenCalledWith({
      sources: ["openalex", "arxiv", "crossref"],
      year_from: 2019,
      year_to: null,
      venue: "bioinformatics",
      per_source: 10,
      max_new: 100,
    });
  });

  it("needs queries and valid years", async () => {
    const { rerender } = render(
      <DiscoverPanel sources={SOURCES} queryCount={0} canRun busy={false} summary={null} onRun={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Search 0 queries" })).toBeDisabled();

    rerender(<DiscoverPanel sources={SOURCES} queryCount={1} canRun busy={false} summary={null} onRun={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("From year"), "2024");
    await userEvent.type(screen.getByLabelText("To year"), "2020");
    expect(screen.getByRole("button", { name: "Search 1 query" })).toBeDisabled();
    expect(screen.getByText(/is after/)).toBeInTheDocument();
  });

  it("reports what each source returned", () => {
    render(
      <DiscoveryReport
        summary={{
          added: 42, enriched: 3, raw: 160, filtered_out: 5, over_cap: 0,
          sources: {
            openalex: { label: "OpenAlex", found: 80, errors: [], skipped: false },
            semantic_scholar: { label: "Semantic Scholar", found: 0, errors: ["Q1: rate limited", "Q2: rate limited"], skipped: true },
            arxiv: { label: "arXiv", found: 30, errors: [], skipped: false },
            crossref: { label: "Crossref", found: 50, errors: [], skipped: false },
          },
        }}
      />,
    ); // prettier-ignore
    const report = screen.getByRole("status");
    expect(report).toHaveTextContent("42 new papers added, 3 already in your library updated · 160 results");
    expect(within(report).getByText("Semantic Scholar: 0 · stopped (unavailable)")).toHaveAttribute(
      "title",
      "Q1: rate limited\nQ2: rate limited",
    );
  });
});

describe("PaperTable", () => {
  it("lists papers with source badges, links and citation warnings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      json(200, { total: 2, items: [paper({}), paper({ id: "p2", title: "Lab report", doi: null, openalex_id: null,
        has_identifier: false, sources: ["upload"], pdf_file_id: "f1", origin: "upload", found_by: [] })] }),
    ); // prettier-ignore
    vi.stubGlobal("fetch", fetchMock);
    render(<PaperTable projectId="p" canEdit refreshKey={0} rqIds={[]} ranked={false} onChanged={vi.fn()} />);

    expect(await screen.findByText("GraphDTA")).toBeInTheDocument();
    expect(screen.getByText("(2)")).toBeInTheDocument();
    const row = screen.getByText("GraphDTA").closest("tr")!;
    expect(within(row).getByText("OpenAlex")).toBeInTheDocument();
    expect(within(row).getByText("Crossref")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "doi:10.1093/bioinformatics/btaa921" })).toHaveAttribute(
      "href",
      "https://doi.org/10.1093/bioinformatics/btaa921",
    );
    expect(within(row).getByText("Found by Q1")).toBeInTheDocument();
    const upload = screen.getByText("Lab report").closest("tr")!;
    expect(within(upload).getByText(/can't be cited/)).toBeInTheDocument();
    expect(within(upload).getByText("PDF available")).toBeInTheDocument();

    await userEvent.click(screen.getByText("GraphDTA"));
    expect(screen.getByText("Abstract text.")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Filter by source"), "arxiv");
    await waitFor(() => expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain("source=arxiv"));
  });
});

describe("AddPaper", () => {
  it("adds by DOI and says when the paper was already there", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(201, { created: true, paper: paper({}) }))
      .mockResolvedValueOnce(json(200, { created: false, paper: paper({}) }))
      .mockResolvedValueOnce(json(404, { detail: "No paper found for DOI 10.9/x" }));
    vi.stubGlobal("fetch", fetchMock);
    const onAdded = vi.fn();
    render(<AddPaper projectId="p" onAdded={onAdded} />);

    const input = screen.getByLabelText("DOI or arXiv id");
    await userEvent.type(input, "10.1093/bioinformatics/btaa921");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText('Added "GraphDTA".')).toBeInTheDocument();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ identifier: "10.1093/bioinformatics/btaa921" });

    await userEvent.type(input, "10.1093/bioinformatics/btaa921");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText('"GraphDTA" is already in your library.')).toBeInTheDocument();
    expect(onAdded).toHaveBeenCalledTimes(2);

    await userEvent.type(input, "10.9/x");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No paper found for DOI 10.9/x");
  });
});

describe("helpers", () => {
  it("formats authors and links", () => {
    expect(authorLine([{ name: "A" }, { name: "B" }, { name: "C" }, { name: "D" }])).toBe("A, B, C et al.");
    expect(authorLine([])).toBe("Unknown authors");
    expect(paperLink(paper({ doi: null, arxiv_id: "2003.06505" }))).toBe("https://arxiv.org/abs/2003.06505");
  });
});

const WS: LiteratureWorkspace = {
  status: "not_started", editable: true, blocked_reason: null, queries: QUERIES, paper_count: 100,
  selected_count: 0, must_include_count: 0, judged_count: 0, unranked_count: 100, parse_counts: {}, indexed_chunks: 0, pending_chunks: 0, profiled_count: 0,
  sources: SOURCES,
  research_questions: [{ id: "RQ1", text: "Q?" }, { id: "RQ2", text: "H." }], subtopics: [],
}; // prettier-ignore

describe("RankPanel", () => {
  it("ranks first, then offers to select the top papers", async () => {
    const onRank = vi.fn();
    const onSelectTop = vi.fn();
    const { rerender } = render(
      <RankPanel ws={WS} canEdit busy={false} onRank={onRank} onSelectTop={onSelectTop} onClear={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Select the top 20" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Rank by relevance" }));
    expect(onRank).toHaveBeenCalledWith(40);

    rerender(
      <RankPanel ws={{ ...WS, judged_count: 40, unranked_count: 3, selected_count: 12 }} canEdit busy={false}
                 onRank={onRank} onSelectTop={onSelectTop} onClear={vi.fn()} />,
    ); // prettier-ignore
    expect(screen.getByRole("status")).toHaveTextContent("3 papers were added since the last ranking");
    expect(screen.getByRole("button", { name: "Rank again" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Select the top 20" }));
    expect(onSelectTop).toHaveBeenCalledWith(20, 0.6);
    await userEvent.click(screen.getByLabelText(/only papers the AI scored/));
    await userEvent.click(screen.getByRole("button", { name: "Select the top 20" }));
    expect(onSelectTop).toHaveBeenLastCalledWith(20, null);
  });
});

describe("PaperTable relevance & selection", () => {
  it("shows AI scores with reasons, sorts by relevance and saves selections", async () => {
    const judged = paper({ relevance_score: 0.9, relevance_reason: "Same task and method.", relevance_rqs: ["RQ1"],
                           similarity: 0.71 }); // prettier-ignore
    const unjudged = paper({ id: "p2", title: "Loosely related", similarity: 0.42 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, { total: 2, items: [judged, unjudged] }))
      .mockResolvedValueOnce(json(200, { ...judged, selected: true }))
      .mockResolvedValueOnce(json(200, { ...unjudged, selected: true, must_include: true }))
      .mockResolvedValue(json(200, { total: 0, items: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = vi.fn();
    render(<PaperTable projectId="p" canEdit refreshKey={0} rqIds={["RQ1", "RQ2"]} ranked onChanged={onChanged} />);

    expect(await screen.findByText("9/10")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain("sort=relevance");
    expect(screen.getByText("Same task and method.")).toBeInTheDocument();
    expect(screen.getByText("match 42%")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Select GraphDTA"));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ selected: true });
    expect(await screen.findByLabelText("Select GraphDTA")).toBeChecked();

    const row = screen.getByText("Loosely related").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "☆ Must include" }));
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ must_include: true });
    expect(await within(row).findByRole("button", { name: "★ Must include" })).toHaveAttribute("aria-pressed", "true");
    expect(onChanged).toHaveBeenCalledTimes(2);

    await userEvent.selectOptions(screen.getByLabelText("Filter by research question"), "RQ2");
    await waitFor(() => expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain("rq=RQ2"));
  });
});

describe("FullTextPanel", () => {
  it("shows each selected paper's parse status and offers uploads where needed", async () => {
    const parsed = paper({ id: "a", title: "Parsed paper", selected: true, parse_status: "parsed" });
    const needs = paper({ id: "b", title: "Paywalled paper", selected: true, parse_status: "upload_needed",
                          parse_error: "No open-access PDF could be downloaded (open_access: HTTP 403)." }); // prettier-ignore
    const fetchMock = vi.fn().mockResolvedValue(json(200, { total: 2, items: [parsed, needs] }));
    vi.stubGlobal("fetch", fetchMock);
    const onParse = vi.fn();
    render(
      <FullTextPanel projectId="p" canEdit busy={false} refreshKey={0} onParse={onParse} onAnalyse={vi.fn()}
                     onChanged={vi.fn()}
                     ws={{ ...WS, selected_count: 3, parse_counts: { parsed: 1, upload_needed: 1, not_started: 1 } }} />,
    ); // prettier-ignore

    expect(await screen.findByText("Parsed paper")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain("selected=true");
    expect(screen.getByText("Parsed: 1")).toBeInTheDocument();
    expect(screen.getByText("Upload needed: 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/projects/p/literature/papers/a");
    const row = screen.getByText("Paywalled paper").closest("li")!;
    expect(within(row).getByText(/HTTP 403/)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Upload PDF" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Get full text for 2 papers" }));
    expect(onParse).toHaveBeenCalledWith({});
    await userEvent.click(screen.getByRole("button", { name: "Parse all again" }));
    expect(onParse).toHaveBeenLastCalledWith({ force: true });
  });

  it("says a new run only retries the open-access sources when only uploads are missing", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, { total: 0, items: [] })));
    render(
      <FullTextPanel projectId="p" canEdit busy={false} refreshKey={0} onParse={vi.fn()} onAnalyse={vi.fn()}
                     onChanged={vi.fn()}
                     ws={{ ...WS, selected_count: 3, parse_counts: { parsed: 1, upload_needed: 2 } }} />,
    ); // prettier-ignore
    expect(screen.getByRole("button", { name: "Try open-access sources again" })).toBeEnabled();
  });
});

describe("AskPanel", () => {
  const SOURCE = {
    marker: "S1", chunk_id: "c1", paper_id: "p9", paper_title: "GraphDTA", year: 2020, section: "4.1 Datasets",
    kind: "dataset", page: 5, text: "We evaluate on the Davis and KIBA datasets.", similarity: 0.81, pdf_file_id: "f1",
  }; // prettier-ignore

  it("answers with clickable sources that open the cited section", async () => {
    const answer = {
      answer: "GraphDTA uses Davis and KIBA [S1].", insufficient: false, citations: [SOURCE], retrieved: 8,
      removed_markers: ["[S12]"],
    }; // prettier-ignore
    const fetchMock = vi.fn().mockResolvedValue(json(200, answer));
    vi.stubGlobal("fetch", fetchMock);
    render(<AskPanel projectId="p" ws={{ ...WS, indexed_chunks: 120 }} canEdit busy={false} onIndex={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("Your question"), "Which datasets are used?");
    await userEvent.selectOptions(screen.getByLabelText("Search in"), "dataset");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      question: "Which datasets are used?",
      section_kinds: ["dataset"],
    });
    const chip = await screen.findByRole("button", { name: "Source S1: GraphDTA" });
    await userEvent.click(chip);
    expect(chip).toHaveClass("bg-indigo-600");
    expect(screen.getByText("Sources (1 of 8 passages read)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in paper" })).toHaveAttribute(
      "href",
      "/projects/p/literature/papers/p9#sec-4-1-datasets",
    );
    expect(screen.getByRole("button", { name: "PDF at p. 5" })).toBeInTheDocument();
    expect(screen.getByText(/1 citation to passages that weren't retrieved was removed/)).toBeInTheDocument();
  });

  it("explains when nothing is searchable yet and offers to index pending passages", async () => {
    const onIndex = vi.fn();
    render(
      <AskPanel projectId="p" ws={{ ...WS, indexed_chunks: 0, pending_chunks: 14 }} canEdit busy={false}
                onIndex={onIndex} />,
    ); // prettier-ignore
    expect(screen.getByText(/Get the full text of some selected papers first/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Your question")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Index them now" }));
    expect(onIndex).toHaveBeenCalledOnce();
  });

  it("flags answers the passages don't support", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, {
      answer: "The passages don't mention wet-lab validation.", insufficient: true, citations: [], retrieved: 8,
      removed_markers: [],
    }))); // prettier-ignore
    render(<AskPanel projectId="p" ws={{ ...WS, indexed_chunks: 50 }} canEdit busy={false} onIndex={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Which evaluation metrics/ }));
    expect(await screen.findByText(/don't fully answer this/)).toBeInTheDocument();
  });
});

describe("citation helpers", () => {
  it("splits markers and builds section anchors", () => {
    expect(splitCitations("A [S1] and B [S2][S3].")).toEqual([
      { text: "A " }, { marker: "S1" }, { text: " and B " }, { marker: "S2" }, { marker: "S3" }, { text: "." },
    ]); // prettier-ignore
    expect(sectionAnchor("5.2.1 Overall Comparison (RQ1).")).toBe("sec-5-2-1-overall-comparison-rq1");
  });
});
