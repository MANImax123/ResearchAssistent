"use client";

import { useState } from "react";

import { Badge, Button } from "@/components/ui";
import type { DiscoverOptions, DiscoverySummary, SearchSource, SourceStatus } from "@/lib/literature";

const INPUT =
  "h-9 w-full rounded-md border border-slate-300 px-2 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

function yearOrNull(value: string): number | null {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : null;
}

/** Sources and filters for a discovery run, and what the last run found. */
export function DiscoverPanel({
  sources,
  queryCount,
  canRun,
  busy,
  summary,
  onRun,
}: {
  sources: SourceStatus[];
  queryCount: number;
  canRun: boolean;
  busy: boolean;
  summary: DiscoverySummary | null;
  onRun: (options: DiscoverOptions) => void;
}) {
  const [chosen, setChosen] = useState<SearchSource[]>(sources.map((s) => s.name));
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [venue, setVenue] = useState("");
  const [perSource, setPerSource] = useState(10);
  const [maxNew, setMaxNew] = useState(100);

  const from = yearOrNull(yearFrom);
  const to = yearOrNull(yearTo);
  const yearsInvalid = from !== null && to !== null && from > to;
  const toggle = (name: SearchSource) =>
    setChosen((prev) => (prev.includes(name) ? prev.filter((s) => s !== name) : [...prev, name]));

  return (
    <section aria-labelledby="discover-heading" className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
      <div>
        <h2 id="discover-heading" className="text-base font-semibold">
          Find papers
        </h2>
        <p className="text-sm text-slate-500">
          Runs every query on the chosen sources, removes duplicates (same DOI or arXiv id, or the same title), and
          adds new papers to your library. Ranking by relevance comes next.
        </p>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-slate-700">Sources</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {sources.map((s) => (
            <label key={s.name} className="flex items-start gap-2 rounded-lg border border-slate-200 p-2.5 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={chosen.includes(s.name)}
                onChange={() => toggle(s.name)}
                disabled={!canRun || busy}
              />
              <span>
                <span className="font-medium text-slate-900">{s.label}</span>
                <span className={`block text-xs ${s.configured ? "text-slate-500" : "text-amber-700"}`}>{s.note}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-3 sm:grid-cols-5">
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">From year</span>
          <input className={INPUT} inputMode="numeric" placeholder="any" value={yearFrom}
                 onChange={(e) => setYearFrom(e.target.value)} disabled={!canRun || busy} aria-label="From year" />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">To year</span>
          <input className={INPUT} inputMode="numeric" placeholder="any" value={yearTo}
                 onChange={(e) => setYearTo(e.target.value)} disabled={!canRun || busy} aria-label="To year" />
        </label>
        <label className="space-y-1 text-sm sm:col-span-1">
          <span className="font-medium text-slate-700">Venue contains</span>
          <input className={INPUT} placeholder="any" value={venue} maxLength={200}
                 onChange={(e) => setVenue(e.target.value)} disabled={!canRun || busy} aria-label="Venue contains" />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Per query & source</span>
          <input className={INPUT} type="number" min={3} max={50} value={perSource}
                 onChange={(e) => setPerSource(Number(e.target.value))} disabled={!canRun || busy} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Add at most</span>
          <input className={INPUT} type="number" min={5} max={200} value={maxNew}
                 onChange={(e) => setMaxNew(Number(e.target.value))} disabled={!canRun || busy} />
        </label>
      </div>
      {yearsInvalid && <p className="text-xs text-amber-700">"From year" is after "To year".</p>}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          onClick={() =>
            onRun({
              sources: chosen,
              year_from: from,
              year_to: to,
              venue: venue.trim() || null,
              per_source: perSource,
              max_new: maxNew,
            })
          }
          disabled={!canRun || busy || chosen.length === 0 || queryCount === 0 || yearsInvalid}
        >
          {busy ? "Searching…" : `Search ${queryCount} quer${queryCount === 1 ? "y" : "ies"}`}
        </Button>
        {queryCount === 0 && <span className="text-xs text-slate-500">Add or suggest queries first.</span>}
      </div>

      {summary && <DiscoveryReport summary={summary} />}
    </section>
  );
}

export function DiscoveryReport({ summary }: { summary: DiscoverySummary }) {
  return (
    <div role="status" className="space-y-2 rounded-lg bg-slate-50 p-3 text-sm">
      <p className="text-slate-800">
        <strong>{summary.added}</strong> new paper{summary.added === 1 ? "" : "s"} added
        {summary.enriched > 0 && `, ${summary.enriched} already in your library updated`} · {summary.raw} results before
        removing duplicates
        {summary.filtered_out > 0 && ` · ${summary.filtered_out} outside your filters`}
        {summary.over_cap > 0 && ` · ${summary.over_cap} more over the limit`}
      </p>
      <ul className="flex flex-wrap gap-2">
        {Object.entries(summary.sources).map(([name, s]) => (
          <li key={name}>
            <Badge
              className={s.errors.length ? "bg-amber-100 text-amber-800" : "bg-emerald-50 text-emerald-700"}
            >
              <span title={s.errors.join("\n")}>
                {s.label}: {s.found}
                {s.skipped ? " · stopped (unavailable)" : s.errors.length ? ` · ${s.errors.length} failed` : ""}
              </span>
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}
