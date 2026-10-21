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
  parse_status: string;
  created_at: string;
  has_identifier: boolean;
};

export type PaperPage = { total: number; items: Paper[] };
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
