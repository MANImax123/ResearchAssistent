import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ComparePanel } from "@/components/literature/ComparePanel";
import type { Comparison } from "@/lib/literature";

afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const EVIDENCE = { chunk_id: "c1", section: "5.1 Settings", kind: "dataset", page: 6, text: "We use PDBbind v2016." };
const COLUMNS = [
  { key: "method", label: "Method", builtin: true },
  { key: "dataset", label: "Dataset", builtin: true },
  { key: "c_hardware", label: "Hardware", builtin: false },
];
const DATA: Comparison = {
  columns: COLUMNS,
  rows: [
    {
      paper: { id: "p1", title: "SIGN", year: 2021, venue: "KDD", parse_status: "parsed", has_profile: true },
      cells: {
        method: { value: "Structure-aware GNN", source: "profile", evidence: [EVIDENCE] },
        dataset: { value: "PDBbind", source: "user", evidence: [] },
      },
    },
    {
      paper: { id: "p2", title: "Closed paper", year: 2019, venue: null, parse_status: "upload_needed", has_profile: false },
      cells: { method: { value: "CNN on grids", source: "abstract", evidence: [] } },
    },
  ],
  version: 3,
  generated_at: new Date().toISOString(),
  uncompared: 0,
};

function panel(overrides: Partial<Parameters<typeof ComparePanel>[0]> = {}) {
  const props = { projectId: "pr", canEdit: true, busy: false, refreshKey: 0, onBuild: vi.fn(), onFill: vi.fn() };
  return { ...render(<ComparePanel {...props} {...overrides} />), props: { ...props, ...overrides } };
}

describe("ComparePanel", () => {
  it("shows every selected paper with sources, and sorts and filters", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, DATA)));
    panel();

    expect(await screen.findByText("Structure-aware GNN")).toBeInTheDocument();
    expect(screen.getByText("no full text: abstract only")).toBeInTheDocument();
    expect(screen.getByText("abstract")).toBeInTheDocument();
    expect(screen.getByText("edited")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Evidence for Structure-aware GNN" }));
    expect(screen.getByRole("note")).toHaveTextContent("5.1 Settings · p. 6 We use PDBbind v2016.");
    expect(within(screen.getByRole("note")).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/projects/pr/literature/papers/p1#sec-5-1-settings",
    );

    const titles = () => screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("link")[0].textContent);
    expect(titles()).toEqual(["Closed paper", "SIGN"]);
    await userEvent.click(screen.getByRole("button", { name: "Sort by Year" }));
    expect(titles()).toEqual(["Closed paper", "SIGN"]);
    await userEvent.click(screen.getByRole("button", { name: "Sort by Year" }));
    expect(titles()).toEqual(["SIGN", "Closed paper"]);
    await userEvent.type(screen.getByLabelText("Filter rows"), "grids");
    expect(titles()).toEqual(["Closed paper"]);
  });

  it("saves a corrected cell", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(200, DATA)).mockResolvedValue(json(200, DATA));
    vi.stubGlobal("fetch", fetchMock);
    panel();
    await screen.findByText("CNN on grids");

    await userEvent.click(screen.getByText("CNN on grids"));
    const box = screen.getByLabelText("Method for Closed paper");
    await userEvent.clear(box);
    await userEvent.type(box, "3D CNN{Enter}");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][1].method).toBe("PUT");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ paper_id: "p2", key: "method", value: "3D CNN" });
  });

  it("builds, fills custom columns, adds columns and inserts the table", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, { ...DATA, uncompared: 2 }))
      .mockResolvedValueOnce(json(201, DATA))
      .mockResolvedValueOnce(json(200, { table_number: 1, section_version: 4 }));
    vi.stubGlobal("fetch", fetchMock);
    const { props } = panel();

    await userEvent.click(await screen.findByRole("button", { name: "Compare 2 papers" }));
    expect(props.onBuild).toHaveBeenCalledWith(false);
    await userEvent.click(screen.getByRole("button", { name: "Fill with AI" }));
    expect(props.onFill).toHaveBeenCalledWith("c_hardware");

    await userEvent.type(screen.getByLabelText("New column name"), "Code available");
    await userEvent.click(screen.getByRole("button", { name: "Add column" }));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ label: "Code available" });

    await userEvent.click(screen.getByRole("button", { name: "Insert into Literature Survey" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Inserted as Table 1 in the Literature Survey section");
  });

  it("is read-only for viewers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, DATA)));
    panel({ canEdit: false });
    await userEvent.click(await screen.findByText("CNN on grids"));
    expect(screen.queryByRole("textbox", { name: /Method for/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add column" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
  });
});
