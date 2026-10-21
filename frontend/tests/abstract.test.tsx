import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AbstractPanel } from "@/components/idea/AbstractPanel";
import type { AbstractInfo } from "@/lib/idea";

vi.mock("@/components/editor/SectionEditor", () => ({
  SectionEditor: ({ type }: { type: string }) => <div data-testid="editor">{type} editor</div>,
}));

const EMPTY: AbstractInfo = {
  content: null,
  word_count: 0,
  min_words: 150,
  max_words: 250,
  generated_at: null,
  edited: false,
  outdated: false,
};
const DRAFTED: AbstractInfo = { ...EMPTY, content: "Text", word_count: 212, generated_at: new Date().toISOString() };

function panel(abstract: AbstractInfo, canAct = true, onDraft = vi.fn()) {
  return render(
    <AbstractPanel
      projectId="p1"
      abstract={abstract}
      venue="IEEE Conference"
      canAct={canAct}
      busy={false}
      onDraft={onDraft}
      onChanged={vi.fn()}
    />,
  );
}

describe("AbstractPanel", () => {
  it("offers to draft when there is no abstract yet, with the venue's target", async () => {
    const onDraft = vi.fn();
    panel(EMPTY, true, onDraft);

    expect(screen.getByText("Target 150–250 words (IEEE Conference)")).toBeInTheDocument();
    expect(screen.queryByTestId("editor")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Draft abstract" }));
    expect(onDraft).toHaveBeenCalledOnce();
  });

  it("shows the draft in the section editor with its word count", () => {
    panel(DRAFTED);

    expect(screen.getByTestId("editor")).toHaveTextContent("abstract editor");
    expect(screen.getByText("212 words")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redraft abstract" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("flags a length outside the target, researcher edits and an outdated plan", () => {
    panel({ ...DRAFTED, word_count: 290, edited: true, outdated: true });

    expect(screen.getByText("290 words · too long")).toBeInTheDocument();
    expect(screen.getByText("Edited by you")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("The plan changed after this abstract was drafted");
  });

  it("hides the draft button when the researcher can't act", () => {
    panel(DRAFTED, false);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
