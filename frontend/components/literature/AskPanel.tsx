"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Badge, Button, ErrorAlert } from "@/components/ui";
import { filesApi } from "@/lib/api";
import {
  literatureApi,
  sectionAnchor,
  SECTION_KIND_LABEL,
  splitCitations,
  type Answer,
  type AskSectionKind,
  type LiteratureWorkspace,
  type Source,
} from "@/lib/literature";

type Exchange = {
  question: string;
  scope: AskSectionKind | "";
  answer: Answer | null;
  error: string | null;
};

const SCOPES: { value: AskSectionKind | ""; label: string }[] = [
  { value: "", label: "Whole papers" },
  { value: "dataset", label: "Data & setup" },
  { value: "methodology", label: "Methodology" },
  { value: "results", label: "Results" },
  { value: "limitations", label: "Limitations" },
  { value: "abstract", label: "Abstracts" },
];

const EXAMPLES = [
  "Which datasets are used for binding affinity prediction?",
  "Which evaluation metrics do the papers report?",
  "What limitations do the authors mention?",
];

function SourceCard({
  projectId,
  source,
}: {
  projectId: string;
  source: Source;
}) {
  const [error, setError] = useState<string | null>(null);

  async function openPdf() {
    if (!source.pdf_file_id) return;
    try {
      const { url } = await filesApi.downloadUrl(projectId, source.pdf_file_id);
      window.open(
        source.page ? `${url}#page=${source.page}` : url,
        "_blank",
        "noopener",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open the PDF");
    }
  }

  const anchor = source.section ? `#${sectionAnchor(source.section)}` : "";
  return (
    <div
      id={`src-${source.chunk_id}`}
      className="space-y-1.5 rounded-lg border border-slate-200 bg-white p-3 text-sm"
    >
      <p className="flex flex-wrap items-center gap-2">
        <Badge className="bg-indigo-100 text-indigo-800">{source.marker}</Badge>
        <span className="font-medium text-slate-900">{source.paper_title}</span>
        {source.year && (
          <span className="text-xs text-slate-500">{source.year}</span>
        )}
      </p>
      <p className="text-xs text-slate-500">
        {source.section ?? "Section unknown"}
        {source.page && ` · p. ${source.page}`}
        {source.kind &&
          ` · ${SECTION_KIND_LABEL[source.kind as keyof typeof SECTION_KIND_LABEL] ?? source.kind}`}
      </p>
      <blockquote className="border-l-2 border-slate-200 pl-3 text-slate-700">
        {source.text.length > 600
          ? `${source.text.slice(0, 600)}…`
          : source.text}
      </blockquote>
      <p className="flex flex-wrap gap-3 text-xs">
        <Link
          href={`/projects/${projectId}/literature/papers/${source.paper_id}${anchor}`}
          className="font-medium text-indigo-700 hover:underline"
        >
          Open in paper
        </Link>
        {source.pdf_file_id && (
          <button
            type="button"
            className="font-medium text-indigo-700 hover:underline"
            onClick={() => void openPdf()}
          >
            PDF{source.page ? ` at p. ${source.page}` : ""}
          </button>
        )}
      </p>
      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
  );
}

function AnswerView({
  projectId,
  answer,
}: {
  projectId: string;
  answer: Answer;
}) {
  const [focused, setFocused] = useState<string | null>(null);
  const byMarker = new Map(answer.citations.map((c) => [c.marker, c]));

  return (
    <div className="space-y-3">
      {answer.insufficient && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
          The indexed passages don't fully answer this. Parse more papers or
          rephrase the question.
        </p>
      )}
      <p className="text-sm leading-relaxed whitespace-pre-line text-slate-800">
        {splitCitations(answer.answer).map((part, i) =>
          "text" in part ? (
            <span key={i}>{part.text}</span>
          ) : byMarker.has(part.marker) ? (
            <button
              key={i}
              type="button"
              aria-label={`Source ${part.marker}: ${byMarker.get(part.marker)!.paper_title}`}
              onClick={() => {
                setFocused(part.marker);
                document
                  .getElementById(`src-${byMarker.get(part.marker)!.chunk_id}`)
                  ?.scrollIntoView?.({ block: "nearest" });
              }}
              className={`mx-0.5 rounded px-1 text-xs font-semibold ${
                focused === part.marker
                  ? "bg-indigo-600 text-white"
                  : "bg-indigo-100 text-indigo-800 hover:bg-indigo-200"
              }`}
            >
              {part.marker}
            </button>
          ) : null,
        )}
      </p>
      {answer.citations.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Sources ({answer.citations.length} of {answer.retrieved} passages
            read)
          </p>
          {answer.citations.map((c) => (
            <SourceCard key={c.marker} projectId={projectId} source={c} />
          ))}
        </div>
      )}
      {answer.removed_markers.length > 0 && (
        <p className="text-xs text-slate-500">
          {answer.removed_markers.length} citation
          {answer.removed_markers.length === 1 ? "" : "s"} to passages that
          weren't retrieved{" "}
          {answer.removed_markers.length === 1 ? "was" : "were"} removed.
        </p>
      )}
    </div>
  );
}

/** "Ask the literature": questions answered only from the parsed papers, every claim with its passage. */
export function AskPanel({
  projectId,
  ws,
  canEdit,
  busy,
  onIndex,
}: {
  projectId: string;
  ws: LiteratureWorkspace;
  canEdit: boolean;
  busy: boolean;
  onIndex: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<AskSectionKind | "">("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [asking, setAsking] = useState(false);

  async function ask(e?: FormEvent, text = question) {
    e?.preventDefault();
    const q = text.trim();
    if (q.length < 3) return;
    setAsking(true);
    setQuestion("");
    setHistory((h) => [
      { question: q, scope, answer: null, error: null },
      ...h,
    ]);
    try {
      const answer = await literatureApi.ask(
        projectId,
        q,
        scope ? [scope] : null,
      );
      setHistory((h) => [{ ...h[0], answer }, ...h.slice(1)]);
    } catch (err) {
      setHistory((h) => [
        {
          ...h[0],
          error: err instanceof Error ? err.message : "Could not answer",
        },
        ...h.slice(1),
      ]);
    } finally {
      setAsking(false);
    }
  }

  const ready = ws.indexed_chunks > 0;
  return (
    <section aria-labelledby="ask-heading" className="space-y-4">
      <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
        <div>
          <h2 id="ask-heading" className="text-base font-semibold">
            Ask the literature
          </h2>
          <p className="text-sm text-slate-500">
            Answers come only from the passages of your parsed papers, and every
            claim links to its passage (paper, section, page).{" "}
            {ws.indexed_chunks} passages are searchable.
          </p>
        </div>
        {ws.pending_chunks > 0 && (
          <p
            role="status"
            className="flex flex-wrap items-center gap-2 text-sm text-amber-800"
          >
            {ws.pending_chunks} parsed passages aren't searchable yet.
            {canEdit && (
              <Button
                variant="secondary"
                className="h-8 px-3 text-xs"
                onClick={onIndex}
                disabled={busy}
              >
                Index them now
              </Button>
            )}
          </p>
        )}
        {!ready ? (
          <p className="text-sm text-slate-500">
            Get the full text of some selected papers first (Full text tab).
          </p>
        ) : (
          <form onSubmit={(e) => void ask(e)} className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <input
                className="h-10 min-w-64 flex-1 rounded-md border border-slate-300 px-3 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none"
                placeholder="e.g. Which datasets are used for binding affinity prediction?"
                aria-label="Your question"
                maxLength={1000}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
              <select
                className="h-10 rounded-md border border-slate-300 bg-white px-2 text-sm"
                aria-label="Search in"
                value={scope}
                onChange={(e) =>
                  setScope(e.target.value as AskSectionKind | "")
                }
              >
                {SCOPES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
              <Button
                type="submit"
                loading={asking}
                disabled={question.trim().length < 3}
              >
                Ask
              </Button>
            </div>
            {history.length === 0 && (
              <p className="flex flex-wrap gap-2 text-xs">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    className="rounded-full border border-slate-200 px-2.5 py-1 text-slate-600 hover:bg-slate-50"
                    onClick={() => void ask(undefined, ex)}
                  >
                    {ex}
                  </button>
                ))}
              </p>
            )}
          </form>
        )}
      </div>

      {history.map((x, i) => (
        <article
          key={history.length - i}
          className="space-y-3 rounded-xl border border-slate-200 bg-slate-50/60 p-5"
        >
          <p className="text-sm font-medium text-slate-900">
            {x.question}
            {x.scope && (
              <Badge className="ml-2 bg-slate-200 text-slate-700">
                {SCOPES.find((s) => s.value === x.scope)?.label}
              </Badge>
            )}
          </p>
          {x.error ? (
            <ErrorAlert>{x.error}</ErrorAlert>
          ) : x.answer ? (
            <AnswerView projectId={projectId} answer={x.answer} />
          ) : (
            <p role="status" className="text-sm text-slate-500">
              Reading the passages…
            </p>
          )}
        </article>
      ))}
    </section>
  );
}
