import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import MethodologyWorkspacePage from "@/app/(app)/projects/[id]/methodology/page";
import { API_URL } from "@/lib/api";
import { EMPTY_INPUT, cleaned, type MethodologyWorkspace } from "@/lib/methodology";

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "pr" }) }));
vi.mock("@/components/FilesPanel", () => ({ FilesPanel: () => <div>files</div> }));

afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const PROJECT = { id: "pr", title: "GNN drug ranking", role: "owner" };

const WS: MethodologyWorkspace = {
  input: { ...EMPTY_INPUT, algorithms: "A 3-layer GCN." },
  version: 1,
  updated_at: new Date().toISOString(),
  editable: true,
  blocked_reason: null,
  stage_status: "not_started",
  missing: [
    { field: "datasets", label: "Datasets", reason: "No dataset is listed.", essential: true },
    { field: "metrics", label: "Evaluation metrics", reason: "No evaluation metric is given.", essential: true },
    { field: "tools", label: "Tools, frameworks and libraries", reason: "No tools are listed.", essential: false },
  ],
  files: { diagram: 0, code: 0, notes: 0 },
  questions: [
    {
      id: "q1", field: "metrics", label: "Evaluation metrics", kind: "missing", status: "open", answer: null,
      question: "Which metrics do you report on Davis?", reason: "RQ1 compares ranking quality.",
      answered_at: null, created_at: new Date().toISOString(),
    },
    {
      id: "q2", field: "algorithms", label: "Algorithms / model", kind: "unclear", status: "answered",
      answer: "Adam, lr 0.001, 100 epochs.", question: "Which optimiser is 'standard'?", reason: null,
      answered_at: new Date().toISOString(), created_at: new Date().toISOString(),
    },
  ],
}; // prettier-ignore

function routes(ws: MethodologyWorkspace = WS, overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).replace(API_URL, "");
    const method = init?.method ?? "GET";
    const key = `${method} ${path}`;
    if (overrides[key]) return overrides[key](init);
    if (path === "/projects/pr") return json(200, PROJECT);
    if (path === "/jobs?project_id=pr") return json(200, []);
    if (key === "GET /projects/pr/methodology") return json(200, ws);
    if (key === "PUT /projects/pr/methodology") {
      const body = JSON.parse(String(init?.body));
      return json(200, { ...ws, input: { ...EMPTY_INPUT, ...body.input }, version: ws.version + 1 });
    }
    if (key === "POST /projects/pr/methodology/follow-ups") return json(202, { id: "j1", status: "pending", progress: 0 });
    if (key.startsWith("PATCH /projects/pr/methodology/questions/")) return json(200, {});
    return json(404, { detail: `unexpected ${key}` });
  });
}

describe("cleaned", () => {
  it("drops unnamed rows and hyperparameters without a value", () => {
    const input = {
      ...EMPTY_INPUT,
      datasets: [{ name: " ", source: "x", size: "", features: "", notes: "" }],
      hyperparameters: [{ name: "lr", value: "", notes: "" }, { name: "epochs", value: "100", notes: "" }],
    };
    expect(cleaned(input).datasets).toEqual([]);
    expect(cleaned(input).hyperparameters.map((h) => h.name)).toEqual(["epochs"]);
  });
});

describe("Methodology workspace", () => {
  it("flags missing fields, saves structured rows with the base version", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    render(<MethodologyWorkspacePage />);

    const missing = await screen.findByRole("region", { name: "What's still missing" });
    expect(within(missing).getByText("Datasets")).toBeInTheDocument();
    expect(within(missing).getAllByText("(essential)")).toHaveLength(2);
    expect(screen.getAllByText("Missing")).toHaveLength(3); // flags beside datasets, metrics, tools
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Add dataset" }));
    await userEvent.type(screen.getByLabelText("Datasets 1: Name"), "Davis");
    await userEvent.type(screen.getByLabelText("Datasets 1: Size"), "30,056 pairs");
    await userEvent.click(screen.getByRole("button", { name: "Add metric" }));
    await userEvent.click(screen.getByRole("button", { name: "Add metric" }));
    await userEvent.type(screen.getByLabelText("Evaluation metrics 1: Metric"), "RMSE");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    // asking needs the saved version
    expect(screen.getByRole("button", { name: "Ask again" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const put = fetchMock.mock.calls.find(([, i]) => i?.method === "PUT");
    const body = JSON.parse(String(put?.[1]?.body));
    expect(body.base_version).toBe(1);
    expect(body.input.datasets).toEqual([{ name: "Davis", source: "", size: "30,056 pairs", features: "", notes: "" }]);
    expect(body.input.metrics).toEqual([{ name: "RMSE", notes: "" }]); // the empty second row is dropped
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument());
  });

  it("shows a conflict when the methodology changed elsewhere", async () => {
    vi.stubGlobal(
      "fetch",
      routes(WS, {
        "PUT /projects/pr/methodology": () =>
          json(409, { detail: "The methodology was changed elsewhere (now version 2); reload and try again" }),
      }),
    );
    render(<MethodologyWorkspacePage />);
    await userEvent.type(await screen.findByLabelText(/Workflow/), "Featurise, train, rank.");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument();
  });

  it("answers, dismisses and asks follow-up questions", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    render(<MethodologyWorkspacePage />);

    const question = await screen.findByRole("listitem", { name: "Question about Evaluation metrics" });
    expect(within(question).getByRole("link", { name: "Evaluation metrics" })).toHaveAttribute("href", "#field-metrics");
    expect(within(question).getByRole("button", { name: "Save answer" })).toBeDisabled();
    await userEvent.type(within(question).getByLabelText(/Answer:/), "RMSE and CI.");
    await userEvent.click(within(question).getByRole("button", { name: "Save answer" }));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/methodology/questions/q1"));
      expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ status: "answered", answer: "RMSE and CI." });
    });

    const answered = screen.getByRole("listitem", { name: "Question about Algorithms / model" });
    expect(within(answered).getByText("Adam, lr 0.001, 100 epochs.")).toBeInTheDocument();
    expect(within(answered).getByText("Unclear")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Ask again" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/methodology/follow-ups") && i?.method === "POST")).toBe(true),
    );
  });

  it("is read-only until the plan is approved", async () => {
    vi.stubGlobal(
      "fetch",
      routes({ ...WS, editable: false, blocked_reason: "Approve the research plan (Idea stage) first", questions: [] }),
    );
    render(<MethodologyWorkspacePage />);

    expect(await screen.findByText(/Approve the research plan/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the Idea workspace" })).toHaveAttribute("href", "/projects/pr/idea");
    expect(screen.getByLabelText(/Algorithms \/ model/)).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ask/ })).not.toBeInTheDocument();
  });
});
