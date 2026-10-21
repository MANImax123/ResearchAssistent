"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import { filesApi } from "@/lib/api";
import {
  authorLine,
  literatureApi,
  paperLink,
  PDF_SOURCE_LABEL,
  SECTION_KIND_LABEL,
  type DocumentSection,
  type PaperDocument,
  type SectionKind,
} from "@/lib/literature";

/** What the survey needs from a paper, pulled out of its sections. */
const KEY_KINDS: SectionKind[] = ["methodology", "dataset", "results"];
const PREVIEW_CHARS = 700;

function SectionText({ section }: { section: DocumentSection }) {
  const [open, setOpen] = useState(false);
  const long = section.text.length > PREVIEW_CHARS;
  return (
    <div className="space-y-1">
      <h4 className="text-sm font-medium text-slate-900">
        {section.heading}
        {section.page && (
          <span className="ml-2 text-xs font-normal text-slate-400">
            p. {section.page}
          </span>
        )}
      </h4>
      <p className="text-sm leading-relaxed whitespace-pre-line text-slate-700">
        {open || !long
          ? section.text
          : `${section.text.slice(0, PREVIEW_CHARS)}…`}
      </p>
      {long && (
        <button
          type="button"
          className="text-xs font-medium text-indigo-700"
          onClick={() => setOpen(!open)}
        >
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}

export default function PaperDocumentPage() {
  const { id, paperId } = useParams<{ id: string; paperId: string }>();
  const [doc, setDoc] = useState<PaperDocument | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    literatureApi
      .document(id, paperId)
      .then(setDoc)
      .catch((err: unknown) =>
        setError(
          err instanceof Error ? err.message : "Could not load the paper",
        ),
      );
  }, [id, paperId]);

  async function openPdf() {
    if (!doc?.paper.pdf_file_id) return;
    try {
      const { url } = await filesApi.downloadUrl(id, doc.paper.pdf_file_id);
      window.open(url, "_blank", "noopener");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open the PDF");
    }
  }

  const back = (
    <Link
      href={`/projects/${id}/literature`}
      className="text-sm text-slate-500 hover:text-slate-700"
    >
      ← Literature Survey
    </Link>
  );
  if (error && !doc)
    return (
      <div className="space-y-4">
        {back}
        <ErrorAlert>{error}</ErrorAlert>
      </div>
    );
  if (!doc) return <Spinner label="Loading the paper" />;

  const { paper } = doc;
  const link = paperLink(paper);
  const byKind = (kind: SectionKind) =>
    doc.sections.filter((s) => s.kind === kind);
  const figures = doc.captions.filter((c) => c.kind === "figure");
  const tables = doc.captions.filter((c) => c.kind === "table");

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        {back}
        <h1 className="text-2xl font-semibold tracking-tight">{paper.title}</h1>
        <p className="text-sm text-slate-600">
          {authorLine(paper.authors, 6)}
          {paper.venue && ` · ${paper.venue}`}
          {paper.year && ` · ${paper.year}`}
        </p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Badge className="bg-emerald-100 text-emerald-700">Parsed</Badge>
          <span>{PDF_SOURCE_LABEL[doc.pdf_source]}</span>
          <span>· {doc.page_count ?? "?"} pages</span>
          <span>· {doc.sections.length} sections</span>
          <span>· {doc.chunk_count} passages for search</span>
          <span>· {doc.reference_count} references</span>
          <span>
            · read by{" "}
            {doc.parser === "grobid"
              ? "GROBID"
              : "the basic text extractor (GROBID failed)"}
          </span>
          {link && (
            <a
              href={link}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-indigo-700 hover:underline"
            >
              {paper.doi
                ? `doi:${paper.doi}`
                : paper.arxiv_id
                  ? `arXiv:${paper.arxiv_id}`
                  : "Source"}
            </a>
          )}
          {paper.pdf_file_id && (
            <Button
              variant="secondary"
              className="h-7 px-2 text-xs"
              onClick={() => void openPdf()}
            >
              Open PDF
            </Button>
          )}
        </div>
        {error && <ErrorAlert>{error}</ErrorAlert>}
      </div>

      <section aria-labelledby="key-heading" className="space-y-4">
        <h2 id="key-heading" className="text-base font-semibold">
          Key parts
        </h2>
        <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-5">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Abstract
          </h3>
          <p className="text-sm leading-relaxed text-slate-700">
            {doc.abstract ?? paper.abstract ?? "No abstract found."}
          </p>
        </div>
        {KEY_KINDS.map((kind) => {
          const sections = byKind(kind);
          return (
            <div
              key={kind}
              className="space-y-3 rounded-xl border border-slate-200 bg-white p-5"
            >
              <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {SECTION_KIND_LABEL[kind]}
              </h3>
              {sections.length ? (
                sections.map((s) => <SectionText key={s.heading} section={s} />)
              ) : (
                <p className="text-sm text-slate-500">
                  No section was recognised as{" "}
                  {SECTION_KIND_LABEL[kind].toLowerCase()}.
                </p>
              )}
            </div>
          );
        })}
        <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-5">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Limitations
          </h3>
          {doc.limitations.length ? (
            <ul className="space-y-2 text-sm text-slate-700">
              {doc.limitations.map((l, i) => (
                <li key={i}>
                  “{l.text}”
                  <span className="ml-1 text-xs text-slate-400">
                    ({l.section}
                    {l.page ? `, p. ${l.page}` : ""})
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">
              The authors don't state limitations in a limitations, discussion
              or conclusion section.
            </p>
          )}
        </div>
      </section>

      <section aria-labelledby="captions-heading" className="space-y-3">
        <h2 id="captions-heading" className="text-base font-semibold">
          Figures & tables
        </h2>
        {doc.captions.length === 0 ? (
          <p className="text-sm text-slate-500">
            No figure or table captions were found.
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {[...tables, ...figures].map((c) => (
              <div
                key={`${c.label}-${c.page}`}
                className="rounded-lg border border-slate-200 bg-white p-3 text-sm"
              >
                <p className="font-medium text-slate-900">
                  {c.label}
                  {c.page && (
                    <span className="ml-2 text-xs font-normal text-slate-400">
                      p. {c.page}
                    </span>
                  )}
                </p>
                <p className="text-slate-600">{c.text}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="all-heading" className="space-y-3">
        <h2 id="all-heading" className="text-base font-semibold">
          All sections
        </h2>
        <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
          {doc.sections.map((s) => (
            <details
              key={`${s.heading}-${s.page}`}
              className="group px-4 py-2.5"
            >
              <summary className="flex cursor-pointer items-center justify-between gap-2 text-sm">
                <span className="font-medium text-slate-900">{s.heading}</span>
                <span className="flex items-center gap-2 text-xs text-slate-500">
                  <Badge className="bg-slate-100 text-slate-600">
                    {SECTION_KIND_LABEL[s.kind]}
                  </Badge>
                  {s.page && `p. ${s.page}`}
                </span>
              </summary>
              <p className="mt-2 text-sm leading-relaxed whitespace-pre-line text-slate-700">
                {s.text || "(no text)"}
              </p>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}
