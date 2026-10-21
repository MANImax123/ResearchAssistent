import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CritiquePanel } from "@/components/idea/CritiquePanel";
import type { Critique, CritiqueSet } from "@/lib/idea";

function critique(prefix: string, score: number, ids: [string, string[]][]): Critique {
  return {
    score,
    summary: `${prefix} summary about O2 and RQ1.`,
    strengths: [`${prefix} strength`],
    risks: [`${prefix} risk`],
    suggestions: ids.map(([id, affects]) => ({ id, title: `${prefix} fix ${id}`, detail: `Change ${id} details.`, affects })),
  };
}

const SET: CritiqueSet = {
  round: 1,
  generated_at: new Date().toISOString(),
  critiques: {
    novelty: critique("Novelty", 2, [["N1", ["expected_contributions"]], ["N2", ["refined_idea"]]]),
    feasibility: critique("Feasibility", 4, [["F1", ["scope"]]]),
    methodology: critique("Methodology", 3, [["M1", ["objectives"]], ["M2", ["research_questions"]]]),
  },
  agent_run_ids: {},
  applied: [],
};

describe("CritiquePanel", () => {
  it("shows the three critiques side by side with scores and affected fields", () => {
    render(<CritiquePanel critiques={SET} canAct busy={false} onRequest={vi.fn()} onApply={vi.fn()} />);

    const novelty = screen.getByRole("article", { name: "Novelty critique" });
    expect(within(novelty).getByLabelText("Score 2 out of 5")).toBeInTheDocument();
    expect(within(novelty).getByText("Novelty strength")).toBeInTheDocument();
    expect(within(novelty).getByText("Contributions")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Feasibility critique" })).toHaveTextContent("4/5");
    expect(screen.getByRole("article", { name: "Methodology critique" })).toHaveTextContent("Research questions");
    expect(screen.getAllByRole("checkbox")).toHaveLength(5);
    expect(screen.getByRole("button", { name: /Apply\s+selected/ })).toBeDisabled();
  });

  it("applies exactly the chosen 2 of 5 suggestions", async () => {
    const onApply = vi.fn();
    render(<CritiquePanel critiques={SET} canAct busy={false} onRequest={vi.fn()} onApply={onApply} />);

    await userEvent.click(screen.getByLabelText("Apply F1: Feasibility fix F1"));
    await userEvent.click(screen.getByLabelText("Apply N1: Novelty fix N1"));
    await userEvent.click(screen.getByRole("button", { name: "Apply 2 selected suggestions" }));

    expect(onApply).toHaveBeenCalledWith(["F1", "N1"]);
  });

  it("marks applied suggestions and doesn't offer them again", () => {
    render(
      <CritiquePanel critiques={{ ...SET, applied: ["F1"] }} canAct busy={false} onRequest={vi.fn()} onApply={vi.fn()} />,
    );

    const feasibility = screen.getByRole("article", { name: "Feasibility critique" });
    expect(within(feasibility).getByText("Applied")).toBeInTheDocument();
    expect(within(feasibility).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByText(/applied: F1/)).toBeInTheDocument();
  });

  it("asks the critics and hides controls for viewers", async () => {
    const onRequest = vi.fn();
    const { rerender } = render(
      <CritiquePanel critiques={null} canAct busy={false} onRequest={onRequest} onApply={vi.fn()} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Ask the critics" }));
    expect(onRequest).toHaveBeenCalledOnce();

    rerender(<CritiquePanel critiques={SET} canAct={false} busy={false} onRequest={onRequest} onApply={vi.fn()} />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
