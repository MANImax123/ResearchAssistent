import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GapsPanel } from "@/components/literature/GapsPanel";
import { API_URL } from "@/lib/api";
import type { Gap } from "@/lib/gaps";

afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const OBJECTIVES = [
  { id: "O1", text: "Design a GNN that predicts binding affinity." },
  { id: "O2", text: "Evaluate on public benchmarks." },
];

const GAP: Gap = {
  id: "g1",
  statement: "It is unclear whether GCN-based models such as GraphDTA generalise to PDBbind 2016.",
  rationale: "GCN is only reported on Davis and KIBA.",
  confidence: "high",
  status: "candidate",
  objectives: [OBJECTIVES[0]],
  signals: [
    {
      kind: "limitation", text: "“GraphDTA” (2020): Ignores protein structure.",
      papers: [{ id: "pa", title: "GraphDTA", year: 2020 }],
      evidence: [{ chunk_id: "c1", section: "5.2 Results", kind: "results", page: 6, text: "We ignore protein structure." }],
    },
    {
      kind: "untested", text: "Method GCN is not reported on PDBbind 2016, which 2 selected papers use.",
      papers: [{ id: "pa", title: "GraphDTA", year: 2020 }, { id: "pb", title: "SIGN", year: 2021 }],
      evidence: [],
    },
  ],
  edited: false,
  decided_at: null,
  created_at: new Date().toISOString(),
}; // prettier-ignore

function panel(props: Partial<Parameters<typeof GapsPanel>[0]> = {}) {
  const all = {
    projectId: "pr", canEdit: true, busy: false, profiled: 3, objectives: OBJECTIVES, refreshKey: 0,
    onDetect: vi.fn(), ...props,
  }; // prettier-ignore
  render(<GapsPanel {...all} />);
  return all;
}

describe("GapsPanel", () => {
  it("labels every suggestion a potential gap and shows its evidence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [GAP])));
    const props = panel();

    const card = await screen.findByRole("article", { name: `Potential gap: ${GAP.statement}` });
    expect(within(card).getByText("Potential gap")).toBeInTheDocument();
    expect(within(card).getByText("High confidence")).toBeInTheDocument();
    expect(screen.queryByText(/confirmed/i)).not.toBeInTheDocument();
    expect(within(card).getByText(/O1 · Design a GNN/)).toBeInTheDocument();

    await userEvent.click(within(card).getByText("Evidence (2)"));
    expect(within(card).getByText("Limitation")).toBeInTheDocument();
    expect(within(card).getByText("Untested combination")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/projects/pr/literature/papers/pa#sec-5-2-results",
    );
    expect(within(card).getByRole("link", { name: "SIGN" })).toHaveAttribute("href", "/projects/pr/literature/papers/pb");

    await userEvent.click(screen.getByRole("button", { name: "Find potential gaps" }));
    expect(props.onDetect).toHaveBeenCalledOnce();
  });

  it("accepts, rejects and restores through the API", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, [GAP, { ...GAP, id: "g2", statement: "Pockets may be under-used here." }]))
      .mockResolvedValueOnce(json(200, { ...GAP, status: "accepted", decided_at: new Date().toISOString() }))
      .mockResolvedValueOnce(json(200, { ...GAP, id: "g2", statement: "Pockets may be under-used here.", status: "rejected" }));
    vi.stubGlobal("fetch", fetchMock);
    panel();

    const first = await screen.findByRole("article", { name: `Potential gap: ${GAP.statement}` });
    await userEvent.click(within(first).getByRole("button", { name: "Accept" }));
    expect(String(fetchMock.mock.calls[1][0])).toBe(`${API_URL}/projects/pr/gaps/g1`);
    expect(fetchMock.mock.calls[1][1].method).toBe("PATCH");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ status: "accepted" });
    expect(await screen.findByText("Accepted (1)")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: /GraphDTA generalise/ })).getByText("Accepted")).toBeInTheDocument();

    const second = screen.getByRole("article", { name: "Potential gap: Pockets may be under-used here." });
    await userEvent.click(within(second).getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(screen.queryByRole("article", { name: /Pockets may be/ })).not.toBeInTheDocument(),
    );
    await userEvent.click(screen.getByLabelText("Show rejected (1)"));
    expect(screen.getByRole("article", { name: /Pockets may be/ })).toBeInTheDocument();
  }); // prettier-ignore

  it("edits the wording, confidence and linked objectives", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, [GAP]))
      .mockResolvedValueOnce(json(200, { ...GAP, statement: "Edited: may not generalise.", edited: true }));
    vi.stubGlobal("fetch", fetchMock);
    panel();

    const card = await screen.findByRole("article", { name: /GraphDTA generalise/ });
    await userEvent.click(within(card).getByRole("button", { name: "Edit" }));
    const statement = within(card).getByLabelText("Potential gap");
    await userEvent.clear(statement);
    await userEvent.type(statement, "Edited: may not generalise.");
    await userEvent.selectOptions(within(card).getByLabelText("Confidence"), "medium");
    await userEvent.click(within(card).getByLabelText(/O2/));
    await userEvent.click(within(card).getByLabelText(/O1/));
    await userEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      statement: "Edited: may not generalise.",
      rationale: GAP.rationale,
      confidence: "medium",
      objective_ids: ["O2"],
    });
    expect(await screen.findByText("Edited")).toBeInTheDocument();
  });

  it("is read-only for viewers and explains what's missing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [])));
    panel({ canEdit: false, profiled: 0 });

    expect(await screen.findByText(/No potential gaps yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find potential gaps" })).not.toBeInTheDocument();
    expect(screen.getByText(/Analyse selected papers first/)).toBeInTheDocument();
  });
});
