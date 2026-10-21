"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import {
  authorLine,
  literatureApi,
  PARSE_STATUS,
  type LiteratureWorkspace,
  type Paper,
} from "@/lib/literature";

/** Selected papers' full text: fetch open-access PDFs, parse them, upload the ones that need it. */
export function FullTextPanel({
  projectId,
  ws,
  canEdit,
  busy,
  refreshKey,
  onParse,
  onChanged,
}: {
  projectId: string;
  ws: LiteratureWorkspace;
  canEdit: boolean;
  busy: boolean; // a literature job is running
  refreshKey: number;
  onParse: (options: { paperIds?: string[]; force?: boolean }) => void;
  onChanged: () => void;
}) {
  const [papers, setPapers] = useState<Paper[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const target = useRef<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPapers(
        (
          await literatureApi.papers(projectId, {
            selected: true,
            sort: "relevance",
            limit: 200,
          })
        ).items,
      );
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load the selected papers",
      );
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  // While a job runs, refresh the per-paper statuses every few seconds
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [busy, load]);

  async function upload(file: File) {
    const paperId = target.current;
    if (!paperId) return;
    setUploading(paperId);
    setError(null);
    try {
      await literatureApi.uploadPaperPdf(projectId, paperId, file);
      onParse({ paperIds: [paperId] });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(null);
      target.current = null;
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  const counts = ws.parse_counts;
  const todo = ws.selected_count - (counts.parsed ?? 0);
  // Only papers waiting for an upload left: a new run just retries the open-access sources
  const onlyUploads = todo > 0 && todo === (counts.upload_needed ?? 0);

  return (
    <section aria-labelledby="fulltext-heading" className="space-y-4">
      <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
        <div>
          <h2 id="fulltext-heading" className="text-base font-semibold">
            Full text of the selected papers
          </h2>
          <p className="text-sm text-slate-500">
            Open-access PDFs are fetched from arXiv and the open locations the
            sources reported (and Unpaywall when configured), then split into
            sections, captions and passages. Paywalled papers need you to upload
            the PDF.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(PARSE_STATUS) as (keyof typeof PARSE_STATUS)[])
            .filter((s) => counts[s])
            .map((s) => (
              <Badge key={s} className={PARSE_STATUS[s].className}>
                {PARSE_STATUS[s].label}: {counts[s]}
              </Badge>
            ))}
          {ws.selected_count === 0 && (
            <span className="text-sm text-slate-500">
              Select papers in the Discover tab first.
            </span>
          )}
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <Button
              variant={onlyUploads ? "secondary" : "primary"}
              onClick={() => onParse({})}
              disabled={busy || todo <= 0}
            >
              {busy
                ? "Working…"
                : onlyUploads
                  ? "Try open-access sources again"
                  : `Get full text for ${todo > 0 ? todo : 0} paper${todo === 1 ? "" : "s"}`}
            </Button>
            <Button
              variant="secondary"
              onClick={() => onParse({ force: true })}
              disabled={busy || (counts.parsed ?? 0) === 0}
            >
              Parse all again
            </Button>
          </div>
        )}
      </div>

      <input
        ref={fileInput}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        aria-label="PDF for the paper"
        onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])}
      />
      {error && <ErrorAlert>{error}</ErrorAlert>}
      {!papers ? (
        <Spinner label="Loading the selected papers" />
      ) : (
        papers.length > 0 && (
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {papers.map((p) => {
              const status = PARSE_STATUS[p.parse_status];
              const needsPdf =
                p.parse_status === "upload_needed" ||
                p.parse_status === "failed";
              return (
                <li
                  key={p.id}
                  className="flex flex-wrap items-start justify-between gap-3 px-4 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-slate-900">{p.title}</p>
                    <p className="text-xs text-slate-500">
                      {authorLine(p.authors)}
                      {p.year && ` · ${p.year}`}
                    </p>
                    {p.parse_error && (
                      <p
                        className={`mt-1 text-xs ${p.parse_status === "failed" ? "text-red-700" : "text-amber-800"}`}
                      >
                        {p.parse_error}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge className={status.className}>{status.label}</Badge>
                    {p.parse_status === "parsed" && (
                      <Link
                        href={`/projects/${projectId}/literature/papers/${p.id}`}
                        className="text-sm font-medium text-indigo-700 hover:underline"
                      >
                        Open
                      </Link>
                    )}
                    {canEdit && needsPdf && (
                      <Button
                        variant="secondary"
                        className="h-8 px-3 text-xs"
                        loading={uploading === p.id}
                        disabled={busy || uploading !== null}
                        onClick={() => {
                          target.current = p.id;
                          fileInput.current?.click();
                        }}
                      >
                        Upload PDF
                      </Button>
                    )}
                    {canEdit &&
                      p.parse_status === "failed" &&
                      p.pdf_file_id && (
                        <Button
                          variant="secondary"
                          className="h-8 px-3 text-xs"
                          disabled={busy}
                          onClick={() => onParse({ paperIds: [p.id] })}
                        >
                          Retry
                        </Button>
                      )}
                  </div>
                </li>
              );
            })}
          </ul>
        )
      )}
    </section>
  );
}
