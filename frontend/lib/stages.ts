/** Stage metadata shared by the dashboard and stage tracker (order matches the backend StageName enum). */

export type StageName =
  | "idea"
  | "literature"
  | "methodology"
  | "results"
  | "introduction"
  | "conclusion"
  | "references"
  | "review"
  | "formatting";

export type StageStatus = "not_started" | "running" | "awaiting_review" | "approved" | "rejected" | "stale";

export const STAGES: { name: StageName; label: string; description: string }[] = [
  { name: "idea", label: "Idea & Problem", description: "Problem statement, objectives, research questions" },
  { name: "literature", label: "Literature Survey", description: "Discover, analyse and compare papers; find gaps" },
  { name: "methodology", label: "Methodology", description: "Structure your architecture, data and setup" },
  { name: "results", label: "Results & Discussion", description: "Analyse your data, tables, charts, interpretation" },
  { name: "introduction", label: "Introduction", description: "Introduction and final abstract" },
  { name: "conclusion", label: "Conclusion", description: "Findings, contributions, limitations, future work" },
  { name: "references", label: "References", description: "Verified bibliography and citation style" },
  { name: "review", label: "Review", description: "Consistency and completeness checks" },
  { name: "formatting", label: "Formatting & Export", description: "Venue template; LaTeX, DOCX, PDF" },
];

export const STAGE_LABEL = Object.fromEntries(STAGES.map((s) => [s.name, s.label])) as Record<StageName, string>;

export const STATUS_META: Record<StageStatus, { label: string; className: string }> = {
  not_started: { label: "Not started", className: "bg-slate-100 text-slate-600" },
  running: { label: "Running", className: "bg-blue-100 text-blue-700" },
  awaiting_review: { label: "Awaiting review", className: "bg-amber-100 text-amber-800" },
  approved: { label: "Approved", className: "bg-emerald-100 text-emerald-700" },
  rejected: { label: "Rejected", className: "bg-red-100 text-red-700" },
  stale: { label: "Needs review", className: "bg-orange-100 text-orange-800" },
};

export const VENUES: { value: string; label: string }[] = [
  { value: "ieee", label: "IEEE Conference" },
  { value: "springer_lncs", label: "Springer LNCS" },
  { value: "acm", label: "ACM" },
  { value: "elsevier", label: "Elsevier" },
];

export function venueLabel(value: string | null): string | null {
  if (!value) return null;
  return VENUES.find((v) => v.value === value)?.label ?? value;
}
