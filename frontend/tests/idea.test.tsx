import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PlanCards } from "@/components/idea/PlanCards";
import { nextId, planProblems, type IdeaPlan } from "@/lib/idea";

const PLAN: IdeaPlan = {
  refined_idea: "Rank drug candidates with graph neural networks.",
  domain: "Computational drug discovery",
  keywords: ["GNN", "drug discovery", "binding affinity"],
  subtopics: ["graph representation", "affinity prediction"],
  problem_statement: "Experimental screening is slow.",
  motivation: "Earlier ranking saves lab time.",
  scope: "Small molecules only.",
  expected_contributions: ["A ranking model", "A benchmark protocol"],
  objectives: [
    { id: "O1", text: "Design a GNN." },
    { id: "O2", text: "Evaluate ranking quality." },
    { id: "O3", text: "Analyse failure cases." },
  ],
  research_questions: [
    { id: "RQ1", text: "Does the GNN beat fingerprints?", kind: "question", linked_objective_ids: ["O1", "O2"] },
    { id: "RQ2", text: "Errors cluster by molecular family.", kind: "hypothesis", linked_objective_ids: ["O3"] },
  ],
  research_directions: ["Transfer to new targets", "Uncertainty-aware ranking"],
  title_options: ["GNNs for Drug Ranking", "GNN-Rank", "Can GNNs Rank Drugs Better?"],
  title: "GNNs for Drug Ranking",
};

function card(name: string) {
  return within(screen.getByRole("region", { name }));
}

describe("plan helpers", () => {
  it("finds the next free id", () => {
    expect(nextId("O", PLAN.objectives)).toBe("O4");
    expect(nextId("RQ", [])).toBe("RQ1");
  });

  it("reports the same link problems the server rejects", () => {
    expect(planProblems(PLAN)).toEqual([]);
    const removed = { ...PLAN, objectives: PLAN.objectives.slice(0, 2) };
    expect(planProblems(removed)).toContain("RQ2 links to removed objective O3");
    const orphan = { ...PLAN, research_questions: [PLAN.research_questions[0]] };
    expect(planProblems(orphan)).toEqual(expect.arrayContaining(["No research question serves O3", "Keep at least 2 research questions"]));
    const questionHypothesis = {
      ...PLAN,
      research_questions: [PLAN.research_questions[0], { ...PLAN.research_questions[1], text: "Do errors cluster?" }],
    };
    expect(planProblems(questionHypothesis)).toEqual(["RQ2 is a hypothesis: state it as a claim, or mark it as a question"]);
  });
});

describe("PlanCards", () => {
  it("shows every part of the plan read-only", () => {
    render(<PlanCards plan={PLAN} editable={false} onSave={vi.fn()} />);

    expect(card("Title").getByText("GNNs for Drug Ranking")).toBeInTheDocument();
    expect(card("Objectives").getByText("Design a GNN.")).toBeInTheDocument();
    expect(card("Research questions & hypotheses").getByText("hypothesis")).toBeInTheDocument();
    expect(card("Possible research directions").getByText("Transfer to new targets")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
  });

  it("edits an objective and saves the whole plan", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PlanCards plan={PLAN} editable onSave={onSave} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit Objectives" }));
    const o2 = screen.getByLabelText("Objective O2");
    await userEvent.clear(o2);
    await userEvent.type(o2, "Evaluate ranking quality against three baselines.");
    await userEvent.click(card("Objectives").getByRole("button", { name: "Save" }));

    const saved: IdeaPlan = onSave.mock.calls[0][0];
    expect(saved.objectives[1]).toEqual({ id: "O2", text: "Evaluate ranking quality against three baselines." });
    expect(saved.research_questions).toEqual(PLAN.research_questions);
  });

  it("won't save a hypothesis phrased as a question", async () => {
    render(<PlanCards plan={PLAN} editable onSave={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit Research questions & hypotheses" }));
    await userEvent.selectOptions(screen.getByLabelText("RQ1 type"), "hypothesis");

    const questions = card("Research questions & hypotheses");
    expect(questions.getByLabelText("Problems to fix")).toHaveTextContent("RQ1 is a hypothesis: state it as a claim");
    expect(questions.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("won't save an objective a question still depends on", async () => {
    const onSave = vi.fn();
    render(<PlanCards plan={PLAN} editable onSave={onSave} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit Objectives" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove O3" }));

    expect(card("Objectives").getByLabelText("Problems to fix")).toHaveTextContent("RQ2 links to removed objective O3");
    expect(card("Objectives").getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("links a question to objectives with checkboxes", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PlanCards plan={PLAN} editable onSave={onSave} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit Research questions & hypotheses" }));
    await userEvent.click(screen.getByLabelText("RQ2 serves O1"));
    await userEvent.selectOptions(screen.getByLabelText("RQ2 type"), "question");
    await userEvent.click(card("Research questions & hypotheses").getByRole("button", { name: "Save" }));

    const saved: IdeaPlan = onSave.mock.calls[0][0];
    expect(saved.research_questions[1].linked_objective_ids).toEqual(["O3", "O1"]);
    expect(saved.research_questions[1].kind).toBe("question");
  });

  it("picks a suggested title or writes one", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PlanCards plan={PLAN} editable onSave={onSave} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit Title" }));
    await userEvent.click(screen.getByLabelText("Can GNNs Rank Drugs Better?"));
    await userEvent.click(card("Title").getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][0].title).toBe("Can GNNs Rank Drugs Better?");

    await userEvent.click(screen.getByRole("button", { name: "Edit Title" }));
    await userEvent.type(screen.getByLabelText("Your own title"), "Ranking Molecules with Graph Learning");
    await userEvent.click(card("Title").getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[1][0].title).toBe("Ranking Molecules with Graph Learning");
  });

  it("shows the server's validation message if saving fails", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("RQ1 links to objectives that don't exist: O9"));
    render(<PlanCards plan={PLAN} editable onSave={onSave} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit Scope" }));
    await userEvent.type(card("Scope").getByRole("textbox"), " Proteins excluded.");
    await userEvent.click(card("Scope").getByRole("button", { name: "Save" }));

    expect(await card("Scope").findByRole("alert")).toHaveTextContent("don't exist: O9");
  });
});
