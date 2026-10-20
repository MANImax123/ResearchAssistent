"use client";

import { useCallback, useEffect, useId, useRef, useState, type DragEvent } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge, Button, ErrorAlert, Select, Spinner } from "@/components/ui";
import { filesApi, type FileKind, type ProjectFile } from "@/lib/api";
import { checkFile, FILE_KINDS, formatSize, KIND_LABEL, MAX_UPLOAD_MB } from "@/lib/files";
import { relativeTime } from "@/lib/format";

type Upload = { key: string; name: string; progress: number; error: string | null };

export function FilesPanel({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const [files, setFiles] = useState<ProjectFile[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [kind, setKind] = useState<FileKind>("paper_pdf");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [toDelete, setToDelete] = useState<ProjectFile | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  const load = useCallback(() => {
    setLoadError(null);
    filesApi
      .list(projectId)
      .then(setFiles)
      .catch((err: Error) => setLoadError(err.message));
  }, [projectId]);

  useEffect(load, [load]);

  const patchUpload = (key: string, patch: Partial<Upload>) =>
    setUploads((prev) => prev.map((u) => (u.key === key ? { ...u, ...patch } : u)));

  async function uploadAll(list: FileList | File[]) {
    const uploadKind = kind;
    await Promise.all(
      Array.from(list).map(async (file) => {
        const key = `${file.name}-${crypto.randomUUID()}`;
        const problem = checkFile(file, uploadKind);
        setUploads((prev) => [...prev, { key, name: file.name, progress: 0, error: problem }]);
        if (problem) return;
        try {
          const created = await filesApi.upload(projectId, file, uploadKind, (p) => patchUpload(key, { progress: p }));
          setFiles((prev) => [created, ...(prev ?? [])]);
          setUploads((prev) => prev.filter((u) => u.key !== key));
        } catch (err) {
          patchUpload(key, { error: err instanceof Error ? err.message : "Upload failed" });
        }
      }),
    );
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) void uploadAll(e.dataTransfer.files);
  }

  async function download(file: ProjectFile) {
    setActionError(null);
    try {
      const { url } = await filesApi.downloadUrl(projectId, file.id);
      // The URL responds with Content-Disposition: attachment, so the page stays put
      window.location.assign(url);
    } catch (err) {
      setActionError(`Could not download ${file.filename}: ${err instanceof Error ? err.message : "error"}`);
    }
  }

  const accept = FILE_KINDS.find((k) => k.value === kind)!.extensions.join(",");

  return (
    <section className="space-y-4" aria-labelledby={`${inputId}-heading`}>
      <h2 id={`${inputId}-heading`} className="text-base font-semibold">
        Files
      </h2>

      {canEdit && (
        <div className="grid gap-4 rounded-xl border border-slate-200 bg-white p-4 sm:grid-cols-[14rem_1fr] sm:p-5">
          <Select
            label="File type"
            id={`${inputId}-kind`}
            value={kind}
            onChange={(e) => setKind(e.target.value as FileKind)}
            hint={FILE_KINDS.find((k) => k.value === kind)!.extensions.join(" ")}
          >
            {FILE_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </Select>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            data-testid="dropzone"
            className={`flex flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm transition ${
              dragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300"
            }`}
          >
            <p className="text-slate-700">
              Drag files here, or{" "}
              <button
                type="button"
                className="font-medium text-indigo-600 hover:text-indigo-500"
                onClick={() => inputRef.current?.click()}
              >
                browse
              </button>
            </p>
            <p className="text-xs text-slate-500">
              Uploaded as <strong className="font-medium">{KIND_LABEL[kind]}</strong> · up to {MAX_UPLOAD_MB} MB each
            </p>
            <input
              ref={inputRef}
              id={`${inputId}-input`}
              type="file"
              multiple
              accept={accept}
              className="sr-only"
              aria-label="Choose files to upload"
              onChange={(e) => {
                if (e.target.files?.length) void uploadAll(e.target.files);
                e.target.value = ""; // allow picking the same file again
              }}
            />
          </div>
        </div>
      )}

      {uploads.length > 0 && (
        <ul className="space-y-2" aria-label="Uploads">
          {uploads.map((u) => (
            <li key={u.key} className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-medium text-slate-800">{u.name}</span>
                {u.error ? (
                  <button
                    type="button"
                    className="text-xs text-slate-500 hover:text-slate-700"
                    onClick={() => setUploads((prev) => prev.filter((x) => x.key !== u.key))}
                  >
                    Dismiss
                  </button>
                ) : (
                  <span className="text-xs text-slate-500">{Math.round(u.progress * 100)}%</span>
                )}
              </div>
              {u.error ? (
                <p role="alert" className="mt-1 text-xs text-red-600">
                  {u.error}
                </p>
              ) : (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-indigo-600 transition-[width]"
                    style={{ width: `${Math.max(4, u.progress * 100)}%` }}
                    role="progressbar"
                    aria-valuenow={Math.round(u.progress * 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`Uploading ${u.name}`}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {actionError && <ErrorAlert>{actionError}</ErrorAlert>}
      {loadError && (
        <ErrorAlert
          action={
            <Button variant="secondary" className="h-8" onClick={load}>
              Retry
            </Button>
          }
        >
          Could not load files: {loadError}
        </ErrorAlert>
      )}
      {files === null && !loadError && <Spinner label="Loading files" />}
      {files?.length === 0 && <p className="text-sm text-slate-500">No files uploaded yet.</p>}

      {files && files.length > 0 && (
        <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white" aria-label="Project files">
          {files.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-5">
              <div className="w-full min-w-0 sm:w-auto sm:flex-1">
                <p className="truncate text-sm font-medium text-slate-900" title={f.filename}>
                  {f.filename}
                </p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
                  <Badge className="bg-slate-100 text-slate-600">{KIND_LABEL[f.kind]}</Badge>
                  <span>{formatSize(f.size)}</span>
                  <span aria-hidden>·</span>
                  <time dateTime={f.created_at}>{relativeTime(f.created_at)}</time>
                </p>
              </div>
              <div className="flex gap-2">
                <Button variant="secondary" className="h-8 px-3" onClick={() => download(f)}>
                  Download
                </Button>
                {canEdit && (
                  <Button
                    variant="danger-outline"
                    className="h-8 px-3"
                    onClick={() => setToDelete(f)}
                    aria-label={`Delete ${f.filename}`}
                  >
                    Delete
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {toDelete && (
        <ConfirmDialog
          title="Delete this file?"
          confirmLabel="Delete file"
          onClose={() => setToDelete(null)}
          onConfirm={async () => {
            await filesApi.remove(projectId, toDelete.id);
            setFiles((prev) => prev?.filter((f) => f.id !== toDelete.id) ?? null);
            setToDelete(null);
          }}
        >
          <p>
            <strong className="font-medium text-slate-900">{toDelete.filename}</strong> will be permanently deleted.
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}
