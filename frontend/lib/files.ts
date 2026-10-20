import type { FileKind } from "@/lib/api";

/** Mirrors the backend allow-list (app/services/files.py) so errors show before uploading. */
export const FILE_KINDS: { value: FileKind; label: string; extensions: string[] }[] = [
  { value: "paper_pdf", label: "Paper (PDF)", extensions: [".pdf"] },
  { value: "dataset", label: "Dataset", extensions: [".csv", ".tsv", ".json", ".xlsx", ".txt"] },
  { value: "diagram", label: "Diagram", extensions: [".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf"] },
  {
    value: "code",
    label: "Code",
    extensions: [".py", ".ipynb", ".r", ".m", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".txt", ".zip"],
  },
  { value: "notes", label: "Notes", extensions: [".txt", ".md", ".pdf", ".docx", ".tex", ".bib"] },
  {
    value: "result",
    label: "Results",
    extensions: [".csv", ".tsv", ".json", ".xlsx", ".txt", ".log", ".png", ".jpg", ".jpeg", ".pdf"],
  },
];

export const KIND_LABEL = Object.fromEntries(FILE_KINDS.map((k) => [k.value, k.label])) as Record<FileKind, string>;

export const MAX_UPLOAD_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB ?? 50);

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/** Returns an error message, or null if the file may be uploaded as `kind`. */
export function checkFile(file: { name: string; size: number }, kind: FileKind): string | null {
  const allowed = FILE_KINDS.find((k) => k.value === kind)!.extensions;
  const ext = extensionOf(file.name);
  if (!allowed.includes(ext)) {
    return `${ext || "Files without an extension"} not allowed for ${KIND_LABEL[kind]} (allowed: ${allowed.join(", ")})`;
  }
  if (file.size === 0) return "File is empty";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `File is larger than ${MAX_UPLOAD_MB} MB`;
  return null;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
