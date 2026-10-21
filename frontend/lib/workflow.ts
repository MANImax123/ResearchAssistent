import { apiFetch } from "@/lib/api";
import type { Job } from "@/lib/jobs";
import type { StageName, StageStatus } from "@/lib/stages";

export type StageState = {
  name: StageName;
  status: StageStatus;
  approved_at: string | null;
  prerequisites: StageName[];
  blocked_by: StageName[];
  can_run: boolean;
  can_regenerate: boolean;
  can_go_back: boolean;
};

export type ProjectState = {
  project_id: string;
  version: number;
  updated_at: string | null;
  state: Record<string, unknown> & { stale_stages?: StageName[]; pending_feedback?: string | null };
  stages: StageState[];
  pending_review: { stage: StageName; draft: string | null } | null;
  checkpoint: { thread_id: string; checkpoint_id: string | null; next: string[] } | null;
};

export type StageJob = { stage: StageName; status: StageStatus; job: Job };

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;
const stagePath = (projectId: string, stage: StageName, action: string) => `${base(projectId)}/stages/${stage}/${action}`;

export const workflowApi = {
  state: (projectId: string) => apiFetch<ProjectState>(`${base(projectId)}/state`),
  run: (projectId: string, stage: StageName) =>
    apiFetch<StageJob>(stagePath(projectId, stage, "run"), { method: "POST" }),
  regenerate: (projectId: string, stage: StageName, feedback?: string) =>
    apiFetch<StageJob>(stagePath(projectId, stage, "regenerate"), {
      method: "POST",
      json: { feedback: feedback?.trim() || null },
    }),
  approve: (projectId: string, stage: StageName, comment?: string) =>
    apiFetch<ProjectState>(stagePath(projectId, stage, "approve"), {
      method: "POST",
      json: { comment: comment?.trim() || null },
    }),
  reject: (projectId: string, stage: StageName, comment: string) =>
    apiFetch<ProjectState>(stagePath(projectId, stage, "reject"), { method: "POST", json: { comment } }),
  edit: (projectId: string, stage: StageName, content: string) =>
    apiFetch<ProjectState>(stagePath(projectId, stage, "edit"), { method: "POST", json: { content } }),
  goBack: (projectId: string, stage: StageName) =>
    apiFetch<ProjectState>(stagePath(projectId, stage, "go-back"), { method: "POST" }),
};
