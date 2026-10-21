"use client";

import { useState } from "react";

import { SectionEditor } from "@/components/editor/SectionEditor";
import { Button, ErrorAlert, LinkButton, TextArea } from "@/components/ui";
import { SECTION_LABEL, STAGE_SECTION } from "@/lib/sections";
import { STAGE_LABEL, type StageName } from "@/lib/stages";

type Mode = "view" | "edit" | "reject" | "regenerate";

export type ReviewHandlers = {
  approve: (comment?: string) => Promise<void>;
  reject: (comment: string) => Promise<void>;
  edit: (content: string) => Promise<void>;
  regenerate: (feedback?: string) => Promise<void>;
};

/** The draft awaiting the researcher's decision, with every review action (A4.4). */
export function ReviewPanel({
  projectId,
  stage,
  draft,
  canAct,
  handlers,
  workspaceHref,
}: {
  projectId: string;
  stage: StageName;
  draft: string | null;
  canAct: boolean;
  handlers: ReviewHandlers;
  /** Stages with their own workspace (e.g. the research plan) are edited there instead of as text. */
  workspaceHref?: string;
}) {
  // Stages that write a paper section are reviewed in the section editor (versioned, autosaved)
  const section = STAGE_SECTION[stage];
  const [mode, setMode] = useState<Mode>("view");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function open(next: Mode) {
    setError(null);
    setText(next === "edit" ? (draft ?? "") : "");
    setMode(next);
  }

  async function submit(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setMode("view");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  const label = STAGE_LABEL[stage];

  return (
    <section
      aria-labelledby="review-heading"
      className="space-y-4 rounded-xl border-2 border-amber-300 bg-amber-50/40 p-5"
    >
      <div>
        <h2 id="review-heading" className="text-base font-semibold text-slate-900">
          Awaiting your review: {label}
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Nothing becomes final until you approve it. Edit the draft, ask for a new one, or reject it with feedback.
        </p>
      </div>

      {section ? (
        <div className="space-y-1">
          <p className="text-xs text-slate-500">
            Draft for the <strong className="font-medium text-slate-700">{SECTION_LABEL[section]}</strong> section. Edit
            it directly: changes autosave as new versions.
          </p>
          <SectionEditor projectId={projectId} type={section} compact />
        </div>
      ) : mode === "edit" ? (
        <TextArea
          label="Your version of the draft"
          id="edit-draft"
          rows={12}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      ) : (
        <div className="max-h-96 overflow-auto rounded-lg border border-slate-200 bg-white p-4 text-sm whitespace-pre-wrap text-slate-800">
          {draft || <span className="text-slate-400">(empty draft)</span>}
        </div>
      )}

      {mode === "reject" && (
        <TextArea
          label="What should change?"
          id="reject-comment"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          hint="Required. The agent sees this when the stage runs again."
        />
      )}
      {mode === "regenerate" && (
        <TextArea
          label="Feedback for the new draft (optional)"
          id="regenerate-feedback"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      )}

      {error && <ErrorAlert>{error}</ErrorAlert>}

      {canAct && (
        <div className="flex flex-wrap gap-2">
          {mode === "view" && (
            <>
              <Button onClick={() => submit(() => handlers.approve())} loading={busy}>
                Approve
              </Button>
              {workspaceHref ? (
                <LinkButton variant="secondary" href={workspaceHref}>
                  Edit in workspace
                </LinkButton>
              ) : (
                !section && (
                  <Button variant="secondary" onClick={() => open("edit")} disabled={busy}>
                    Edit
                  </Button>
                )
              )}
              <Button variant="secondary" onClick={() => open("regenerate")} disabled={busy}>
                Regenerate
              </Button>
              <Button variant="danger-outline" onClick={() => open("reject")} disabled={busy}>
                Reject
              </Button>
            </>
          )}
          {mode === "edit" && (
            <Button onClick={() => submit(() => handlers.edit(text))} loading={busy} disabled={!text.trim()}>
              Save edit
            </Button>
          )}
          {mode === "reject" && (
            <Button variant="danger" onClick={() => submit(() => handlers.reject(text.trim()))} loading={busy} disabled={!text.trim()}>
              Reject draft
            </Button>
          )}
          {mode === "regenerate" && (
            <Button onClick={() => submit(() => handlers.regenerate(text))} loading={busy}>
              Regenerate draft
            </Button>
          )}
          {mode !== "view" && (
            <Button variant="secondary" onClick={() => setMode("view")} disabled={busy}>
              Cancel
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
