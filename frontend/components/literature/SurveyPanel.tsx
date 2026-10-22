"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { MarkdownView } from "@/components/editor/MarkdownView";
import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import { gapsApi } from "@/lib/gaps";
import type { Job } from "@/lib/jobs";
import { STATUS_META } from "@/lib/stages";
import {
  numberCitations,
  surveyApi,
  wordDiff,
  type Refinement,
  type Survey,
} from "@/lib/survey";
import { workflowApi, type StageState } from "@/lib/workflow";

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none";

const KIND_LABEL: Record<Refinement["kind"], string> = {
  modify_objective: "Change objective",
  modify_question: "Change research question",
  add_question: "New research question",
  add_objective: "New objective",
};

function Diff({ before, after }: { before: string; after: string }) {
  return (
    <p className="text-sm leading-relaxed text-slate-800" aria-label="Changes">
      {wordDiff(before, after).map((part, i) =>
        part.change === "same" ? (
          <span key={i}>{part.text}</span>
        ) : part.change === "removed" ? (
          <del key={i} className="bg-red-50 text-red-700">
            {part.text}
          </del>
        ) : (
          <ins key={i} className="bg-emerald-50 text-emerald-800 no-underline">
            {part.text}
          </ins>
        ),
      )}
    </p>
  );
}

function RefinementCard({
  item,
  canEdit,
  onDecide,
}: {
  item: Refinement;
  canEdit: boolean;
  onDecide: (accept: boolean) => void;
}) {
  const heading = `${KIND_LABEL[item.kind]}${item.target ? ` ${item.target}` : ""}`;
  return (
    <article
      aria-label={heading}
      className={`space-y-2 rounded-lg border p-3 ${item.status === "proposed" ? "border-slate-200 bg-white" : "border-slate-200 bg-slate-50"}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-800">{heading}</span>
        {item.kind === "add_question" &&
          item.linked_objective_ids.length > 0 && (
            <span className="text-xs text-slate-500">
              serves {item.linked_objective_ids.join(", ")}
            </span>
          )}
        {item.status === "accepted" && (
          <Badge className="bg-emerald-100 text-emerald-800">
            Applied
            {item.applied_ids.length
              ? ` as ${item.applied_ids.join(", ")}`
              : ""}
          </Badge>
        )}
        {item.status === "rejected" && (
          <Badge className="bg-slate-200 text-slate-700">Rejected</Badge>
        )}
      </div>
      {item.before !== null ? (
        <Diff before={item.before} after={item.text} />
      ) : (
        <div className="space-y-1 text-sm">
          <p className="bg-emerald-50 text-emerald-800">+ {item.text}</p>
          {item.question_text && (
            <p className="bg-emerald-50 text-emerald-800">
              + Research question: {item.question_text}
            </p>
          )}
        </div>
      )}
      {item.rationale && (
        <p className="text-xs text-slate-600">{item.rationale}</p>
      )}
      {item.gaps.length > 0 && (
        <ul className="space-y-0.5 text-xs text-slate-500">
          {item.gaps.map((g) => (
            <li key={g.id}>Addresses the potential gap: {g.statement}</li>
          ))}
        </ul>
      )}
      {canEdit && item.status === "proposed" && (
        <div className="flex gap-2">
          <Button className="h-8" onClick={() => onDecide(true)}>
            Accept
          </Button>
          <Button
            className="h-8"
            variant="secondary"
            onClick={() => onDecide(false)}
          >
            Reject
          </Button>
        </div>
      )}
    </article>
  );
}

/** L11: refine the plan from accepted gaps; write, check and approve the Literature Survey section. */
export function SurveyPanel({
  projectId,
  canEdit,
  busy,
  refreshKey,
  onStart,
  onChanged,
}: {
  projectId: string;
  canEdit: boolean; // the paper library / plan refinements can be changed
  busy: boolean;
  refreshKey: number;
  onStart: (action: () => Promise<Job>) => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<Refinement[] | null>(null);
  const [survey, setSurvey] = useState<Survey | null>(null);
  const [stage, setStage] = useState<StageState | null>(null);
  const [accepted, setAccepted] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [refs, s, state, gaps] = await Promise.all([
        surveyApi.refinements(projectId),
        surveyApi.survey(projectId),
        workflowApi.state(projectId),
        gapsApi.list(projectId),
      ]);
      setItems(refs);
      setSurvey(s);
      setStage(state.stages.find((x) => x.name === "literature") ?? null);
      setAccepted(gaps.filter((g) => g.status === "accepted").length);
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load the Literature Survey",
      );
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work");
    }
  }

  const numbered = useMemo(
    () =>
      survey?.content
        ? numberCitations(survey.content, survey.citations)
        : null,
    [survey],
  );

  if (!items || !survey || !stage)
    return error ? (
      <ErrorAlert>{error}</ErrorAlert>
    ) : (
      <Spinner label="Loading" />
    );

  const status = STATUS_META[survey.stage_status];
  const reviewing = survey.stage_status === "awaiting_review";
  const proposed = items.filter((i) => i.status === "proposed");
  const decided = items.filter((i) => i.status !== "proposed");
  const write = () =>
    onStart(
      async () =>
        (await workflowApi.run(projectId, "literature", feedback || undefined))
          .job,
    );
  const rewrite = () =>
    onStart(
      async () =>
        (
          await workflowApi.regenerate(
            projectId,
            "literature",
            feedback || undefined,
          )
        ).job,
    );

  return (
    <div className="space-y-6">
      {error && <ErrorAlert>{error}</ErrorAlert>}

      <section
        aria-labelledby="refine-heading"
        className="space-y-3 rounded-xl border border-slate-200 bg-white p-5"
      >
        <div>
          <h2 id="refine-heading" className="text-base font-semibold">
            Refine objectives and research questions
          </h2>
          <p className="text-sm text-slate-500">
            The agent suggests changes to your plan for the potential gaps you
            accepted. Accepting a change edits your approved research plan;
            stages already worked on after it are marked stale.
          </p>
        </div>
        {canEdit && (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              onClick={() =>
                onStart(() => surveyApi.suggestRefinements(projectId))
              }
              disabled={busy || accepted === 0}
            >
              Suggest refinements
            </Button>
            {accepted === 0 && (
              <span className="text-xs text-slate-500">
                Accept potential gaps first (Gaps tab).
              </span>
            )}
          </div>
        )}
        {items.length === 0 ? (
          <p className="text-sm text-slate-500">No suggestions yet.</p>
        ) : (
          <div className="space-y-2">
            {[...proposed, ...decided].map((item) => (
              <RefinementCard
                key={item.id}
                item={item}
                canEdit={canEdit}
                onDecide={(accept) =>
                  void act(() => surveyApi.decide(projectId, item.id, accept))
                }
              />
            ))}
          </div>
        )}
      </section>

      <section
        aria-labelledby="survey-heading"
        className="space-y-4 rounded-xl border border-slate-200 bg-white p-5"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 id="survey-heading" className="text-base font-semibold">
                Literature Survey section
              </h2>
              <Badge className={status.className}>{status.label}</Badge>
            </div>
            <p className="text-sm text-slate-500">
              Written by theme from your selected papers, with every claim
              cited, ending with a summary of the potential gaps. Comparison
              tables you inserted are kept.
            </p>
          </div>
          <Button
            variant="secondary"
            onClick={() => void act(() => surveyApi.downloadReport(projectId))}
          >
            Download analysis report
          </Button>
        </div>

        {survey.stage_status === "stale" && (
          <p
            role="status"
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
          >
            The research plan or an earlier stage changed after this section was
            written. Rewrite it to bring it up to date.
          </p>
        )}

        {canEdit && (stage.can_run || stage.can_regenerate) && (
          <div className="space-y-2">
            <label className="block text-sm">
              <span className="font-medium text-slate-700">
                {reviewing
                  ? "What should change?"
                  : "Instructions for the writer (optional)"}
              </span>
              <textarea
                className={`${INPUT} mt-1`}
                rows={2}
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="e.g. Put evaluation practice first; keep it under 900 words."
              />
            </label>
            <div className="flex flex-wrap gap-2">
              {stage.can_run && (
                <Button onClick={write} disabled={busy}>
                  {survey.content
                    ? "Rewrite the section"
                    : "Write the Literature Survey"}
                </Button>
              )}
              {reviewing && stage.can_regenerate && (
                <Button variant="secondary" onClick={rewrite} disabled={busy}>
                  Rewrite with this feedback
                </Button>
              )}
              {reviewing && (
                <Button
                  variant="secondary"
                  onClick={() =>
                    void act(() => workflowApi.approve(projectId, "literature"))
                  }
                  disabled={busy}
                >
                  Approve section
                </Button>
              )}
            </div>
          </div>
        )}
        {!stage.can_run && stage.blocked_by.length > 0 && (
          <p className="text-xs text-slate-500">
            Approve the research plan (Idea stage) first.
          </p>
        )}

        {numbered ? (
          <>
            <div
              role="status"
              className={`rounded-lg px-3 py-2 text-sm ${survey.unresolved.length || survey.uncited_paragraphs ? "bg-amber-50 text-amber-800" : "bg-emerald-50 text-emerald-800"}`}
            >
              {survey.citations.length} papers cited
              {survey.unresolved.length
                ? ` · ${survey.unresolved.length} citation${survey.unresolved.length === 1 ? "" : "s"} don't resolve to a verified paper`
                : " · every citation resolves to a verified paper"}
              {survey.uncited_paragraphs > 0 &&
                ` · ${survey.uncited_paragraphs} paragraph${survey.uncited_paragraphs === 1 ? " has" : "s have"} no citation`}
            </div>
            <div className="rounded-lg border border-slate-100 p-4">
              <MarkdownView markdown={numbered.text} />
            </div>
            {numbered.references.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-slate-700">
                  Cited papers
                </h3>
                <ol className="mt-2 space-y-1 text-sm">
                  {numbered.references.map((r) => (
                    <li key={r.paper_id} className="flex gap-2">
                      <span className="text-slate-500">[{r.n}]</span>
                      <span>
                        {r.authors && (
                          <span className="text-slate-600">{r.authors}. </span>
                        )}
                        <Link
                          href={`/projects/${projectId}/literature/papers/${r.paper_id}`}
                          className="text-indigo-700 hover:underline"
                        >
                          {r.title}
                        </Link>
                        {r.year && (
                          <span className="text-slate-600"> ({r.year})</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            <Link
              href={`/projects/${projectId}/sections/literature_survey`}
              className="inline-block text-sm font-medium text-indigo-700 hover:underline"
            >
              Edit in the section editor
            </Link>
          </>
        ) : (
          <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
            No section yet. Write it once your papers are selected and analysed
            and you have reviewed the potential gaps.
          </p>
        )}
      </section>
    </div>
  );
}
