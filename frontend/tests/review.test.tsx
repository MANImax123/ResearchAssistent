import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ReviewPanel, type ReviewHandlers } from "@/components/ReviewPanel";
import { StagesWorkspace } from "@/components/StagesWorkspace";
import { STAGES } from "@/lib/stages";
import type { ProjectState, StageState } from "@/lib/workflow";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type MockHandlers = { [K in keyof ReviewHandlers]: Mock<ReviewHandlers[K]> };

function handlers(): MockHandlers {
  return {
    approve: vi.fn<ReviewHandlers["approve"]>().mockResolvedValue(undefined),
    reject: vi.fn<ReviewHandlers["reject"]>().mockResolvedValue(undefined),
    edit: vi.fn<ReviewHandlers["edit"]>().mockResolvedValue(undefined),
    regenerate: vi.fn<ReviewHandlers["regenerate"]>().mockResolvedValue(undefined),
  };
}

describe("ReviewPanel", () => {
  it("approves", async () => {
    const h = handlers();
    render(<ReviewPanel stage="idea" draft="Draft text" canAct handlers={h} />);

    expect(screen.getByRole("heading", { name: "Awaiting your review: Idea & Problem" })).toBeInTheDocument();
    expect(screen.getByText("Draft text")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(h.approve).toHaveBeenCalledOnce();
  });

  it("rejects only with a comment", async () => {
    const h = handlers();
    render(<ReviewPanel stage="idea" draft="Draft" canAct handlers={h} />);

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    const confirm = screen.getByRole("button", { name: "Reject draft" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What should change?"), "  Too broad ");
    await userEvent.click(confirm);
    expect(h.reject).toHaveBeenCalledWith("Too broad");
  });

  it("edits starting from the current draft", async () => {
    const h = handlers();
    render(<ReviewPanel stage="idea" draft="Old draft" canAct handlers={h} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const box = screen.getByLabelText("Your version of the draft");
    expect(box).toHaveValue("Old draft");
    await userEvent.clear(box);
    await userEvent.type(box, "New draft");
    await userEvent.click(screen.getByRole("button", { name: "Save edit" }));
    expect(h.edit).toHaveBeenCalledWith("New draft");
  });

  it("regenerates with optional feedback and shows errors", async () => {
    const h = handlers();
    h.regenerate.mockRejectedValueOnce(new Error("A stage is running; wait for it to finish"));
    render(<ReviewPanel stage="literature" draft="D" canAct handlers={h} />);

    await userEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await userEvent.type(screen.getByLabelText("Feedback for the new draft (optional)"), "More 2024 papers");
    await userEvent.click(screen.getByRole("button", { name: "Regenerate draft" }));

    expect(h.regenerate).toHaveBeenCalledWith("More 2024 papers");
    expect(await screen.findByRole("alert")).toHaveTextContent("A stage is running");
  });

  it("is read-only for viewers", () => {
    render(<ReviewPanel stage="idea" draft="D" canAct={false} handlers={handlers()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

function stage(name: StageState["name"], patch: Partial<StageState> = {}): StageState {
  return {
    name,
    status: "not_started",
    approved_at: null,
    prerequisites: [],
    blocked_by: [],
    can_run: false,
    can_regenerate: false,
    can_go_back: false,
    ...patch,
  };
}

function projectState(stages: Partial<Record<StageState["name"], Partial<StageState>>>, extra: Partial<ProjectState> = {}): ProjectState {
  return {
    project_id: "p1",
    version: 3,
    updated_at: null,
    state: { current_stage: "literature" },
    stages: STAGES.map((s) => stage(s.name, stages[s.name])),
    pending_review: null,
    checkpoint: null,
    ...extra,
  };
}

describe("StagesWorkspace", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("shows per-stage actions, blocked hints and the stale banner", async () => {
    const s = projectState({
      idea: { status: "approved", can_go_back: true, can_regenerate: true },
      literature: { status: "stale", can_run: true, can_go_back: true },
      methodology: { blocked_by: ["literature"] },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, s)));
    render(<StagesWorkspace projectId="p1" canEdit />);

    const banner = (await screen.findByText(/Needs review:/)).closest('[role="status"]');
    expect(banner).toHaveTextContent("Needs review: Literature Survey");
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByRole("button", { name: "Go back" })).toBeInTheDocument();
    expect(within(rows[0]).getByRole("button", { name: "Regenerate" })).toBeInTheDocument();
    expect(within(rows[1]).getByRole("button", { name: "Run again" })).toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("Needs Literature Survey approved");
    expect(within(rows[2]).queryByRole("button")).not.toBeInTheDocument();
  });

  it("go back asks for confirmation, then shows the reopened review", async () => {
    const before = projectState({ idea: { status: "approved", can_go_back: true } });
    const after = projectState(
      { idea: { status: "awaiting_review" }, literature: { status: "stale" } },
      { pending_review: { stage: "idea", draft: "The idea draft" } },
    );
    const fetchMock = vi.fn().mockResolvedValueOnce(json(200, before)).mockResolvedValueOnce(json(200, after));
    vi.stubGlobal("fetch", fetchMock);
    render(<StagesWorkspace projectId="p1" canEdit />);

    await userEvent.click(await screen.findByRole("button", { name: "Go back" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Go back" }));

    expect(await screen.findByRole("heading", { name: "Awaiting your review: Idea & Problem" })).toBeInTheDocument();
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/projects\/p1\/stages\/idea\/go-back$/);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("hides actions from viewers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, projectState({ idea: { can_run: true } }))));
    render(<StagesWorkspace projectId="p1" canEdit={false} />);

    await screen.findAllByRole("listitem");
    expect(screen.queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
  });
});
