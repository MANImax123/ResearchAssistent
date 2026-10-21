"use client";

import { useEffect, useState } from "react";

import { Badge, Button } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { QueryDraft, SearchQuery } from "@/lib/literature";

type Row = QueryDraft & { key: string; label?: string; origin?: SearchQuery["origin"]; lastRun?: string | null; found?: number | null };

let nextKey = 0;
const toRows = (queries: SearchQuery[]): Row[] =>
  queries.map((q) => ({
    key: q.id,
    id: q.id,
    text: q.text,
    purpose: q.purpose,
    targets: q.targets,
    label: q.label,
    origin: q.origin,
    lastRun: q.last_run_at,
    found: q.last_found,
  }));

/** The search queries: the planning agent proposes them; the researcher edits, adds and removes. */
export function QueryEditor({
  queries,
  targetText,
  canEdit,
  busy,
  onSave,
  onSuggest,
}: {
  queries: SearchQuery[];
  targetText: Record<string, string>; // "RQ1" / "S2" → its text, for tooltips
  canEdit: boolean;
  busy: boolean;
  onSave: (queries: QueryDraft[]) => Promise<void>;
  onSuggest: () => void;
}) {
  const [rows, setRows] = useState<Row[]>(() => toRows(queries));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setRows(toRows(queries));
    setDirty(false);
  }, [queries]);

  const update = (key: string, text: string) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, text } : r)));
    setDirty(true);
  };
  const remove = (key: string) => {
    setRows((prev) => prev.filter((r) => r.key !== key));
    setDirty(true);
  };
  const add = () => {
    setRows((prev) => [...prev, { key: `new-${nextKey++}`, text: "" }]);
    setDirty(true);
  };
  const valid = rows.every((r) => r.text.trim().length >= 3);

  async function save() {
    setSaving(true);
    try {
      await onSave(
        rows.map((r) => ({ id: r.id, text: r.text.trim(), purpose: r.purpose ?? null, targets: r.targets ?? [] })),
      );
      setDirty(false);
    } finally {
      setSaving(false);
    }
  }

  function suggest() {
    if (rows.length > 0 && !window.confirm("Replace the current queries with new suggestions from the planning agent?")) return;
    onSuggest();
  }

  return (
    <section aria-labelledby="queries-heading" className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="queries-heading" className="text-base font-semibold">
            Search queries
          </h2>
          <p className="text-sm text-slate-500">
            Keyword queries sent to every source. Each one serves research questions (RQ) or subtopics (S) of your plan.
          </p>
        </div>
        {canEdit && (
          <Button variant={rows.length ? "secondary" : "primary"} onClick={suggest} disabled={busy || saving}>
            {rows.length ? "Suggest new queries" : "Suggest queries"}
          </Button>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">
          No queries yet. {canEdit ? "Let the planning agent suggest some from your research plan, or add your own." : ""}
        </p>
      ) : (
        <ol className="space-y-2">
          {rows.map((row, i) => (
            <li key={row.key} className="rounded-lg border border-slate-200 p-3">
              <div className="flex items-center gap-2">
                <span className="w-8 shrink-0 text-xs font-semibold text-slate-500">{row.label ?? `Q${i + 1}`}</span>
                <input
                  aria-label={`Query ${i + 1}`}
                  className="h-9 min-w-0 flex-1 rounded-md border border-slate-300 px-2 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none disabled:bg-slate-50"
                  value={row.text}
                  maxLength={300}
                  disabled={!canEdit || busy}
                  onChange={(e) => update(row.key, e.target.value)}
                  placeholder="e.g. graph neural network binding affinity"
                />
                {canEdit && (
                  <Button
                    variant="secondary"
                    className="h-9 px-3"
                    aria-label={`Remove query ${i + 1}`}
                    onClick={() => remove(row.key)}
                    disabled={busy}
                  >
                    Remove
                  </Button>
                )}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-10 text-xs text-slate-500">
                {(row.targets ?? []).map((t) => (
                  <Badge key={t} className="bg-indigo-50 text-indigo-700">
                    <span title={targetText[t.toUpperCase()] ?? t}>{t}</span>
                  </Badge>
                ))}
                {row.purpose && <span>{row.purpose}</span>}
                {row.origin === "user" && <Badge className="bg-slate-100 text-slate-600">Yours</Badge>}
                {row.lastRun && (
                  <span className="ml-auto">
                    {row.found ?? 0} results · run {relativeTime(row.lastRun)}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}

      {canEdit && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" className="h-9" onClick={add} disabled={busy || rows.length >= 30}>
            Add query
          </Button>
          {dirty && (
            <Button className="h-9" onClick={() => void save()} loading={saving} disabled={busy || !valid}>
              Save queries
            </Button>
          )}
          {dirty && !valid && <span className="text-xs text-amber-700">Each query needs at least 3 characters.</span>}
        </div>
      )}
    </section>
  );
}
