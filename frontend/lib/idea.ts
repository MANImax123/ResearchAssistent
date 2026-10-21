import { apiFetch } from "@/lib/api";
import type { Job } from "@/lib/jobs";
import type { StageStatus } from "@/lib/stages";

export type Objective = { id: string; text: string };
export type ResearchQuestion = {
  id: string;
  text: string;
  kind: "question" | "hypothesis";
  linked_objective_ids: string[];
};

export type IdeaPlan = {
  refined_idea: string;
  domain: string;
  keywords: string[];
  subtopics: string[];
  problem_statement: string;
  motivation: string;
  scope: string;
  expected_contributions: string[];
  objectives: Objective[];
  research_questions: ResearchQuestion[];
  research_directions: string[];
  title_options: string[];
  title: string | null;
};

export type CriticName = "novelty" | "feasibility" | "methodology";

export type Suggestion = { id: string; title: string; detail: string; affects: string[] };

export type Critique = {
  score: number;
  summary: string;
  strengths: string[];
  risks: string[];
  suggestions: Suggestion[];
};

export type CritiqueSet = {
  round: number;
  generated_at: string;
  critiques: Partial<Record<CriticName, Critique>>;
  agent_run_ids: Record<string, string>;
  applied: string[];
};

export const CRITIC_LABEL: Record<CriticName, { title: string; question: string }> = {
  novelty: { title: "Novelty", question: "Is the contribution distinct and clearly positioned?" },
  feasibility: { title: "Feasibility", question: "Can this be done in one paper?" },
  methodology: { title: "Methodology", question: "Are the objectives measurable and the questions answerable?" },
};

/** Readable names for the plan fields a suggestion would change. */
export const FIELD_LABEL: Record<string, string> = {
  refined_idea: "Refined idea",
  domain: "Domain",
  keywords: "Keywords",
  subtopics: "Subtopics",
  problem_statement: "Problem statement",
  motivation: "Motivation",
  scope: "Scope",
  expected_contributions: "Contributions",
  objectives: "Objectives",
  research_questions: "Research questions",
  research_directions: "Directions",
  title_options: "Title options",
};

export type IdeaWorkspace = {
  status: StageStatus;
  raw_idea: string | null;
  plan: IdeaPlan | null;
  editable: boolean;
  edit_blocked_reason: string | null;
  generated_at: string | null;
  agent_run_id: string | null;
  pending_feedback: string | null;
  critiques: CritiqueSet | null;
};

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/idea`;

export const ideaApi = {
  get: (projectId: string) => apiFetch<IdeaWorkspace>(base(projectId)),
  savePlan: (projectId: string, plan: IdeaPlan) =>
    apiFetch<IdeaWorkspace>(`${base(projectId)}/plan`, { method: "PUT", json: plan }),
  critique: (projectId: string) => apiFetch<Job>(`${base(projectId)}/critiques`, { method: "POST" }),
  revise: (projectId: string, suggestionIds: string[]) =>
    apiFetch<Job>(`${base(projectId)}/revise`, { method: "POST", json: { suggestion_ids: suggestionIds } }),
};

/** Next free id with the given prefix ("O" → "O4" when O1–O3 exist). */
export function nextId(prefix: "O" | "RQ", existing: { id: string }[]): string {
  const numbers = existing.map((x) => Number(x.id.slice(prefix.length))).filter(Number.isFinite);
  return `${prefix}${Math.max(0, ...numbers) + 1}`;
}

/** Problems the server would reject, so the researcher sees them before saving. */
export function planProblems(plan: IdeaPlan): string[] {
  const problems: string[] = [];
  const ids = new Set(plan.objectives.map((o) => o.id));
  for (const q of plan.research_questions) {
    if (q.kind === "hypothesis" && q.text.trim().endsWith("?"))
      problems.push(`${q.id} is a hypothesis: state it as a claim, or mark it as a question`);
    const missing = q.linked_objective_ids.filter((id) => !ids.has(id));
    if (q.linked_objective_ids.length === 0) problems.push(`${q.id} must serve at least one objective`);
    else if (missing.length) problems.push(`${q.id} links to removed objective ${missing.join(", ")}`);
  }
  const used = new Set(plan.research_questions.flatMap((q) => q.linked_objective_ids));
  const orphans = plan.objectives.filter((o) => !used.has(o.id)).map((o) => o.id);
  if (orphans.length) problems.push(`No research question serves ${orphans.join(", ")}`);
  if (plan.objectives.length < 3 || plan.objectives.length > 6) problems.push("Keep 3–6 objectives");
  if (plan.research_questions.length < 2) problems.push("Keep at least 2 research questions");
  return problems;
}
