import { apiFetch } from "@/lib/api";
import type { Job } from "@/lib/jobs";

export type NodeType = "Paper" | "Method" | "Dataset" | "Metric" | "Limitation" | "Objective" | "ResearchQuestion";

// The Project node only anchors the graph in Neo4j; it has no edges worth drawing
export const NODE_TYPES: { type: NodeType; label: string; color: string }[] = [
  { type: "Paper", label: "Papers", color: "#4f46e5" },
  { type: "Method", label: "Methods", color: "#0891b2" },
  { type: "Dataset", label: "Datasets", color: "#16a34a" },
  { type: "Metric", label: "Metrics", color: "#d97706" },
  { type: "Limitation", label: "Limitations", color: "#dc2626" },
  { type: "ResearchQuestion", label: "Research questions", color: "#9333ea" },
  { type: "Objective", label: "Objectives", color: "#64748b" },
];

export const NODE_COLOR: Record<string, string> = Object.fromEntries(NODE_TYPES.map((t) => [t.type, t.color]));
export const TYPE_NAME: Record<string, string> = {
  Paper: "Paper",
  Method: "Method",
  Dataset: "Dataset",
  Metric: "Metric",
  Limitation: "Limitation",
  ResearchQuestion: "Research question",
  Objective: "Objective",
};

export type GraphNode = { id: string; type: string; label: string | null; degree: number; props: Record<string, unknown> };
export type GraphEdge = { source: string; target: string; type: string; props: Record<string, unknown> };
export type Subgraph = { nodes: GraphNode[]; edges: GraphEdge[] };
export type Neighbour = {
  id: string;
  type: string;
  label: string | null;
  relation: string;
  outgoing: boolean;
  props: Record<string, unknown>;
};
export type NodeDetails = {
  id: string;
  type: string;
  label: string | null;
  props: Record<string, unknown>;
  neighbours: Neighbour[];
};

// How a relation reads from the selected node's side: [outgoing, incoming]
export const RELATION_TEXT: Record<string, [string, string]> = {
  USES: ["Uses", "Used by"],
  EVALUATED_ON: ["Evaluated on", "Evaluates"],
  REPORTS: ["Reports", "Reported by"],
  HAS_LIMITATION: ["Limitations", "Limitation of"],
  CONTRADICTS: ["Contradicts", "Contradicted by"],
  RELEVANT_TO: ["Relevant to", "Relevant papers"],
  SUPPORTS: ["Supports", "Supported by"],
};

export function relationText(relation: string, outgoing: boolean): string {
  const text = RELATION_TEXT[relation];
  return text ? text[outgoing ? 0 : 1] : relation;
}

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/kg`;

export const graphApi = {
  build: (projectId: string) => apiFetch<Job>(`${base(projectId)}/build`, { method: "POST" }),
  get: (projectId: string, types: string[]) =>
    apiFetch<Subgraph>(`${base(projectId)}${types.length ? `?${types.map((t) => `types=${t}`).join("&")}` : ""}`),
  node: (projectId: string, nodeId: string) =>
    apiFetch<NodeDetails>(`${base(projectId)}/nodes/${encodeURIComponent(nodeId)}`),
};
