import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProfilePanel } from "@/components/literature/ProfilePanel";
import type { PaperProfile } from "@/lib/literature";

afterEach(() => vi.unstubAllGlobals());

const MODEL = { chunk_id: "c3", section: "4 Model", kind: "methodology", page: 3, text: "SIGN uses polar-inspired attention." };
const DATA = { chunk_id: "c4", section: "5.1 Settings", kind: "dataset", page: 6, text: "We train on PDBbind v2016." };

const PROFILE: PaperProfile = {
  paper_id: "p1",
  problem: { value: "Binding affinity with long-range interactions.", evidence: [MODEL] },
  method: { value: "Structure-aware GNN with polar-inspired attention.", evidence: [MODEL] },
  results: null,
  dataset: [{ value: "PDBbind v2016", evidence: [DATA] }, { value: "In-house set", evidence: [] }],
  metrics: [{ value: "RMSE", evidence: [DATA] }],
  limitations: [],
  tech_stack: [],
  edited_fields: ["dataset"],
  updated_at: new Date().toISOString(),
};

function panel(profile: PaperProfile | null, canEdit = true, onSaved = vi.fn(), onAnalyse = vi.fn()) {
  return render(
    <ProfilePanel projectId="pr" paperId="p1" profile={profile} canEdit={canEdit} analysing={false}
                  onAnalyse={onAnalyse} onSaved={onSaved} />,
  ); // prettier-ignore
}

describe("ProfilePanel", () => {
  it("shows each value with evidence that opens the passage", async () => {
    panel(PROFILE);

    expect(screen.getByText("Structure-aware GNN with polar-inspired attention.")).toBeInTheDocument();
    expect(screen.getByText("Not stated in the paper")).toBeInTheDocument(); // results
    expect(screen.getByText("no evidence (added by you)")).toBeInTheDocument();
    expect(screen.getByText(/You edited: Datasets/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Evidence 1 for Method: 4 Model, page 3" }));
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("4 Model · p. 3");
    expect(note).toHaveTextContent("SIGN uses polar-inspired attention.");
    expect(screen.getByRole("link", { name: "Go to this section" })).toHaveAttribute("href", "#sec-4-model");
  });

  it("saves the researcher's corrections, keeping evidence of kept items", async () => {
    const saved = { ...PROFILE, edited_fields: ["dataset", "method"] };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(saved), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSaved = vi.fn();
    panel(PROFILE, true, onSaved);

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const method = screen.getByLabelText("Method");
    await userEvent.clear(method);
    await userEvent.type(method, "SIGN (KDD 2021).");
    await userEvent.click(screen.getByRole("button", { name: "Remove Datasets 2" }));
    await userEvent.click(screen.getByRole("button", { name: "+ Add metrics" }));
    await userEvent.type(screen.getByLabelText("Metrics 2"), "Pearson R");
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }));

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(fetchMock.mock.calls[0][1].method).toBe("PUT");
    expect(body.method).toBe("SIGN (KDD 2021).");
    expect(body.results).toBeNull();
    expect(body.dataset).toEqual([{ value: "PDBbind v2016", evidence: ["c4"] }]);
    expect(body.metrics).toEqual([{ value: "RMSE", evidence: ["c4"] }, { value: "Pearson R", evidence: [] }]);
    expect(onSaved).toHaveBeenCalledWith(saved);
  });

  it("offers analysis when there is no profile, and nothing to viewers", async () => {
    const onAnalyse = vi.fn();
    const { unmount } = panel(null, true, vi.fn(), onAnalyse);
    expect(screen.getByText("Not analysed yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Analyse this paper" }));
    expect(onAnalyse).toHaveBeenCalledOnce();
    unmount();

    panel(PROFILE, false);
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Analyse/ })).not.toBeInTheDocument();
  });
});
