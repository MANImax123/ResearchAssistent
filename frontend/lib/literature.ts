import { apiFetch, apiUpload } from "@/lib/api";
import type { Job } from "@/lib/jobs";
import type { StageStatus } from "@/lib/stages";

export type SearchSource = "semantic_scholar" | "openalex" | "arxiv" | "crossref";
export type PaperSource = SearchSource | "manual" | "upload";
export type PaperOrigin = "search" | "manual" | "upload";

export const SOURCE_BADGE: Record<PaperSource, { label: string; className: string }> = {
  semantic_scholar: { label: "Semantic Scholar", className: "bg-sky-100 text-sky-800" },
  openalex: { label: "OpenAlex", className: "bg-violet-100 text-violet-800" },
  arxiv: { label: "arXiv", className: "bg-rose-100 text-rose-800" },
  crossref: { label: "Crossref", className: "bg-amber-100 text-amber-800" },
  manual: { label: "Added by ID", className: "bg-slate-100 text-slate-700" },
  upload: { label: "Uploaded PDF", className: "bg-slate-100 text-slate-700" },
};

export type SearchQuery = {
  id: string;
  label: string;
  text: string;
  purpose: string | null;
  targets: string[];
  origin: "ai" | "user";
  last_run_at: string | null;
  last_found: number | null;
};

/** A query being edited (new ones have no id yet). */
export type QueryDraft = { id?: string; text: string; purpose?: string | null; targets?: string[] };

export type SourceStatus = { name: SearchSource; label: string; configured: boolean; note: string };

export type LiteratureWorkspace = {
  status: StageStatus;
  editable: boolean;
  blocked_reason: string | null;
  queries: SearchQuery[];
  paper_count: number;
  selected_count: number;
  must_include_count: number;
  judged_count: number; // papers the AI judged against the current research questions
  unranked_count: number; // papers added since the last ranking
  parse_counts: Partial<Record<ParseStatus, number>>; // selected papers by parse status
  indexed_chunks: number; // passages "Ask the literature" can search
  pending_chunks: number; // parsed passages not indexed yet
  sources: SourceStatus[];
  research_questions: { id: string; text: string }[];
  subtopics: string[];
};

export type Paper = {
  id: string;
  title: string;
  authors: { name: string }[];
  year: number | null;
  venue: string | null;
  abstract: string | null;
  doi: string | null;
  s2_id: string | null;
  arxiv_id: string | null;
  openalex_id: string | null;
  url: string | null;
  pdf_url: string | null;
  pdf_file_id: string | null;
  citation_count: number | null;
  origin: PaperOrigin;
  sources: PaperSource[];
  found_by: string[];
  selected: boolean;
  must_include: boolean;
  similarity: number | null; // best cosine similarity to a research question
  rq_similarity: Record<string, number>;
  relevance_score: number | null; // AI judgement 0–1 (only for papers it reviewed)
  relevance_reason: string | null;
  relevance_rqs: string[];
  parse_status: ParseStatus;
  parse_error: string | null;
  created_at: string;
  has_identifier: boolean;
};

export type PaperPage = { total: number; items: Paper[] };

export type ParseStatus = "not_started" | "upload_needed" | "pending" | "parsing" | "parsed" | "failed";

export const PARSE_STATUS: Record<ParseStatus, { label: string; className: string }> = {
  not_started: { label: "Not parsed", className: "bg-slate-100 text-slate-600" },
  pending: { label: "Queued", className: "bg-slate-100 text-slate-600" },
  parsing: { label: "Parsing…", className: "bg-blue-100 text-blue-700" },
  parsed: { label: "Parsed", className: "bg-emerald-100 text-emerald-700" },
  upload_needed: { label: "Upload needed", className: "bg-amber-100 text-amber-800" },
  failed: { label: "Failed", className: "bg-red-100 text-red-700" },
};

export const PDF_SOURCE_LABEL: Record<PaperDocument["pdf_source"], string> = {
  arxiv: "PDF from arXiv",
  open_access: "Open-access PDF",
  unpaywall: "Open-access PDF found via Unpaywall",
  upload: "PDF you uploaded",
};

export const SECTION_KIND_LABEL: Record<SectionKind, string> = {
  introduction: "Introduction",
  related_work: "Related work",
  methodology: "Methodology",
  dataset: "Data & setup",
  results: "Results",
  discussion: "Discussion",
  limitations: "Limitations",
  conclusion: "Conclusion",
  appendix: "Appendix",
  other: "Other",
};

export type SectionKind =
  | "introduction"
  | "related_work"
  | "methodology"
  | "dataset"
  | "results"
  | "discussion"
  | "limitations"
  | "conclusion"
  | "appendix"
  | "other";

export type DocumentSection = {
  heading: string;
  title: string;
  number: string | null;
  kind: SectionKind;
  page: number | null;
  text: string;
};

export type PaperDocument = {
  paper: Paper;
  parser: "grobid" | "pymupdf";
  pdf_source: "upload" | "arxiv" | "open_access" | "unpaywall";
  pdf_url: string | null;
  page_count: number | null;
  abstract: string | null;
  sections: DocumentSection[];
  captions: { kind: "figure" | "table"; label: string; text: string; page: number | null }[];
  limitations: { section: string; text: string; page: number | null }[];
  reference_count: number;
  chunk_count: number;
};
export type PaperSort = "relevance" | "added" | "year" | "citations" | "title";
export type PaperFilters = {
  q?: string;
  source?: PaperSource | "";
  sort?: PaperSort;
  selected?: boolean;
  rq?: string;
  min_score?: number;
  limit?: number;
  offset?: number;
};

export type DiscoverOptions = {
  sources: SearchSource[];
  year_from?: number | null;
  year_to?: number | null;
  venue?: string | null;
  per_source: number;
  max_new: number;
  query_ids?: string[] | null;
};

/** What a finished discovery job reports (job.result). */
export type DiscoverySummary = {
  added: number;
  enriched: number;
  raw: number;
  filtered_out: number;
  over_cap: number;
  sources: Record<SearchSource, { label: string; found: number; errors: string[]; skipped: boolean }>;
};

export type AddResult = { paper: Paper; created: boolean };

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/literature`;

export const literatureApi = {
  get: (projectId: string) => apiFetch<LiteratureWorkspace>(base(projectId)),
  saveQueries: (projectId: string, queries: QueryDraft[]) =>
    apiFetch<SearchQuery[]>(`${base(projectId)}/queries`, { method: "PUT", json: { queries } }),
  generateQueries: (projectId: string) => apiFetch<Job>(`${base(projectId)}/queries/generate`, { method: "POST" }),
  discover: (projectId: string, options: DiscoverOptions) =>
    apiFetch<Job>(`${base(projectId)}/discover`, { method: "POST", json: options }),
  papers: (projectId: string, filters: PaperFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "" && value !== null) params.set(key, String(value));
    }
    const qs = params.toString();
    return apiFetch<PaperPage>(`${base(projectId)}/papers${qs ? `?${qs}` : ""}`);
  },
  removePaper: (projectId: string, paperId: string) =>
    apiFetch<void>(`${base(projectId)}/papers/${encodeURIComponent(paperId)}`, { method: "DELETE" }),
  rank: (projectId: string, topN: number) =>
    apiFetch<Job>(`${base(projectId)}/rank`, { method: "POST", json: { top_n: topN } }),
  setFlags: (projectId: string, paperId: string, flags: { selected?: boolean; must_include?: boolean }) =>
    apiFetch<Paper>(`${base(projectId)}/papers/${encodeURIComponent(paperId)}`, { method: "PATCH", json: flags }),
  selectTop: (projectId: string, k: number, minScore?: number | null) =>
    apiFetch<{ selected: number }>(`${base(projectId)}/selection`, {
      method: "POST",
      json: { mode: "top", k, min_score: minScore ?? null },
    }),
  clearSelection: (projectId: string) =>
    apiFetch<{ selected: number }>(`${base(projectId)}/selection`, { method: "POST", json: { mode: "none" } }),
  parse: (projectId: string, options: { paperIds?: string[]; force?: boolean } = {}) =>
    apiFetch<Job>(`${base(projectId)}/parse`, {
      method: "POST",
      json: { paper_ids: options.paperIds ?? null, force: options.force ?? false },
    }),
  uploadPaperPdf: (projectId: string, paperId: string, file: File, onProgress?: (fraction: number) => void) => {
    const data = new FormData();
    data.append("file", file);
    return apiUpload<Paper>(`${base(projectId)}/papers/${encodeURIComponent(paperId)}/pdf`, data, onProgress);
  },
  document: (projectId: string, paperId: string) =>
    apiFetch<PaperDocument>(`${base(projectId)}/papers/${encodeURIComponent(paperId)}/document`),
  indexPassages: (projectId: string) => apiFetch<Job>(`${base(projectId)}/index`, { method: "POST" }),
  ask: (projectId: string, question: string, sectionKinds: AskSectionKind[] | null) =>
    apiFetch<Answer>(`${base(projectId)}/ask`, {
      method: "POST",
      json: { question, section_kinds: sectionKinds },
    }),
  addByIdentifier: (projectId: string, identifier: string) =>
    apiFetch<AddResult>(`${base(projectId)}/papers/lookup`, { method: "POST", json: { identifier } }),
  uploadPdf: (
    projectId: string,
    file: File,
    fields: { title?: string; identifier?: string },
    onProgress?: (fraction: number) => void,
  ) => {
    const data = new FormData();
    data.append("file", file);
    if (fields.title?.trim()) data.append("title", fields.title.trim());
    if (fields.identifier?.trim()) data.append("identifier", fields.identifier.trim());
    return apiUpload<AddResult>(`${base(projectId)}/papers/upload`, data, onProgress);
  },
};

/** "A. Smith, B. Jones, C. Lee et al." */
export function authorLine(authors: { name: string }[], max = 3): string {
  if (authors.length === 0) return "Unknown authors";
  const names = authors.slice(0, max).map((a) => a.name);
  return authors.length > max ? `${names.join(", ")} et al.` : names.join(", ");
}

/** Where to open a paper: DOI, arXiv, or the source's own link. */
export function paperLink(paper: Paper): string | null {
  if (paper.doi) return `https://doi.org/${paper.doi}`;
  if (paper.arxiv_id) return `https://arxiv.org/abs/${paper.arxiv_id}`;
  return paper.url;
}

/** "8/10" for AI-judged papers; colours from off-topic (red) to directly relevant (green). */
export function scoreTone(score: number): string {
  if (score >= 0.8) return "bg-emerald-100 text-emerald-800";
  if (score >= 0.6) return "bg-lime-100 text-lime-800";
  if (score >= 0.3) return "bg-amber-100 text-amber-800";
  return "bg-red-100 text-red-700";
}

export type AskSectionKind = SectionKind | "abstract";

/** A passage the answer cites ([S3] → marker "S3"). */
export type Source = {
  marker: string;
  chunk_id: string;
  paper_id: string;
  paper_title: string;
  year: number | null;
  section: string | null;
  kind: string | null;
  page: number | null;
  text: string;
  similarity: number;
  pdf_file_id: string | null; // the stored PDF, to open it at `page`
};

export type Answer = {
  answer: string;
  insufficient: boolean;
  citations: Source[];
  retrieved: number;
  removed_markers: string[];
};

/** Anchor id of a section on the paper page (citations link straight to it). */
export function sectionAnchor(heading: string): string {
  return `sec-${heading.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
}

/** Split an answer into text and [S#] citation markers. */
export function splitCitations(text: string): ({ text: string } | { marker: string })[] {
  const parts: ({ text: string } | { marker: string })[] = [];
  let last = 0;
  for (const match of text.matchAll(/\[(S\d+)\]/g)) {
    if (match.index > last) parts.push({ text: text.slice(last, match.index) });
    parts.push({ marker: match[1] });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}
