"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";

import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import {
  literatureApi,
  sectionAnchor,
  type Comparison,
  type ComparisonCell,
  type ComparisonColumn,
} from "@/lib/literature";

type SortKey = "paper" | "year" | string;
const INPUT =
  "h-9 rounded-md border border-slate-300 px-2 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

function CellView({
  projectId,
  paperId,
  cell,
}: {
  projectId: string;
  paperId: string;
  cell: ComparisonCell;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <span>{cell.value}</span>
      <span className="ml-1 inline-flex gap-1 align-middle">
        {cell.source === "user" && (
          <Badge className="bg-slate-100 text-[10px] text-slate-600">
            edited
          </Badge>
        )}
        {cell.source === "abstract" && (
          <Badge className="bg-amber-50 text-[10px] text-amber-800">
            abstract
          </Badge>
        )}
        {cell.evidence.length > 0 && (
          <button
            type="button"
            className="rounded bg-indigo-100 px-1 text-[10px] font-semibold text-indigo-800 hover:bg-indigo-200"
            aria-expanded={open}
            aria-label={`Evidence for ${cell.value}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpen(!open);
            }}
          >
            {cell.evidence[0].page ? `p.${cell.evidence[0].page}` : "src"}
          </button>
        )}
      </span>
      {open && (
        <span
          role="note"
          className="mt-1 block rounded border border-indigo-200 bg-indigo-50/60 p-2 text-xs"
        >
          {cell.evidence.map((e) => (
            <span key={e.chunk_id} className="block">
              <span className="font-medium">
                {e.section}
                {e.page && ` · p. ${e.page}`}
              </span>{" "}
              {e.text.length > 280 ? `${e.text.slice(0, 280)}…` : e.text}{" "}
              <Link
                href={`/projects/${projectId}/literature/papers/${paperId}${e.section && e.section !== "Abstract" ? `#${sectionAnchor(e.section)}` : ""}`}
                className="font-medium text-indigo-700 hover:underline"
                onClick={(ev) => ev.stopPropagation()}
              >
                Open
              </Link>
            </span>
          ))}
        </span>
      )}
    </>
  );
}

/** L7: the comparison matrix of the selected papers — sortable, filterable, editable, exportable. */
export function ComparePanel({
  projectId,
  canEdit,
  busy,
  refreshKey,
  onBuild,
  onFill,
}: {
  projectId: string;
  canEdit: boolean;
  busy: boolean; // a literature job is running
  refreshKey: number;
  onBuild: (force: boolean) => void;
  onFill: (key: string) => void;
}) {
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: "paper",
    desc: false,
  });
  const [editing, setEditing] = useState<{
    paperId: string;
    key: string;
    value: string;
  } | null>(null);
  const [newColumn, setNewColumn] = useState("");
  const [caption, setCaption] = useState("Comparison of the selected studies.");

  const load = useCallback(async () => {
    try {
      setData(await literatureApi.comparison(projectId));
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load the comparison",
      );
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function act(
    action: () => Promise<Comparison | void>,
    message?: string,
  ) {
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      if (result) setData(result);
      if (message) setNotice(message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work");
    }
  }

  const rows = useMemo(() => {
    if (!data) return [];
    const q = filter.trim().toLowerCase();
    const matching = q
      ? data.rows.filter((r) =>
          [
            r.paper.title,
            String(r.paper.year ?? ""),
            ...Object.values(r.cells).map((c) => c.value),
          ]
            .join(" ")
            .toLowerCase()
            .includes(q),
        )
      : data.rows;
    const value = (r: (typeof matching)[number]) =>
      sort.key === "paper"
        ? r.paper.title
        : sort.key === "year"
          ? (r.paper.year ?? 0)
          : (r.cells[sort.key]?.value ?? "");
    return [...matching].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const order =
        typeof x === "number" && typeof y === "number"
          ? x - y
          : String(x).localeCompare(String(y));
      return sort.desc ? -order : order;
    });
  }, [data, filter, sort]);

  if (!data)
    return error ? (
      <ErrorAlert>{error}</ErrorAlert>
    ) : (
      <Spinner label="Loading the comparison" />
    );

  const header = (key: SortKey, label: string) => (
    <button
      type="button"
      className="inline-flex items-center gap-1 font-semibold uppercase hover:text-slate-800"
      onClick={() =>
        setSort({ key, desc: sort.key === key ? !sort.desc : false })
      }
      aria-label={`Sort by ${label}`}
    >
      {label}
      {sort.key === key && <span aria-hidden>{sort.desc ? "▼" : "▲"}</span>}
    </button>
  );

  async function saveCell() {
    if (!editing) return;
    const { paperId, key, value } = editing;
    setEditing(null);
    await act(() => literatureApi.setCell(projectId, paperId, key, value));
  }

  async function addColumn(e: FormEvent) {
    e.preventDefault();
    const label = newColumn.trim();
    if (!label) return;
    setNewColumn("");
    await act(() => literatureApi.addColumn(projectId, label));
  }

  const customColumn = (c: ComparisonColumn) =>
    canEdit && !c.builtin ? (
      <span className="ml-1 inline-flex gap-1 normal-case">
        <button
          type="button"
          className="text-indigo-700 hover:underline"
          disabled={busy}
          onClick={() => onFill(c.key)}
        >
          Fill with AI
        </button>
        <button
          type="button"
          aria-label={`Remove column ${c.label}`}
          className="text-slate-400 hover:text-red-600"
          onClick={() => {
            if (window.confirm(`Remove the column "${c.label}"?`))
              void act(() =>
                literatureApi.updateColumn(projectId, c.key, { remove: true }),
              );
          }}
        >
          ×
        </button>
      </span>
    ) : null;

  return (
    <section aria-labelledby="compare-heading" className="space-y-4">
      <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
        <div>
          <h2 id="compare-heading" className="text-base font-semibold">
            Comparison of the selected papers
          </h2>
          <p className="text-sm text-slate-500">
            Cells are condensed from each paper's evidence-backed profile (or
            its abstract when there's no full text). Click a cell to correct it;
            your values are kept when the table is rebuilt.
          </p>
        </div>
        {canEdit && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={() => onBuild(false)}
              disabled={busy || data.rows.length === 0 || data.uncompared === 0}
            >
              {data.uncompared > 0
                ? `Compare ${data.uncompared} paper${data.uncompared === 1 ? "" : "s"}`
                : "All compared"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => onBuild(true)}
              disabled={busy || data.rows.length === 0}
            >
              Rebuild all
            </Button>
            <form onSubmit={(e) => void addColumn(e)} className="flex gap-2">
              <input
                className={`${INPUT} w-44`}
                placeholder="New column, e.g. Hardware"
                aria-label="New column name"
                maxLength={60}
                value={newColumn}
                onChange={(e) => setNewColumn(e.target.value)}
              />
              <Button
                type="submit"
                variant="secondary"
                disabled={!newColumn.trim()}
              >
                Add column
              </Button>
            </form>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          <input
            className={`${INPUT} w-56`}
            placeholder="Filter rows"
            aria-label="Filter rows"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <Button
            variant="secondary"
            onClick={() =>
              void act(() => literatureApi.downloadComparisonCsv(projectId))
            }
            disabled={data.rows.length === 0}
          >
            Export CSV
          </Button>
          {canEdit && (
            <>
              <input
                className={`${INPUT} min-w-60 flex-1`}
                aria-label="Table caption"
                value={caption}
                maxLength={300}
                onChange={(e) => setCaption(e.target.value)}
              />
              <Button
                variant="secondary"
                disabled={data.rows.length === 0 || caption.trim().length < 3}
                onClick={() =>
                  void act(async () => {
                    const r = await literatureApi.insertComparison(
                      projectId,
                      caption.trim(),
                      null,
                    );
                    setNotice(
                      `Inserted as Table ${r.table_number} in the Literature Survey section (version ${r.section_version}).`,
                    );
                  })
                }
              >
                Insert into Literature Survey
              </Button>
            </>
          )}
        </div>
        {notice && (
          <p role="status" className="text-sm text-emerald-700">
            {notice}
          </p>
        )}
        {error && <ErrorAlert>{error}</ErrorAlert>}
      </div>

      {data.rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          Select papers in the Discover tab to compare them.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
              <tr>
                <th className="px-3 py-2">{header("paper", "Paper")}</th>
                <th className="px-3 py-2">{header("year", "Year")}</th>
                {data.columns.map((c) => (
                  <th key={c.key} className="px-3 py-2 align-top">
                    {header(c.key, c.label)}
                    {customColumn(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.paper.id} className="align-top">
                  <td className="max-w-64 px-3 py-2.5">
                    <Link
                      href={`/projects/${projectId}/literature/papers/${r.paper.id}`}
                      className="font-medium text-slate-900 hover:text-indigo-700"
                    >
                      {r.paper.title}
                    </Link>
                    {!r.paper.has_profile && (
                      <span className="block text-[11px] text-amber-700">
                        {r.paper.parse_status === "parsed"
                          ? "not analysed yet"
                          : "no full text: abstract only"}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-slate-600 tabular-nums">
                    {r.paper.year ?? "—"}
                  </td>
                  {data.columns.map((c) => {
                    const cell = r.cells[c.key];
                    const isEditing =
                      editing?.paperId === r.paper.id && editing.key === c.key;
                    return (
                      <td
                        key={c.key}
                        className={`max-w-56 px-3 py-2.5 text-slate-700 ${canEdit && !isEditing ? "cursor-text hover:bg-slate-50" : ""}`}
                        onClick={() =>
                          canEdit &&
                          !isEditing &&
                          setEditing({
                            paperId: r.paper.id,
                            key: c.key,
                            value: cell?.value ?? "",
                          })
                        }
                      >
                        {isEditing ? (
                          <textarea
                            autoFocus
                            aria-label={`${c.label} for ${r.paper.title}`}
                            className="w-full rounded border border-indigo-300 p-1 text-sm"
                            rows={3}
                            value={editing.value}
                            onChange={(e) =>
                              setEditing({ ...editing, value: e.target.value })
                            }
                            onBlur={() => void saveCell()}
                            onKeyDown={(e) => {
                              if (e.key === "Escape") setEditing(null);
                              if (e.key === "Enter" && !e.shiftKey) {
                                e.preventDefault();
                                void saveCell();
                              }
                            }}
                          />
                        ) : cell ? (
                          <CellView
                            projectId={projectId}
                            paperId={r.paper.id}
                            cell={cell}
                          />
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
