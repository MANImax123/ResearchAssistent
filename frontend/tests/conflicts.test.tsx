import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConflictsPanel } from "@/components/literature/ConflictsPanel";
import type { Contradiction } from "@/lib/literature";

afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const ITEM: Contradiction = {
  id: "x1",
  topic: "dataset: PDBbind core",
  a: {
    paper_id: "pa", title: "GraphDTA", year: 2020, claim: "GraphDTA reaches RMSE 1.32 on PDBbind core.",
    evidence: [{ chunk_id: "c1", section: "4.2 Results", kind: "results", page: 9, text: "Table 3: RMSE 1.32." }],
  },
  b: {
    paper_id: "pb", title: "SIGN", year: 2021, claim: "GraphDTA reaches RMSE 1.56 on PDBbind core.",
    evidence: [{ chunk_id: "c2", section: "5.2 Results", kind: "results", page: 7, text: "Baselines: GraphDTA 1.56." }],
  },
  explanation: "Different PDBbind versions (2016 vs 2013).",
  explanation_kind: "dataset_version",
  status: "open",
  dismiss_reason: null,
  dismissed_at: null,
  created_at: new Date().toISOString(),
}; // prettier-ignore

function panel(props: Partial<Parameters<typeof ConflictsPanel>[0]> = {}) {
  const all = { projectId: "pr", canEdit: true, busy: false, profiled: 3, refreshKey: 0, onDetect: vi.fn(), ...props };
  render(<ConflictsPanel {...all} />);
  return all;
}

describe("ConflictsPanel", () => {
  it("shows both claims with their passages and the explanation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [ITEM])));
    const props = panel();

    const card = await screen.findByRole("article", { name: "Contradiction: GraphDTA vs SIGN" });
    expect(within(card).getByText("dataset: PDBbind core")).toBeInTheDocument();
    expect(within(card).getByText("Different dataset version")).toBeInTheDocument();
    expect(within(card).getByText("“GraphDTA reaches RMSE 1.32 on PDBbind core.”")).toBeInTheDocument();
    expect(within(card).getByText(/Baselines: GraphDTA 1.56/)).toBeInTheDocument();
    const links = within(card).getAllByRole("link", { name: "Open" });
    expect(links[0]).toHaveAttribute("href", "/projects/pr/literature/papers/pa#sec-4-2-results");
    expect(links[1]).toHaveAttribute("href", "/projects/pr/literature/papers/pb#sec-5-2-results");
    expect(within(card).getByText(/Different PDBbind versions/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Find contradictions" }));
    expect(props.onDetect).toHaveBeenCalledOnce();
  });

  it("dismisses with a reason, and can show and restore dismissed ones", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, [ITEM]))
      .mockResolvedValueOnce(json(200, { ...ITEM, status: "dismissed", dismiss_reason: "Different splits" }))
      .mockResolvedValueOnce(json(200, []))
      .mockResolvedValueOnce(json(200, [{ ...ITEM, status: "dismissed", dismiss_reason: "Different splits" }]))
      .mockResolvedValueOnce(json(200, ITEM))
      .mockResolvedValue(json(200, [ITEM]));
    vi.stubGlobal("fetch", fetchMock);
    panel();

    await userEvent.click(await screen.findByRole("button", { name: "Dismiss…" }));
    await userEvent.type(screen.getByLabelText("Reason for dismissing"), "Different splits");
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ reason: "Different splits" });
    expect(await screen.findByText(/No contradictions to show/)).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Show dismissed"));
    expect(await screen.findByText("Dismissed: Different splits")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[3][0])).toContain("status=all");
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(String(fetchMock.mock.calls[4][0])).toContain("/contradictions/x1/restore"));
  });

  it("needs two analysed papers", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [])));
    panel({ profiled: 1 });
    expect(screen.getByRole("button", { name: "Find contradictions" })).toBeDisabled();
    expect(screen.getByText(/Analyse at least two selected papers/)).toBeInTheDocument();
  });
});

describe("focusExcerpt", () => {
  it("shows the part of a long passage that backs the claim", async () => {
    const { focusExcerpt } = await import("@/lib/literature");
    const passage = `${"Different loss functions are discussed here. ".repeat(20)}The Davis dataset contains 442 proteins, 68 drugs and 30,056 affinity values. ${"More text. ".repeat(30)}`;
    const cut = focusExcerpt(passage, "The Davis dataset contains 442 proteins and 68 drugs.");
    expect(cut).toContain("68 drugs");
    expect(cut.startsWith("…")).toBe(true);
    expect(focusExcerpt("short", "anything")).toBe("short");
  });
});
