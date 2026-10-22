import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GraphPanel } from "@/components/literature/GraphPanel";
import { API_URL } from "@/lib/api";
import type { NodeDetails, Subgraph } from "@/lib/graph";

afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const GRAPH: Subgraph = {
  nodes: [
    { id: "Paper:p1", type: "Paper", label: "GraphDTA: affinity with GNNs", degree: 3, props: { year: 2020 } },
    { id: "Paper:p2", type: "Paper", label: "GraphCL-DTA", degree: 2, props: { year: 2023 } },
    { id: "Method:gcn", type: "Method", label: "GCN", degree: 1, props: {} },
    { id: "Dataset:davis", type: "Dataset", label: "Davis", degree: 2, props: {} },
  ],
  edges: [
    { source: "Paper:p1", target: "Method:gcn", type: "USES", props: {} },
    { source: "Paper:p1", target: "Dataset:davis", type: "EVALUATED_ON", props: {} },
    { source: "Paper:p2", target: "Dataset:davis", type: "EVALUATED_ON", props: {} },
    { source: "Paper:p2", target: "Paper:p1", type: "CONTRADICTS", props: { topic: "dataset: Davis" } },
  ],
};

const DAVIS: NodeDetails = {
  id: "Dataset:davis",
  type: "Dataset",
  label: "Davis",
  props: {},
  neighbours: [
    { id: "Paper:p1", type: "Paper", label: "GraphDTA: affinity with GNNs", relation: "EVALUATED_ON", outgoing: false, props: {} },
    { id: "Paper:p2", type: "Paper", label: "GraphCL-DTA", relation: "EVALUATED_ON", outgoing: false, props: {} },
  ],
}; // prettier-ignore

const P1: NodeDetails = {
  id: "Paper:p1",
  type: "Paper",
  label: "GraphDTA: affinity with GNNs",
  props: { year: 2020, venue: "Bioinformatics", relevance: 0.82 },
  neighbours: [
    { id: "Method:gcn", type: "Method", label: "GCN", relation: "USES", outgoing: true, props: { proposed: false } },
    { id: "Paper:p2", type: "Paper", label: "GraphCL-DTA", relation: "CONTRADICTS", outgoing: false, props: { topic: "dataset: Davis" } },
  ],
}; // prettier-ignore

function panel(props: Partial<Parameters<typeof GraphPanel>[0]> = {}) {
  const all = { projectId: "pr", canEdit: true, busy: false, selected: 2, refreshKey: 0, onBuild: vi.fn(), ...props };
  render(<GraphPanel {...all} />);
  return all;
}

describe("GraphPanel", () => {
  it("draws papers linked to methods and datasets, and shows a node's details on click", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, GRAPH))
      .mockResolvedValueOnce(json(200, DAVIS))
      .mockResolvedValueOnce(json(200, P1));
    vi.stubGlobal("fetch", fetchMock);
    const props = panel();

    expect(await screen.findByRole("img", { name: "Knowledge graph with 4 nodes and 4 links" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Method: GCN" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Dataset: Davis" }));
    const aside = await screen.findByRole("complementary", { name: "Details: Davis" });
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `${API_URL}/projects/pr/kg?types=Paper&types=Method&types=Dataset&types=Metric&types=Limitation&types=ResearchQuestion&types=Objective&types=Gap`,
    );
    expect(String(fetchMock.mock.calls[1][0])).toBe(`${API_URL}/projects/pr/kg/nodes/Dataset%3Adavis`);
    expect(within(aside).getByText("Evaluates (2)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dataset: Davis" })).toHaveAttribute("aria-pressed", "true");

    // Neighbours are links to their own details
    await userEvent.click(within(aside).getByRole("button", { name: /GraphDTA: affinity/ }));
    const paper = await screen.findByRole("complementary", { name: "Details: GraphDTA: affinity with GNNs" });
    expect(within(paper).getByText("Bioinformatics")).toBeInTheDocument();
    expect(within(paper).getByText("82%")).toBeInTheDocument();
    expect(within(paper).getByText("Contradicted by (1)")).toBeInTheDocument();
    expect(within(paper).getByText("dataset: Davis")).toBeInTheDocument();
    expect(within(paper).getByRole("link", { name: "Open paper" })).toHaveAttribute(
      "href",
      "/projects/pr/literature/papers/p1",
    );

    await userEvent.click(screen.getByRole("button", { name: "Rebuild graph" }));
    expect(props.onBuild).toHaveBeenCalledOnce();
  });

  it("filters node types through the API", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, GRAPH))
      .mockResolvedValue(json(200, { nodes: GRAPH.nodes.filter((n) => n.type !== "Method"), edges: [] }));
    vi.stubGlobal("fetch", fetchMock);
    panel();

    await screen.findByRole("button", { name: "Method: GCN" });
    await userEvent.click(screen.getByRole("checkbox", { name: /Methods/ }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Method: GCN" })).not.toBeInTheDocument());
    const url = String(fetchMock.mock.calls.at(-1)![0]);
    expect(url.startsWith(`${API_URL}/projects/pr/kg?types=Paper`)).toBe(true);
    expect(url).not.toContain("types=Method");
  });

  it("ignores a slower, outdated response when filters change quickly", async () => {
    let releaseFirst: (r: Response) => void = () => undefined;
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => (releaseFirst = resolve)))
      .mockResolvedValueOnce(json(200, { nodes: GRAPH.nodes.filter((n) => n.type !== "Method"), edges: [] }));
    vi.stubGlobal("fetch", fetchMock);
    panel();

    await userEvent.click(screen.getByRole("checkbox", { name: /Methods/ }));
    expect(await screen.findByRole("button", { name: "Dataset: Davis" })).toBeInTheDocument();
    releaseFirst(json(200, GRAPH)); // the first (all types) answer arrives last
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: "Method: GCN" })).not.toBeInTheDocument();
  });

  it("explains how to get a graph when there is none yet", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, { nodes: [], edges: [] })));
    panel({ selected: 0 });

    expect(await screen.findByText(/Select papers on the Discover tab/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Build graph" })).toBeDisabled();
  });

  it("is read-only for viewers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, GRAPH)));
    panel({ canEdit: false });

    await screen.findByRole("img");
    expect(screen.queryByRole("button", { name: /Build graph|Rebuild graph/ })).not.toBeInTheDocument();
  });
});
