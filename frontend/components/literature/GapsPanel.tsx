"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import {
  CONFIDENCE_META,
  gapsApi,
  SIGNAL_LABEL,
  type Gap,
  type GapChanges,
  type GapConfidence,
  type GapSignal,
} from "@/lib/gaps";
import { sectionAnchor } from "@/lib/literature";

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none";

function Signal({
  projectId,
  signal,
}: {
  projectId: string;
  signal: GapSignal;
}) {
  const paperId = signal.papers.length === 1 ? signal.papers[0].id : null;
  return (
    <li className="space-y-1.5 rounded-lg border border-slate-200 bg-white p-3">
      <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
        {SIGNAL_LABEL[signal.kind]}
      </p>
      <p className="text-sm text-slate-800">{signal.text}</p>
      {signal.evidence.map((e) => {
        const target = paperId ?? signal.papers[0]?.id;
        return (
          <blockquote
            key={e.chunk_id}
            className="border-l-2 border-indigo-200 pl-2 text-xs text-slate-600"
          >
            <span className="font-medium text-slate-700">
              {e.section}
              {e.page && ` · p. ${e.page}`}
            </span>{" "}
            {e.text.length > 320 ? `${e.text.slice(0, 320)}…` : e.text}
            {paperId && target && (
              <>
                {" "}
                <Link
                  href={`/projects/${projectId}/literature/papers/${target}${e.section && e.section !== "Abstract" ? `#${sectionAnchor(e.section)}` : ""}`}
                  className="font-medium text-indigo-700 hover:underline"
                >
                  Open
                </Link>
              </>
            )}
          </blockquote>
        );
      })}
      {!paperId && signal.papers.length > 0 && (
        <p className="text-xs text-slate-500">
          Papers:{" "}
          {signal.papers.map((p, i) => (
            <span key={p.id}>
              {i > 0 && "; "}
              <Link
                href={`/projects/${projectId}/literature/papers/${p.id}`}
                className="text-indigo-700 hover:underline"
              >
                {p.title}
              </Link>
            </span>
          ))}
        </p>
      )}
    </li>
  );
}

function Editor({
  gap,
  objectives,
  onSave,
  onCancel,
}: {
  gap: Gap;
  objectives: { id: string; text: string }[];
  onSave: (changes: GapChanges) => void;
  onCancel: () => void;
}) {
  const [statement, setStatement] = useState(gap.statement);
  const [rationale, setRationale] = useState(gap.rationale ?? "");
  const [confidence, setConfidence] = useState<GapConfidence>(gap.confidence);
  const [linked, setLinked] = useState(gap.objectives.map((o) => o.id));
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          statement: statement.trim(),
          rationale: rationale.trim(),
          confidence,
          objective_ids: linked,
        });
      }}
    >
      <label className="block text-sm">
        <span className="font-medium text-slate-700">Potential gap</span>
        <textarea
          className={`${INPUT} mt-1`}
          rows={2}
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
          minLength={10}
          maxLength={500}
          required
        />
      </label>
      <label className="block text-sm">
        <span className="font-medium text-slate-700">Rationale</span>
        <textarea
          className={`${INPUT} mt-1`}
          rows={3}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          maxLength={1500}
        />
      </label>
      <label className="block text-sm">
        <span className="font-medium text-slate-700">Confidence</span>
        <select
          className={`${INPUT} mt-1 w-auto`}
          value={confidence}
          onChange={(e) => setConfidence(e.target.value as GapConfidence)}
        >
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </label>
      {objectives.length > 0 && (
        <fieldset className="space-y-1 text-sm">
          <legend className="font-medium text-slate-700">
            Objectives it could serve
          </legend>
          {objectives.map((o) => (
            <label key={o.id} className="flex items-start gap-2 text-slate-700">
              <input
                type="checkbox"
                className="mt-1"
                checked={linked.includes(o.id)}
                onChange={() =>
                  setLinked((cur) =>
                    cur.includes(o.id)
                      ? cur.filter((x) => x !== o.id)
                      : [...cur, o.id],
                  )
                }
              />
              <span>
                <span className="font-medium">{o.id}</span> {o.text}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={statement.trim().length < 10}>
          Save
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function GapCard({
  projectId,
  gap,
  objectives,
  canEdit,
  onChange,
}: {
  projectId: string;
  gap: Gap;
  objectives: { id: string; text: string }[];
  canEdit: boolean;
  onChange: (changes: GapChanges) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const confidence = CONFIDENCE_META[gap.confidence];
  const tone =
    gap.status === "accepted"
      ? "border-emerald-200 bg-emerald-50/40"
      : gap.status === "rejected"
        ? "border-slate-200 bg-slate-50 opacity-70"
        : "border-slate-200 bg-white";
  return (
    <article
      aria-label={`Potential gap: ${gap.statement}`}
      className={`space-y-3 rounded-xl border p-4 ${tone}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge className="bg-indigo-50 text-indigo-700">Potential gap</Badge>
        <Badge className={confidence.className}>{confidence.label}</Badge>
        {gap.status === "accepted" && (
          <Badge className="bg-emerald-100 text-emerald-800">Accepted</Badge>
        )}
        {gap.status === "rejected" && (
          <Badge className="bg-slate-200 text-slate-700">Rejected</Badge>
        )}
        {gap.edited && (
          <Badge className="bg-slate-100 text-slate-600">Edited</Badge>
        )}
      </div>
      {editing ? (
        <Editor
          gap={gap}
          objectives={objectives}
          onCancel={() => setEditing(false)}
          onSave={(changes) =>
            void onChange(changes).then((ok) => ok && setEditing(false))
          }
        />
      ) : (
        <>
          <p className="font-medium text-slate-900">{gap.statement}</p>
          {gap.rationale && (
            <p className="text-sm text-slate-700">{gap.rationale}</p>
          )}
          {gap.objectives.length > 0 && (
            <ul
              aria-label="Objectives it could serve"
              className="flex flex-wrap gap-2"
            >
              {gap.objectives.map((o) => (
                <li
                  key={o.id}
                  className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-700"
                  title={o.text}
                >
                  {o.id} ·{" "}
                  {o.text.length > 60 ? `${o.text.slice(0, 60)}…` : o.text}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer font-medium text-slate-700">
          Evidence ({gap.signals.length})
        </summary>
        <ul className="mt-2 space-y-2">
          {gap.signals.map((s, i) => (
            <Signal key={i} projectId={projectId} signal={s} />
          ))}
        </ul>
      </details>
      {canEdit && !editing && (
        <div className="flex flex-wrap gap-2">
          {gap.status === "candidate" && (
            <>
              <Button
                className="h-8"
                onClick={() => void onChange({ status: "accepted" })}
              >
                Accept
              </Button>
              <Button
                className="h-8"
                variant="secondary"
                onClick={() => setEditing(true)}
              >
                Edit
              </Button>
              <Button
                className="h-8"
                variant="secondary"
                onClick={() => void onChange({ status: "rejected" })}
              >
                Reject
              </Button>
            </>
          )}
          {gap.status === "accepted" && (
            <>
              <Button
                className="h-8"
                variant="secondary"
                onClick={() => setEditing(true)}
              >
                Edit
              </Button>
              <Button
                className="h-8"
                variant="secondary"
                onClick={() => void onChange({ status: "candidate" })}
              >
                Undo accept
              </Button>
            </>
          )}
          {gap.status === "rejected" && (
            <Button
              className="h-8"
              variant="secondary"
              onClick={() => void onChange({ status: "candidate" })}
            >
              Restore
            </Button>
          )}
        </div>
      )}
    </article>
  );
}

/** L10: potential gaps from the selected papers. Suggestions to check, never established facts. */
export function GapsPanel({
  projectId,
  canEdit,
  busy,
  profiled,
  objectives,
  refreshKey,
  onDetect,
}: {
  projectId: string;
  canEdit: boolean;
  busy: boolean;
  profiled: number; // selected papers with a profile
  objectives: { id: string; text: string }[];
  refreshKey: number;
  onDetect: () => void;
}) {
  const [gaps, setGaps] = useState<Gap[] | null>(null);
  const [showRejected, setShowRejected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setGaps(await gapsApi.list(projectId));
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load the potential gaps",
      );
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function change(gap: Gap, changes: GapChanges): Promise<boolean> {
    setError(null);
    try {
      const updated = await gapsApi.update(projectId, gap.id, changes);
      setGaps(
        (cur) => cur?.map((g) => (g.id === gap.id ? updated : g)) ?? null,
      );
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work");
      return false;
    }
  }

  const groups = [
    { status: "accepted", title: "Accepted" },
    { status: "candidate", title: "To review" },
    ...(showRejected ? [{ status: "rejected", title: "Rejected" }] : []),
  ] as const;
  const rejected = gaps?.filter((g) => g.status === "rejected").length ?? 0;

  return (
    <section aria-labelledby="gaps-heading" className="space-y-4">
      <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
        <div>
          <h2 id="gaps-heading" className="text-base font-semibold">
            Potential research gaps
          </h2>
          <p className="text-sm text-slate-500">
            Suggestions drawn from your selected papers&apos; limitations,
            conflicting results, method–dataset combinations none of them
            report, and their future-work statements. They are possibilities to
            check, not established facts. Accept the ones worth pursuing and
            link them to your objectives.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {canEdit && (
            <Button onClick={onDetect} disabled={busy || profiled < 1}>
              Find potential gaps
            </Button>
          )}
          {profiled < 1 && (
            <span className="text-xs text-slate-500">
              Analyse selected papers first (Full text tab).
            </span>
          )}
          {rejected > 0 && (
            <label className="ml-auto flex items-center gap-1.5 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={showRejected}
                onChange={(e) => setShowRejected(e.target.checked)}
              />
              Show rejected ({rejected})
            </label>
          )}
        </div>
        {error && <ErrorAlert>{error}</ErrorAlert>}
      </div>

      {!gaps ? (
        <Spinner label="Loading potential gaps" />
      ) : gaps.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          No potential gaps yet. Run the search after analysing your selected
          papers; finding contradictions and building the graph first gives it
          more to work with.
        </p>
      ) : (
        groups.map(({ status, title }) => {
          const items = gaps.filter((g) => g.status === status);
          if (!items.length) return null;
          return (
            <div key={status} className="space-y-3">
              <h3 className="text-sm font-semibold text-slate-700">
                {title} ({items.length})
              </h3>
              {items.map((g) => (
                <GapCard
                  key={g.id}
                  projectId={projectId}
                  gap={g}
                  objectives={objectives}
                  canEdit={canEdit}
                  onChange={(changes) => change(g, changes)}
                />
              ))}
            </div>
          );
        })
      )}
    </section>
  );
}
