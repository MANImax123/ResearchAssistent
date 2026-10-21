"use client";

import { useEffect, useState } from "react";

import { MarkdownView } from "@/components/editor/MarkdownView";
import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { SECTION_LABEL, sectionsApi, SOURCE_BADGE, type Diff, type SectionType, type Version, type VersionMeta } from "@/lib/sections";

/** Side drawer: every version of a section, with preview, diff against the current one, and restore. */
export function VersionHistory({
  projectId,
  type,
  currentId,
  canRestore,
  onClose,
  onRestored,
}: {
  projectId: string;
  type: SectionType;
  currentId: string | null;
  canRestore: boolean;
  onClose: () => void;
  onRestored: (version: Version) => void;
}) {
  const [versions, setVersions] = useState<VersionMeta[] | null>(null);
  const [selected, setSelected] = useState<Version | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [view, setView] = useState<"preview" | "diff">("preview");
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    sectionsApi
      .versions(projectId, type)
      .then(setVersions)
      .catch((err: Error) => setError(err.message));
  }, [projectId, type]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function select(meta: VersionMeta) {
    setError(null);
    setDiff(null);
    try {
      setSelected(await sectionsApi.version(projectId, type, meta.id));
      if (currentId && meta.id !== currentId) setDiff(await sectionsApi.diff(projectId, type, meta.id, currentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load version");
    }
  }

  async function restore() {
    if (!selected) return;
    setRestoring(true);
    try {
      onRestored(await sectionsApi.restore(projectId, type, selected.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not restore");
      setRestoring(false);
    }
  }

  const isCurrent = selected?.id === currentId;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-title"
        className="relative flex h-full w-full max-w-3xl flex-col bg-white shadow-xl"
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 id="history-title" className="text-base font-semibold">
            {SECTION_LABEL[type]}: version history
          </h2>
          <Button variant="secondary" className="h-8 px-3" onClick={onClose}>
            Close
          </Button>
        </header>
        {error && (
          <div className="px-5 pt-3">
            <ErrorAlert>{error}</ErrorAlert>
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          <ol aria-label="Versions" className="max-h-56 shrink-0 overflow-auto border-b border-slate-200 sm:max-h-none sm:w-64 sm:border-r sm:border-b-0">
            {!versions && !error && (
              <li className="p-4">
                <Spinner label="Loading versions" />
              </li>
            )}
            {versions?.map((v) => (
              <li key={v.id}>
                <button
                  type="button"
                  onClick={() => void select(v)}
                  aria-current={selected?.id === v.id ? "true" : undefined}
                  className={`w-full px-4 py-3 text-left text-sm hover:bg-slate-50 ${selected?.id === v.id ? "bg-indigo-50" : ""}`}
                >
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-slate-900">v{v.number}</span>
                    <Badge className={SOURCE_BADGE[v.source].className}>{SOURCE_BADGE[v.source].label}</Badge>
                    {v.id === currentId && <span className="text-xs text-indigo-600">current</span>}
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500">
                    {relativeTime(v.created_at)}
                    {v.change_note ? ` · ${v.change_note}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <section className="flex min-h-0 min-w-0 flex-1 flex-col">
            {!selected ? (
              <p className="p-5 text-sm text-slate-500">Select a version to preview it or compare it with the current text.</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-5 py-3">
                  <div role="tablist" aria-label="View" className="flex gap-1">
                    <ViewTab active={view === "preview"} onClick={() => setView("preview")}>
                      Preview
                    </ViewTab>
                    <ViewTab active={view === "diff"} onClick={() => setView("diff")} disabled={isCurrent}>
                      Changes vs current
                    </ViewTab>
                  </div>
                  <span className="flex-1" />
                  {canRestore && !isCurrent && (
                    <Button className="h-8 px-3" onClick={() => void restore()} loading={restoring}>
                      Restore v{selected.number}
                    </Button>
                  )}
                </div>
                <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
                  {view === "preview" || isCurrent ? (
                    <MarkdownView markdown={selected.content} />
                  ) : diff ? (
                    <DiffView diff={diff} />
                  ) : (
                    <Spinner label="Comparing" />
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}

function ViewTab({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-md px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${active ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"}`}
    >
      {children}
    </button>
  );
}

function DiffView({ diff }: { diff: Diff }) {
  const lines = diff.unified.split("\n").filter((l) => !l.startsWith("---") && !l.startsWith("+++"));
  if (lines.length === 0) return <p className="text-sm text-slate-500">No differences.</p>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-500">
        v{diff.from_version} → v{diff.to_version}: <span className="text-emerald-700">+{diff.added}</span>{" "}
        <span className="text-red-700">−{diff.removed}</span> lines
      </p>
      <pre aria-label="Differences" className="overflow-x-auto rounded-lg border border-slate-200 text-xs leading-5">
        {lines.map((line, i) => (
          <div
            key={i}
            className={`px-3 ${
              line.startsWith("+")
                ? "bg-emerald-50 text-emerald-900"
                : line.startsWith("-")
                  ? "bg-red-50 text-red-900"
                  : line.startsWith("@@")
                    ? "bg-slate-50 text-slate-400"
                    : "text-slate-700"
            }`}
          >
            {line || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}
