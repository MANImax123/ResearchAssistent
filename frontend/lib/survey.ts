import { API_URL, apiFetch, tokenStore } from "@/lib/api";
import type { Job } from "@/lib/jobs";
import type { StageStatus } from "@/lib/stages";

export type RefinementKind = "modify_objective" | "modify_question" | "add_question" | "add_objective";

export type Refinement = {
  id: string;
  kind: RefinementKind;
  target: string | null;
  text: string;
  question_kind: "question" | "hypothesis" | null;
  linked_objective_ids: string[];
  question_text: string | null;
  before: string | null;
  rationale: string | null;
  gaps: { id: string; statement: string }[];
  status: "proposed" | "accepted" | "rejected";
  applied_ids: string[];
  decided_at: string | null;
  created_at: string;
};

export type SurveyCitation = { paper_id: string; title: string; year: number | null; authors: string };

export type Survey = {
  stage_status: StageStatus;
  version: number | null;
  content: string | null;
  source: string | null;
  updated_at: string | null;
  citations: SurveyCitation[];
  unresolved: string[];
  uncited_paragraphs: number;
  paragraphs: number;
};

export type DiffPart = { text: string; change: "same" | "added" | "removed" };

/** Word-level diff (LCS) for the refinement view: what the change removes and adds. */
export function wordDiff(before: string, after: string): DiffPart[] {
  const a = before.split(/(\s+)/).filter(Boolean);
  const b = after.split(/(\s+)/).filter(Boolean);
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const parts: DiffPart[] = [];
  const push = (text: string, change: DiffPart["change"]) => {
    const last = parts[parts.length - 1];
    if (last && last.change === change) last.text += text;
    else parts.push({ text, change });
  };
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push(a[i], "same");
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) push(a[i++], "removed");
    else push(b[j++], "added");
  }
  while (i < a.length) push(a[i++], "removed");
  while (j < b.length) push(b[j++], "added");
  return parts;
}

/** `[@paper-id]` keys → numbered markers in order of first citation, with the matching reference list. */
export function numberCitations(
  markdown: string,
  citations: SurveyCitation[],
): { text: string; references: (SurveyCitation & { n: number })[] } {
  const known = new Map(citations.map((c) => [c.paper_id, c]));
  const order: string[] = [];
  const text = markdown.replace(/\[@([A-Za-z0-9_:.\-/]+)\]/g, (_, key: string) => {
    if (!known.has(key)) return `[?]`;
    if (!order.includes(key)) order.push(key);
    return `[${order.indexOf(key) + 1}]`;
  });
  return { text, references: order.map((key, i) => ({ ...known.get(key)!, n: i + 1 })) };
}

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/literature`;

export const surveyApi = {
  suggestRefinements: (projectId: string) =>
    apiFetch<Job>(`${base(projectId)}/refinements/suggest`, { method: "POST" }),
  refinements: (projectId: string) => apiFetch<Refinement[]>(`${base(projectId)}/refinements`),
  decide: (projectId: string, id: string, accept: boolean) =>
    apiFetch<Refinement>(`${base(projectId)}/refinements/${encodeURIComponent(id)}/${accept ? "accept" : "reject"}`, {
      method: "POST",
    }),
  survey: (projectId: string) => apiFetch<Survey>(`${base(projectId)}/survey`),
  /** Downloads the literature analysis report (needs the auth header, so it can't be a plain link). */
  downloadReport: async (projectId: string) => {
    const token = tokenStore.get();
    const resp = await fetch(`${API_URL}${base(projectId)}/report`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!resp.ok) throw new Error(`Download failed (${resp.status})`);
    const name =
      /filename="([^"]+)"/.exec(resp.headers.get("content-disposition") ?? "")?.[1] ?? "literature-analysis-report.md";
    const url = URL.createObjectURL(await resp.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
  },
};
