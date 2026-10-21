"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import {
  authorLine,
  literatureApi,
  paperLink,
  scoreTone,
  SOURCE_BADGE,
  type Paper,
  type PaperFilters,
  type PaperPage,
  type PaperSort,
  type PaperSource,
} from "@/lib/literature";

const PAGE = 50;
const CONTROL =
  "h-9 rounded-md border border-slate-300 bg-white px-2 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

/** The project's paper library: search, filter, sort by relevance, select, mark must-include, remove. */
export function PaperTable({
  projectId,
  canEdit,
  refreshKey,
  rqIds,
  ranked,
  onChanged,
}: {
  projectId: string;
  canEdit: boolean;
  refreshKey: number; // bump to reload (after a discovery/ranking run or a manual add)
  rqIds: string[];
  ranked: boolean; // the library has AI judgements: sort by relevance by default
  onChanged: () => void; // selection or contents changed (refresh the counts)
}) {
  const [filters, setFilters] = useState<PaperFilters>({ sort: ranked ? "relevance" : "added", q: "", source: "" });
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<PaperPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  // Once the library is first ranked, switch to relevance order
  useEffect(() => {
    if (ranked) setFilters((f) => (f.sort === "added" ? { ...f, sort: "relevance" } : f));
  }, [ranked]);

  const load = useCallback(async () => {
    try {
      setPage(await literatureApi.papers(projectId, { ...filters, limit: PAGE, offset }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load papers");
    }
  }, [projectId, filters, offset]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  // Search as you type, without a request per keystroke
  useEffect(() => {
    const timer = setTimeout(() => {
      setOffset(0);
      setFilters((f) => (f.q === query ? f : { ...f, q: query }));
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const filter = (change: Partial<PaperFilters>) => {
    setOffset(0);
    setFilters((f) => ({ ...f, ...change }));
  };

  async function flag(paper: Paper, flags: { selected?: boolean; must_include?: boolean }) {
    try {
      const updated = await literatureApi.setFlags(projectId, paper.id, flags);
      setPage((prev) => prev && { ...prev, items: prev.items.map((p) => (p.id === updated.id ? updated : p)) });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the selection");
    }
  }

  async function remove(id: string, title: string) {
    if (!window.confirm(`Remove "${title}" from the library?`)) return;
    try {
      await literatureApi.removePaper(projectId, id);
      onChanged();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the paper");
    }
  }

  const filtered = Boolean(filters.q || filters.source || filters.selected || filters.rq);

  return (
    <section aria-labelledby="library-heading" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="library-heading" className="text-base font-semibold">
          Paper library {page && <span className="font-normal text-slate-500">({page.total})</span>}
        </h2>
        <div className="flex flex-wrap gap-2">
          <input
            className={`${CONTROL} w-52`}
            placeholder="Search title, venue, DOI"
            aria-label="Search papers"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label className={`${CONTROL} flex items-center gap-1.5`}>
            <input
              type="checkbox"
              checked={Boolean(filters.selected)}
              onChange={(e) => filter({ selected: e.target.checked || undefined })}
            />
            Selected only
          </label>
          {rqIds.length > 0 && (
            <select
              className={CONTROL}
              aria-label="Filter by research question"
              value={filters.rq ?? ""}
              onChange={(e) => filter({ rq: e.target.value || undefined })}
            >
              <option value="">Any question</option>
              {rqIds.map((rq) => (
                <option key={rq} value={rq}>
                  Relevant to {rq}
                </option>
              ))}
            </select>
          )}
          <select
            className={CONTROL}
            aria-label="Filter by source"
            value={filters.source}
            onChange={(e) => filter({ source: e.target.value as PaperSource | "" })}
          >
            <option value="">All sources</option>
            {Object.entries(SOURCE_BADGE).map(([value, meta]) => (
              <option key={value} value={value}>
                {meta.label}
              </option>
            ))}
          </select>
          <select
            className={CONTROL}
            aria-label="Sort papers"
            value={filters.sort}
            onChange={(e) => filter({ sort: e.target.value as PaperSort })}
          >
            <option value="relevance">Most relevant</option>
            <option value="added">Newest added</option>
            <option value="citations">Most cited</option>
            <option value="year">Newest published</option>
            <option value="title">Title A–Z</option>
          </select>
        </div>
      </div>

      {error && <ErrorAlert>{error}</ErrorAlert>}
      {!page ? (
        <Spinner label="Loading papers" />
      ) : page.items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          {filtered ? "No papers match these filters." : "No papers yet. Run a search or add one by DOI."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500 uppercase">
              <tr>
                <th className="w-10 px-3 py-2 font-semibold">
                  <span className="sr-only">Selected</span>
                </th>
                <th className="px-3 py-2 font-semibold">Paper</th>
                <th className="px-3 py-2 font-semibold">Relevance</th>
                <th className="px-3 py-2 font-semibold">Year</th>
                <th className="px-3 py-2 font-semibold">Cited</th>
                <th className="px-3 py-2 font-semibold">Sources</th>
                {canEdit && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {page.items.map((p) => {
                const link = paperLink(p);
                return (
                  <tr key={p.id} className={`align-top ${p.selected ? "bg-indigo-50/40" : ""}`}>
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={p.selected}
                        disabled={!canEdit}
                        onChange={(e) => void flag(p, { selected: e.target.checked })}
                        aria-label={`Select ${p.title}`}
                      />
                    </td>
                    <td className="max-w-xl px-3 py-2.5">
                      <button
                        type="button"
                        className="text-left font-medium text-slate-900 hover:text-indigo-700"
                        onClick={() => setOpen(open === p.id ? null : p.id)}
                        aria-expanded={open === p.id}
                      >
                        {p.title}
                      </button>
                      <p className="text-xs text-slate-500">
                        {authorLine(p.authors)}
                        {p.venue && ` · ${p.venue}`}
                      </p>
                      {p.relevance_reason && (
                        <p className="mt-1 text-xs text-slate-700">
                          {p.relevance_rqs.map((rq) => (
                            <Badge key={rq} className="mr-1 bg-indigo-50 text-indigo-700">
                              {rq}
                            </Badge>
                          ))}
                          {p.relevance_reason}
                        </p>
                      )}
                      <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs">
                        {link && (
                          <a href={link} target="_blank" rel="noreferrer" className="text-indigo-700 hover:underline">
                            {p.doi ? `doi:${p.doi}` : p.arxiv_id ? `arXiv:${p.arxiv_id}` : "Open"}
                          </a>
                        )}
                        {(p.pdf_url || p.pdf_file_id) && <span className="text-emerald-700">PDF available</span>}
                        {!p.has_identifier && (
                          <span className="text-amber-700">No DOI or arXiv id yet: can't be cited</span>
                        )}
                        {p.found_by.length > 0 && <span className="text-slate-400">Found by {p.found_by.join(", ")}</span>}
                      </p>
                      {open === p.id && (
                        <p className="mt-2 text-xs leading-relaxed text-slate-600">{p.abstract ?? "No abstract available."}</p>
                      )}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {p.relevance_score !== null ? (
                        <Badge className={scoreTone(p.relevance_score)}>
                          <span title="AI relevance judgement">{Math.round(p.relevance_score * 10)}/10</span>
                        </Badge>
                      ) : p.similarity !== null ? (
                        <span className="text-xs text-slate-500" title="Similarity to your research questions (not read by the AI)">
                          match {Math.round(p.similarity * 100)}%
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">not ranked</span>
                      )}
                      {canEdit ? (
                        <button
                          type="button"
                          className={`mt-1 block text-xs ${p.must_include ? "font-medium text-indigo-700" : "text-slate-400 hover:text-slate-600"}`}
                          aria-pressed={p.must_include}
                          onClick={() => void flag(p, { must_include: !p.must_include })}
                        >
                          {p.must_include ? "★ Must include" : "☆ Must include"}
                        </button>
                      ) : (
                        p.must_include && <span className="mt-1 block text-xs font-medium text-indigo-700">★ Must include</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-slate-600 tabular-nums">{p.year ?? "—"}</td>
                    <td className="px-3 py-2.5 text-slate-600 tabular-nums">{p.citation_count ?? "—"}</td>
                    <td className="px-3 py-2.5">
                      <span className="flex flex-wrap gap-1">
                        {p.sources.map((s) => (
                          <Badge key={s} className={SOURCE_BADGE[s]?.className ?? "bg-slate-100 text-slate-700"}>
                            {SOURCE_BADGE[s]?.label ?? s}
                          </Badge>
                        ))}
                      </span>
                    </td>
                    {canEdit && (
                      <td className="px-3 py-2.5 text-right">
                        <Button
                          variant="danger-outline"
                          className="h-8 px-2.5 text-xs"
                          aria-label={`Remove ${p.title}`}
                          onClick={() => void remove(p.id, p.title)}
                        >
                          Remove
                        </Button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {page && page.total > PAGE && (
        <div className="flex items-center justify-end gap-2 text-sm text-slate-600">
          <span>
            {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total}
          </span>
          <Button variant="secondary" className="h-8" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
            Previous
          </Button>
          <Button
            variant="secondary"
            className="h-8"
            disabled={offset + PAGE >= page.total}
            onClick={() => setOffset(offset + PAGE)}
          >
            Next
          </Button>
        </div>
      )}
    </section>
  );
}
