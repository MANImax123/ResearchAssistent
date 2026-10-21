import { apiFetch } from "@/lib/api";
import type { StageName } from "@/lib/stages";

export type SectionType =
  | "title"
  | "abstract"
  | "introduction"
  | "literature_survey"
  | "methodology"
  | "results"
  | "discussion"
  | "conclusion"
  | "references";

export type VersionSource = "ai" | "user" | "ai_edited";

export type VersionMeta = {
  id: string;
  number: number;
  source: VersionSource;
  created_by: string | null;
  created_at: string;
  change_note: string | null;
  agent_run_id: string | null;
};

export type Version = VersionMeta & { content: string };

export type SectionSummary = {
  type: SectionType;
  stage: StageName | null;
  status: "empty" | "draft" | "approved" | "stale";
  exists: boolean;
  version_count: number;
  current_version: VersionMeta | null;
  word_count: number;
  editable: boolean;
  edit_blocked_reason: string | null;
};

export type SectionDetail = SectionSummary & { id: string | null; content: string };

export type Diff = { from_version: number; to_version: number; unified: string; added: number; removed: number };

export const SECTION_LABEL: Record<SectionType, string> = {
  title: "Title",
  abstract: "Abstract",
  introduction: "Introduction",
  literature_survey: "Literature Survey",
  methodology: "Methodology",
  results: "Results",
  discussion: "Discussion",
  conclusion: "Conclusion",
  references: "References",
};

/**
 * The section each stage's draft is reviewed in (mirrors backend STAGE_SECTIONS). The Idea stage
 * produces a research plan and the working abstract, both reviewed in the Idea workspace.
 */
export const STAGE_SECTION: Partial<Record<StageName, SectionType>> = {
  literature: "literature_survey",
  methodology: "methodology",
  results: "results",
  introduction: "introduction",
  conclusion: "conclusion",
  references: "references",
};

export const SOURCE_BADGE: Record<VersionSource, { label: string; className: string }> = {
  ai: { label: "AI draft", className: "bg-violet-100 text-violet-700" },
  ai_edited: { label: "Edited", className: "bg-sky-100 text-sky-700" },
  user: { label: "Written by you", className: "bg-emerald-100 text-emerald-700" },
};

const path = (projectId: string, type: SectionType) =>
  `/projects/${encodeURIComponent(projectId)}/sections/${type}`;

export const sectionsApi = {
  list: (projectId: string) => apiFetch<SectionSummary[]>(`/projects/${encodeURIComponent(projectId)}/sections`),
  get: (projectId: string, type: SectionType) => apiFetch<SectionDetail>(path(projectId, type)),
  versions: (projectId: string, type: SectionType) => apiFetch<VersionMeta[]>(`${path(projectId, type)}/versions`),
  version: (projectId: string, type: SectionType, id: string) =>
    apiFetch<Version>(`${path(projectId, type)}/versions/${encodeURIComponent(id)}`),
  save: (
    projectId: string,
    type: SectionType,
    body: { content: string; base_version_id: string | null; autosave: boolean; change_note?: string | null },
  ) => apiFetch<{ version: Version; coalesced: boolean }>(`${path(projectId, type)}/versions`, { method: "POST", json: body }),
  restore: (projectId: string, type: SectionType, id: string) =>
    apiFetch<Version>(`${path(projectId, type)}/versions/${encodeURIComponent(id)}/restore`, { method: "POST" }),
  diff: (projectId: string, type: SectionType, fromId: string, toId?: string) =>
    apiFetch<Diff>(
      `${path(projectId, type)}/diff?from_version=${encodeURIComponent(fromId)}${toId ? `&to_version=${encodeURIComponent(toId)}` : ""}`,
    ),
};
