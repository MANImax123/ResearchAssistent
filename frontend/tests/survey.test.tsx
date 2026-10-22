import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SurveyPanel } from "@/components/literature/SurveyPanel";
import { API_URL } from "@/lib/api";
import type { Gap } from "@/lib/gaps";
import { numberCitations, wordDiff, type Refinement, type Survey } from "@/lib/survey";

vi.mock("@/components/editor/MarkdownView", () => ({
  MarkdownView: ({ markdown }: { markdown: string }) => <div data-testid="markdown">{markdown}</div>,
}));

afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("wordDiff", () => {
  it("marks removed and added words", () => {
    expect(wordDiff("Evaluate ranking against baselines.", "Evaluate ranking on cold targets against baselines.")).toEqual([
      { text: "Evaluate ranking ", change: "same" },
      { text: "on cold targets ", change: "added" },
      { text: "against baselines.", change: "same" },
    ]);
    expect(wordDiff("a b", "a c").filter((p) => p.change !== "same").map((p) => p.change)).toEqual(["removed", "added"]);
  });
});

describe("numberCitations", () => {
  it("numbers papers by first citation and flags unknown keys", () => {
    const cites = [
      { paper_id: "p1", title: "GraphDTA", year: 2020, authors: "A. Author" },
      { paper_id: "p2", title: "SIGN", year: 2021, authors: "" },
    ];
    const { text, references } = numberCitations("A [@p2][@p1]. B [@p2]. C [@zz].", cites);
    expect(text).toBe("A [1][2]. B [1]. C [?].");
    expect(references.map((r) => [r.n, r.title])).toEqual([[1, "SIGN"], [2, "GraphDTA"]]);
  });
});

const REFS: Refinement[] = [
  {
    id: "r1", kind: "modify_objective", target: "O2", text: "Evaluate ranking quality, including cold-target splits.",
    question_kind: null, linked_objective_ids: [], question_text: null, before: "Evaluate ranking quality.",
    rationale: "The accepted gap concerns cold targets.", gaps: [{ id: "g1", statement: "Cold-target evaluation appears rare." }],
    status: "proposed", applied_ids: [], decided_at: null, created_at: new Date().toISOString(),
  },
  {
    id: "r2", kind: "add_question", target: null, text: "Do GNN gains hold for unseen targets?",
    question_kind: "question", linked_objective_ids: ["O2"], question_text: null, before: null, rationale: null,
    gaps: [], status: "accepted", applied_ids: ["RQ3"], decided_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
  },
]; // prettier-ignore

const SURVEY: Survey = {
  stage_status: "awaiting_review", version: 2, source: "ai", updated_at: new Date().toISOString(),
  content: "Opening [@p1].\n\n### Graph models\n\nGNNs dominate [@p1][@p2].\n\n### Research gaps\n\nCold targets may be rare [@p2].",
  citations: [
    { paper_id: "p1", title: "GraphDTA", year: 2020, authors: "A. Author, B. Author" },
    { paper_id: "p2", title: "SIGN", year: 2021, authors: "" },
  ],
  unresolved: [], uncited_paragraphs: 0, paragraphs: 3,
}; // prettier-ignore

const STATE = {
  project_id: "pr", version: 3, updated_at: null, state: {}, pending_review: null, checkpoint: null,
  stages: [{ name: "literature", status: "awaiting_review", approved_at: null, prerequisites: ["idea"], blocked_by: [],
             can_run: false, can_regenerate: true, can_go_back: false }],
}; // prettier-ignore

const ACCEPTED_GAP = { id: "g1", status: "accepted" } as Gap;

function routes(overrides: Record<string, unknown> = {}) {
  const table: Record<string, unknown> = {
    "/literature/refinements": REFS,
    "/literature/survey": SURVEY,
    "/state": STATE,
    "/gaps": [ACCEPTED_GAP],
    ...overrides,
  };
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).replace(`${API_URL}/projects/pr`, "");
    if (init?.method === "POST") {
      if (path.endsWith("/accept")) return json(200, { ...REFS[0], status: "accepted" });
      if (path.endsWith("/approve")) return json(200, STATE);
      if (path.endsWith("/regenerate")) return json(202, { stage: "literature", status: "running", job: { id: "j1" } });
      if (path.endsWith("/refinements/suggest")) return json(202, { id: "j2" });
    }
    return json(200, table[path]);
  });
}

function panel(props: Partial<Parameters<typeof SurveyPanel>[0]> = {}) {
  const all = { projectId: "pr", canEdit: true, busy: false, refreshKey: 0, onStart: vi.fn(), onChanged: vi.fn(), ...props };
  render(<SurveyPanel {...all} />);
  return all;
}

describe("SurveyPanel", () => {
  it("shows refinements as a diff and applies one on Accept", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    const props = panel();

    const card = await screen.findByRole("article", { name: "Change objective O2" });
    expect(within(card).getByText(/including cold-target splits\./).tagName).toBe("INS");
    expect(within(card).getByText(/Addresses the potential gap: Cold-target/)).toBeInTheDocument();
    expect(screen.getByText("Applied as RQ3")).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/refinements/r1/accept") && i?.method === "POST")).toBe(true),
    );
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "Suggest refinements" }));
    expect(props.onStart).toHaveBeenCalledOnce();
  });

  it("previews the section with numbered citations and approves it", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    panel();

    expect(await screen.findByTestId("markdown")).toHaveTextContent("Opening [1]. ### Graph models GNNs dominate [1][2].");
    expect(screen.getByText(/2 papers cited · every citation resolves to a verified paper/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SIGN" })).toHaveAttribute("href", "/projects/pr/literature/papers/p2");
    expect(screen.getByRole("link", { name: "Edit in the section editor" })).toHaveAttribute(
      "href",
      "/projects/pr/sections/literature_survey",
    );

    await userEvent.click(screen.getByRole("button", { name: "Approve section" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/stages/literature/approve"))).toBe(true),
    );
  });

  it("rewrites with the researcher's feedback", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    const onStart = vi.fn();
    panel({ onStart });

    await userEvent.type(await screen.findByLabelText("What should change?"), "Shorter, please.");
    await userEvent.click(screen.getByRole("button", { name: "Rewrite with this feedback" }));
    expect(onStart).toHaveBeenCalledOnce();
    expect(await onStart.mock.calls[0][0]()).toEqual({ id: "j1" });
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/stages/literature/regenerate"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ feedback: "Shorter, please." });
  });

  it("warns about unresolved citations and needs accepted gaps before refining", async () => {
    vi.stubGlobal(
      "fetch",
      routes({ "/gaps": [], "/literature/refinements": [], "/literature/survey": { ...SURVEY, unresolved: ["zz"], uncited_paragraphs: 1 } }),
    );
    panel();

    expect(await screen.findByText(/1 citation don't resolve to a verified paper · 1 paragraph has no citation/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Suggest refinements" })).toBeDisabled();
    expect(screen.getByText(/Accept potential gaps first/)).toBeInTheDocument();
  });

  it("is read-only for viewers", async () => {
    vi.stubGlobal("fetch", routes());
    panel({ canEdit: false });

    await screen.findByTestId("markdown");
    expect(screen.queryByRole("button", { name: /Approve section|Rewrite|Suggest refinements|Accept/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download analysis report" })).toBeInTheDocument();
  });
});
