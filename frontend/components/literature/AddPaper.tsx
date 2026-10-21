"use client";

import { useState, type FormEvent } from "react";

import { Button, ErrorAlert } from "@/components/ui";
import { literatureApi, type AddResult } from "@/lib/literature";

const INPUT =
  "h-9 w-full rounded-md border border-slate-300 px-2 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

/** Add a known paper by DOI / arXiv id, or upload its PDF. */
export function AddPaper({ projectId, onAdded }: { projectId: string; onAdded: () => void }) {
  const [identifier, setIdentifier] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [pdfId, setPdfId] = useState("");
  const [busy, setBusy] = useState<"lookup" | "upload" | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function report(result: AddResult) {
    setMessage(result.created ? `Added "${result.paper.title}".` : `"${result.paper.title}" is already in your library.`);
    onAdded();
  }

  async function lookup(e: FormEvent) {
    e.preventDefault();
    setBusy("lookup");
    setError(null);
    setMessage(null);
    try {
      report(await literatureApi.addByIdentifier(projectId, identifier.trim()));
      setIdentifier("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the paper");
    } finally {
      setBusy(null);
    }
  }

  async function upload(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy("upload");
    setProgress(0);
    setError(null);
    setMessage(null);
    try {
      report(await literatureApi.uploadPdf(projectId, file, { title, identifier: pdfId }, setProgress));
      setFile(null);
      setTitle("");
      setPdfId("");
      (e.target as HTMLFormElement).reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="add-heading" className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
      <h2 id="add-heading" className="text-base font-semibold">
        Add a paper you know
      </h2>

      <form onSubmit={(e) => void lookup(e)} className="flex flex-wrap items-end gap-2">
        <label className="min-w-64 flex-1 space-y-1 text-sm">
          <span className="font-medium text-slate-700">DOI or arXiv id</span>
          <input
            className={INPUT}
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="10.1093/bioinformatics/btaa921 or 2003.06505"
            aria-label="DOI or arXiv id"
          />
        </label>
        <Button type="submit" className="h-9" loading={busy === "lookup"} disabled={busy !== null || identifier.trim().length < 4}>
          Add
        </Button>
      </form>

      <form onSubmit={(e) => void upload(e)} className="space-y-2 border-t border-slate-100 pt-4">
        <p className="text-sm font-medium text-slate-700">Or upload a PDF</p>
        <div className="grid gap-2 sm:grid-cols-3">
          <input
            type="file"
            accept="application/pdf,.pdf"
            aria-label="Paper PDF"
            className="text-sm file:mr-2 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <input
            className={INPUT}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title (optional: read from the PDF)"
            aria-label="Paper title"
            maxLength={500}
          />
          <input
            className={INPUT}
            value={pdfId}
            onChange={(e) => setPdfId(e.target.value)}
            placeholder="DOI / arXiv id (optional)"
            aria-label="Uploaded paper DOI or arXiv id"
          />
        </div>
        <p className="text-xs text-slate-500">
          With a DOI or arXiv id the metadata is looked up, and the PDF is attached if the paper is already in your
          library. Without one, the paper can't be cited until you add an identifier.
        </p>
        <Button type="submit" variant="secondary" className="h-9" loading={busy === "upload"} disabled={busy !== null || !file}>
          Upload PDF
        </Button>
        {busy === "upload" && <span className="ml-2 text-xs text-slate-500">{Math.round(progress * 100)}%</span>}
      </form>

      {message && (
        <p role="status" className="text-sm text-emerald-700">
          {message}
        </p>
      )}
      {error && <ErrorAlert>{error}</ErrorAlert>}
    </section>
  );
}
