"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import {
  authorLine,
  literatureApi,
  paperLink,
  SOURCE_BADGE,
  type PaperFilters,
  type PaperPage,
  type PaperSort,
  type PaperSource,
} from "@/lib/literature";

const PAGE = 50;
const CONTROL =
  "h-9 rounded-md border border-slate-300 bg-white px-2 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

/** The project's paper library: search, filter by source, sort, remove. */
export function PaperTable({
  projectId,
  canEdit,
  refreshKey,
  onChanged,
}: {
  projectId: string;
  canEdit: boolean;
  refreshKey: number; // bump to reload (after a discovery run or a manual add)
  onChanged: () => void;
}) {
  const [filters, setFilters] = useState<PaperFilters>({ sort: "added", q: "", source: "" });
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<PaperPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

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

  return (
    <section aria-labelledby="library-heading" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="library-heading" className="text-base font-semibold">
          Paper library {page && <span className="font-normal text-slate-500">({page.total})</span>}
        </h2>
        <div className="flex flex-wrap gap-2">
          <input
            className={`${CONTROL} w-56`}
            placeholder="Search title, venue, DOI"
            aria-label="Search papers"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className={CONTROL}
            aria-label="Filter by source"
            value={filters.source}
            onChange={(e) => {
              setOffset(0);
              setFilters({ ...filters, source: e.target.value as PaperSource | "" });
            }}
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
            onChange={(e) => setFilters({ ...filters, sort: e.target.value as PaperSort })}
          >
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
          {filters.q || filters.source ? "No papers match these filters." : "No papers yet. Run a search or add one by DOI."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500 uppercase">
              <tr>
                <th className="px-3 py-2 font-semibold">Paper</th>
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
                  <tr key={p.id} className="align-top">
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
