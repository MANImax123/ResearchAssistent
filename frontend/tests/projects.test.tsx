import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/(app)/dashboard/page";
import { ProjectForm } from "@/components/ProjectForm";
import { StageTracker } from "@/components/StageTracker";
import type { ProjectDetail, ProjectSummary } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { STAGES } from "@/lib/stages";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: "u1", name: "Ada Lovelace", email: "ada@example.com", created_at: "" } }),
}));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const project: ProjectSummary = {
  id: "p1",
  title: "Graph neural networks for drug discovery",
  domain: "Bioinformatics",
  target_venue: "springer_lncs",
  current_stage: "literature",
  status: "active",
  role: "owner",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

describe("Dashboard", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("shows the empty state when there are no projects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [])));
    render(<DashboardPage />);

    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create your first project" })).toHaveAttribute("href", "/projects/new");
  });

  it("renders a card per project with title, domain, venue and stage", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [project, { ...project, id: "p2", title: "Second" }])));
    render(<DashboardPage />);

    const card = (await screen.findByText(project.title)).closest("a")!;
    expect(card).toHaveAttribute("href", "/projects/p1");
    expect(within(card).getByText("Bioinformatics · Springer LNCS")).toBeInTheDocument();
    expect(within(card).getByText("Literature Survey")).toBeInTheDocument();
    expect(within(card).getByText("Stage 2 of 9")).toBeInTheDocument();
    expect(within(card).getByText("Updated just now")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("shows an error with retry when loading fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(500, { detail: "boom" }))
      .mockResolvedValueOnce(json(200, [project]));
    vi.stubGlobal("fetch", fetchMock);
    render(<DashboardPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(project.title)).toBeInTheDocument();
  });
});

describe("StageTracker", () => {
  it("lists all 9 stages in order with status and current stage", () => {
    const stages: ProjectDetail["stages"] = STAGES.map((s) => ({
      name: s.name,
      status: s.name === "idea" ? "approved" : "not_started",
      approved_at: null,
    }));
    render(<StageTracker stages={stages} current="literature" />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(9);
    expect(items.map((li) => li.querySelector("p")!.textContent)).toEqual([
      "Idea & Problem",
      "Literature SurveyCurrent stage",
      ...STAGES.slice(2).map((s) => s.label),
    ]);
    expect(items[0]).toHaveTextContent("Approved");
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(within(items[8]).getByText("Not started")).toBeInTheDocument();
  });
});

describe("ProjectForm", () => {
  it("requires a title and submits trimmed values with blanks as null", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm submitLabel="Create project" onSubmit={onSubmit} />);

    await userEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Title is required.");
    expect(onSubmit).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText("Title"), "  My paper  ");
    await userEvent.selectOptions(screen.getByLabelText("Target venue"), "acm");
    await userEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(onSubmit).toHaveBeenCalledWith({ title: "My paper", domain: null, raw_idea: null, target_venue: "acm" });
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-10-20T12:00:00Z");
  it.each([
    ["2026-10-20T11:59:30Z", "just now"],
    ["2026-10-20T11:55:00Z", "5 minutes ago"],
    ["2026-10-20T09:00:00Z", "3 hours ago"],
    ["2026-10-19T12:00:00Z", "yesterday"],
  ])("%s → %s", (iso, expected) => {
    expect(relativeTime(iso, now)).toBe(expected);
  });
});
