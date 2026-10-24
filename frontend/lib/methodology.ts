import { apiFetch } from "@/lib/api";
import type { Job } from "@/lib/jobs";
import type { StageStatus } from "@/lib/stages";

export type DatasetInfo = {
  name: string;
  source: string;
  size: string;
  features: string;
  notes: string;
};
export type Hyperparameter = { name: string; value: string; notes: string };
export type Tool = { name: string; version: string; purpose: string };
export type Metric = { name: string; notes: string };

export type MethodologyInput = {
  architecture: string;
  algorithms: string;
  workflow: string;
  datasets: DatasetInfo[];
  preprocessing: string;
  experimental_setup: string;
  hyperparameters: Hyperparameter[];
  tools: Tool[];
  metrics: Metric[];
  reproducibility: string;
  notes: string;
};

export type TextField =
  | "architecture"
  | "algorithms"
  | "workflow"
  | "preprocessing"
  | "experimental_setup"
  | "reproducibility"
  | "notes";
export type ListField = "datasets" | "hyperparameters" | "tools" | "metrics";
export type FieldName = Exclude<TextField, "notes"> | ListField;

export type MissingItem = {
  field: FieldName;
  label: string;
  reason: string;
  essential: boolean;
};

export type FollowUpQuestion = {
  id: string;
  field: FieldName;
  label: string;
  question: string;
  reason: string | null;
  kind: "missing" | "unclear";
  status: "open" | "answered" | "dismissed";
  answer: string | null;
  answered_at: string | null;
  created_at: string;
};

export type MethodologyWorkspace = {
  input: MethodologyInput;
  version: number;
  updated_at: string | null;
  editable: boolean;
  blocked_reason: string | null;
  stage_status: StageStatus;
  missing: MissingItem[];
  files: { diagram: number; code: number; notes: number };
  questions: FollowUpQuestion[];
};

export const EMPTY_INPUT: MethodologyInput = {
  architecture: "",
  algorithms: "",
  workflow: "",
  datasets: [],
  preprocessing: "",
  experimental_setup: "",
  hyperparameters: [],
  tools: [],
  metrics: [],
  reproducibility: "",
  notes: "",
};

export const EMPTY_ROW = {
  datasets: { name: "", source: "", size: "", features: "", notes: "" },
  hyperparameters: { name: "", value: "", notes: "" },
  tools: { name: "", version: "", purpose: "" },
  metrics: { name: "", notes: "" },
} satisfies { [K in ListField]: MethodologyInput[K][number] };

/** Rows without a name are dropped before saving (the API requires a name). */
export function cleaned(input: MethodologyInput): MethodologyInput {
  const named = <T extends { name: string }>(rows: T[]) =>
    rows.filter((r) => r.name.trim());
  return {
    ...input,
    datasets: named(input.datasets),
    hyperparameters: named(input.hyperparameters).filter((h) => h.value.trim()),
    tools: named(input.tools),
    metrics: named(input.metrics),
  };
}

const base = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/methodology`;

export const methodologyApi = {
  get: (projectId: string) => apiFetch<MethodologyWorkspace>(base(projectId)),
  save: (projectId: string, input: MethodologyInput, baseVersion: number) =>
    apiFetch<MethodologyWorkspace>(base(projectId), {
      method: "PUT",
      json: { input: cleaned(input), base_version: baseVersion },
    }),
  askFollowUps: (projectId: string) =>
    apiFetch<Job>(`${base(projectId)}/follow-ups`, { method: "POST" }),
  updateQuestion: (
    projectId: string,
    questionId: string,
    status: FollowUpQuestion["status"],
    answer?: string,
  ) =>
    apiFetch<FollowUpQuestion>(
      `${base(projectId)}/questions/${encodeURIComponent(questionId)}`,
      {
        method: "PATCH",
        json: { status, answer: answer?.trim() || null },
      },
    ),
};
