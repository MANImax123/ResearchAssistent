"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import {
  EXPLANATION_LABEL,
  focusExcerpt,
  literatureApi,
  sectionAnchor,
  type ClaimSide,
  type Contradiction,
} from "@/lib/literature";

function Side({
  projectId,
  label,
  side,
}: {
  projectId: string;
  label: string;
  side: ClaimSide;
}) {
  return (
    <div className="min-w-0 flex-1 space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
        {label}
      </p>
      <Link
        href={`/projects/${projectId}/literature/papers/${side.paper_id}`}
        className="block text-sm font-medium text-slate-900 hover:text-indigo-700"
      >
        {side.title}
        {side.year && (
          <span className="ml-1 font-normal text-slate-500">({side.year})</span>
        )}
      </Link>
      <p className="text-sm text-slate-800">“{side.claim}”</p>
      {side.evidence.map((e) => (
        <blockquote
          key={e.chunk_id}
          className="border-l-2 border-indigo-200 pl-2 text-xs text-slate-600"
        >
          <span className="font-medium text-slate-700">
            {e.section}
            {e.page && ` · p. ${e.page}`}
          </span>{" "}
          {focusExcerpt(e.text, side.claim)}{" "}
          <Link
            href={`/projects/${projectId}/literature/papers/${side.paper_id}${e.section && e.section !== "Abstract" ? `#${sectionAnchor(e.section)}` : ""}`}
            className="font-medium text-indigo-700 hover:underline"
          >
            Open
          </Link>
        </blockquote>
      ))}
    </div>
  );
}

/** L8: conflicting claims between selected papers. Researchers dismiss the ones that aren't real conflicts. */
export function ConflictsPanel({
  projectId,
  canEdit,
  busy,
  profiled,
  refreshKey,
  onDetect,
}: {
  projectId: string;
  canEdit: boolean;
  busy: boolean;
  profiled: number; // selected papers with a profile (detection needs two)
  refreshKey: number;
  onDetect: () => void;
}) {
  const [items, setItems] = useState<Contradiction[] | null>(null);
  const [showDismissed, setShowDismissed] = useState(false);
  const [dismissing, setDismissing] = useState<{
    id: string;
    reason: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(
        await literatureApi.contradictions(
          projectId,
          showDismissed ? "all" : "open",
        ),
      );
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load the contradictions",
      );
    }
  }, [projectId, showDismissed]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function change(action: () => Promise<Contradiction>) {
    setError(null);
    try {
      await action();
      setDismissing(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work");
    }
  }

  return (
    <section aria-labelledby="conflicts-heading" className="space-y-4">
      <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
        <div>
          <h2 id="conflicts-heading" className="text-base font-semibold">
            Possible contradictions
          </h2>
          <p className="text-sm text-slate-500">
            Analysed papers that share a dataset or metric are compared claim by
            claim. Each finding shows both claims with the passages they come
            from and a possible explanation. Dismiss the ones that aren't real
            conflicts.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {canEdit && (
            <Button onClick={onDetect} disabled={busy || profiled < 2}>
              Find contradictions
            </Button>
          )}
          {profiled < 2 && (
            <span className="text-xs text-slate-500">
              Analyse at least two selected papers first (Full text tab).
            </span>
          )}
          <label className="ml-auto flex items-center gap-1.5 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={showDismissed}
              onChange={(e) => setShowDismissed(e.target.checked)}
            />
            Show dismissed
          </label>
        </div>
        {error && <ErrorAlert>{error}</ErrorAlert>}
      </div>

      {!items ? (
        <Spinner label="Loading contradictions" />
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          No contradictions to show. Run the check after analysing papers that
          report on the same datasets.
        </p>
      ) : (
        items.map((c) => (
          <article
            key={c.id}
            aria-label={`Contradiction: ${c.a.title} vs ${c.b.title}`}
            className={`space-y-3 rounded-xl border p-4 ${c.status === "dismissed" ? "border-slate-200 bg-slate-50 opacity-70" : "border-amber-200 bg-amber-50/40"}`}
          >
            <div className="flex flex-wrap items-center gap-2">
              {c.topic && (
                <Badge className="bg-white text-slate-700">{c.topic}</Badge>
              )}
              {c.explanation_kind && (
                <Badge className="bg-amber-100 text-amber-800">
                  {EXPLANATION_LABEL[c.explanation_kind] ?? c.explanation_kind}
                </Badge>
              )}
              {c.status === "dismissed" && (
                <Badge className="bg-slate-200 text-slate-700">
                  Dismissed{c.dismiss_reason ? `: ${c.dismiss_reason}` : ""}
                </Badge>
              )}
            </div>
            <div className="flex flex-col gap-3 md:flex-row">
              <Side projectId={projectId} label="Claim A" side={c.a} />
              <span className="self-center text-xs font-semibold text-slate-400">
                vs
              </span>
              <Side projectId={projectId} label="Claim B" side={c.b} />
            </div>
            {c.explanation && (
              <p className="text-sm text-slate-700">
                <span className="font-medium">Possible explanation:</span>{" "}
                {c.explanation}
              </p>
            )}
            {canEdit && (
              <div className="flex flex-wrap items-center gap-2">
                {c.status === "open" ? (
                  dismissing?.id === c.id ? (
                    <>
                      <input
                        className="h-8 min-w-60 flex-1 rounded-md border border-slate-300 px-2 text-sm"
                        placeholder="Why isn't this a real conflict? (optional)"
                        aria-label="Reason for dismissing"
                        maxLength={500}
                        value={dismissing.reason}
                        onChange={(e) =>
                          setDismissing({
                            ...dismissing,
                            reason: e.target.value,
                          })
                        }
                      />
                      <Button
                        className="h-8 px-3 text-xs"
                        onClick={() =>
                          void change(() =>
                            literatureApi.dismissContradiction(
                              projectId,
                              c.id,
                              dismissing.reason.trim() || null,
                            ),
                          )
                        }
                      >
                        Dismiss
                      </Button>
                      <Button
                        variant="secondary"
                        className="h-8 px-3 text-xs"
                        onClick={() => setDismissing(null)}
                      >
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="secondary"
                      className="h-8 px-3 text-xs"
                      onClick={() => setDismissing({ id: c.id, reason: "" })}
                    >
                      Dismiss…
                    </Button>
                  )
                ) : (
                  <Button
                    variant="secondary"
                    className="h-8 px-3 text-xs"
                    onClick={() =>
                      void change(() =>
                        literatureApi.restoreContradiction(projectId, c.id),
                      )
                    }
                  >
                    Restore
                  </Button>
                )}
              </div>
            )}
          </article>
        ))
      )}
    </section>
  );
}
