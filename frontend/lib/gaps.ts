import { apiFetch } from "@/lib/api";
import type { Job } from "@/lib/jobs";
import type { Evidence } from "@/lib/literature";

export type GapStatus = "candidate" | "accepted" | "rejected";
export type GapConfidence = "low" | "medium" | "high";
export type GapSignalKind = "limitation" | "contradiction" | "untested" | "future_work";

export type GapSignal = {
  kind: GapSignalKind;
  text: string;
  papers: { id: string; title: string; year: number | null }[];
  evidence: Evidence[];
};

export type Gap = {
  id: string;
  statement: string;
  rationale: string | null;
  confidence: GapConfidence;
  status: GapStatus;
  objectives: { id: string; text: string }[];
  signals: GapSignal[];
  edited: boolean;
  decided_at: string | null;
  created_at: string;
};

export type GapChanges = Partial<{
  statement: string;
  rationale: string;
  confidence: GapConfidence;
  objective_ids: string[];
  status: GapStatus;
}>;

export const SIGNAL_LABEL: Record<GapSignalKind, string> = {
  limitation: "Limitation",
  contradiction: "Conflicting results",
  untested: "Untested combination",
  future_work: "Future work",
};

export const CONFIDENCE_META: Record<GapConfidence, { label: string; className: string }> = {
  high: { label: "High confidence", className: "bg-emerald-50 text-emerald-700" },
  medium: { label: "Medium confidence", className: "bg-amber-50 text-amber-700" },
  low: { label: "Low confidence", className: "bg-slate-100 text-slate-600" },
};

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/gaps`;

export const gapsApi = {
  detect: (projectId: string) => apiFetch<Job>(`${base(projectId)}/detect`, { method: "POST" }),
  list: (projectId: string) => apiFetch<Gap[]>(base(projectId)),
  update: (projectId: string, gapId: string, changes: GapChanges) =>
    apiFetch<Gap>(`${base(projectId)}/${encodeURIComponent(gapId)}`, { method: "PATCH", json: changes }),
};
