"use client";

import { useState } from "react";

import { Button } from "@/components/ui";
import type { LiteratureWorkspace } from "@/lib/literature";

const INPUT =
  "h-9 w-20 rounded-md border border-slate-300 px-2 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

/** Rank the library by relevance to the research questions, then choose the papers the survey uses. */
export function RankPanel({
  ws,
  canEdit,
  busy,
  onRank,
  onSelectTop,
  onClear,
}: {
  ws: LiteratureWorkspace;
  canEdit: boolean;
  busy: boolean;
  onRank: (topN: number) => void;
  onSelectTop: (k: number, minScore: number | null) => void;
  onClear: () => void;
}) {
  const [topN, setTopN] = useState(40);
  const [k, setK] = useState(20);
  const [onlyRelevant, setOnlyRelevant] = useState(true);
  const ranked = ws.judged_count > 0;

  return (
    <section aria-labelledby="rank-heading" className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
      <div>
        <h2 id="rank-heading" className="text-base font-semibold">
          Rank & select
        </h2>
        <p className="text-sm text-slate-500">
          Every paper is compared with your research questions. The closest ones are then read by the AI, which
          scores them 0–10 and says why. You choose which papers the survey uses.
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {[
          ["Papers", ws.paper_count],
          ["Judged by AI", ws.judged_count],
          ["Selected", ws.selected_count],
          ["Must include", ws.must_include_count],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg bg-slate-50 px-3 py-2">
            <dt className="text-xs text-slate-500">{label}</dt>
            <dd className="text-lg font-semibold text-slate-900 tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {ranked && ws.unranked_count > 0 && (
        <p role="status" className="text-sm text-amber-800">
          {ws.unranked_count} paper{ws.unranked_count === 1 ? " was" : "s were"} added since the last ranking. Rank
          again to score {ws.unranked_count === 1 ? "it" : "them"}.
        </p>
      )}

      {canEdit && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Button onClick={() => onRank(topN)} disabled={busy || ws.paper_count === 0}>
              {ranked ? "Rank again" : "Rank by relevance"}
            </Button>
            <label className="flex items-center gap-2 text-slate-600">
              AI reads the top
              <input className={INPUT} type="number" min={1} max={200} value={topN} aria-label="Papers the AI judges"
                     onChange={(e) => setTopN(Number(e.target.value))} disabled={busy} />
              papers
            </label>
            <span className="text-xs text-slate-500">Papers it already judged are not re-read.</span>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-sm">
            <Button variant="secondary" onClick={() => onSelectTop(k, onlyRelevant ? 0.6 : null)} disabled={busy || !ranked}>
              Select the top {k}
            </Button>
            <input className={INPUT} type="number" min={1} max={200} value={k} aria-label="How many to select"
                   onChange={(e) => setK(Number(e.target.value))} disabled={busy} />
            <label className="flex items-center gap-1.5 text-slate-600">
              <input type="checkbox" checked={onlyRelevant} onChange={(e) => setOnlyRelevant(e.target.checked)} />
              only papers the AI scored 6/10 or more
            </label>
            <Button variant="secondary" className="ml-auto" onClick={onClear} disabled={busy || ws.selected_count === 0}>
              Clear selection
            </Button>
          </div>
          <p className="text-xs text-slate-500">
            "Select the top" replaces the selection but keeps papers you marked must-include. You can also tick papers
            one by one in the library below.
          </p>
        </div>
      )}
    </section>
  );
}
