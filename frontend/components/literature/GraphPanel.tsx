"use client";

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, ErrorAlert, Spinner } from "@/components/ui";
import {
  graphApi,
  NODE_COLOR,
  NODE_TYPES,
  relationText,
  TYPE_NAME,
  type GraphNode,
  type Neighbour,
  type NodeDetails,
  type Subgraph,
} from "@/lib/graph";

type Placed = GraphNode & SimulationNodeDatum & { r: number };
type PlacedEdge = SimulationLinkDatum<Placed> & { type: string };

const radius = (n: GraphNode) =>
  (n.type === "Paper" ? 9 : 5) + Math.min(10, Math.sqrt(n.degree) * 2);

function shortLabel(n: GraphNode): string {
  const text = n.label ?? n.id;
  const max = n.type === "Paper" ? 28 : 22;
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Lay the graph out once (no animation): deterministic, and cheap for a few hundred nodes. */
function layout(graph: Subgraph): {
  nodes: Placed[];
  edges: PlacedEdge[];
  box: string;
} {
  const nodes: Placed[] = graph.nodes.map((n, i) => {
    const angle = (i / Math.max(1, graph.nodes.length)) * 2 * Math.PI;
    return {
      ...n,
      r: radius(n),
      x: 200 * Math.cos(angle),
      y: 200 * Math.sin(angle),
    };
  });
  const ids = new Set(nodes.map((n) => n.id));
  const edges: PlacedEdge[] = graph.edges
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ source: e.source, target: e.target, type: e.type }));
  const sim = forceSimulation(nodes)
    .force(
      "link",
      forceLink<Placed, PlacedEdge>(edges)
        .id((d) => d.id)
        .distance(70),
    )
    .force("charge", forceManyBody().strength(-220))
    .force("center", forceCenter(0, 0))
    // Gentle gravity: papers without links stay near the rest instead of stretching the view
    .force("x", forceX(0).strength(0.06))
    .force("y", forceY(0).strength(0.06))
    .force(
      "collide",
      forceCollide<Placed>((d) => d.r + 6),
    )
    .stop();
  for (let i = 0; i < 300; i++) sim.tick();
  if (!nodes.length) return { nodes, edges, box: "-100 -100 200 200" };
  const pad = 60;
  const xs = nodes.map((n) => n.x ?? 0);
  const ys = nodes.map((n) => n.y ?? 0);
  const [x0, y0] = [Math.min(...xs) - pad, Math.min(...ys) - pad];
  const [w, h] = [Math.max(...xs) - x0 + pad, Math.max(...ys) - y0 + pad];
  return { nodes, edges, box: `${x0} ${y0} ${w} ${h}` };
}

function PropRows({ props }: { props: Record<string, unknown> }) {
  const rows: [string, string][] = [];
  if (props.year) rows.push(["Year", String(props.year)]);
  if (props.venue) rows.push(["Venue", String(props.venue)]);
  if (typeof props.relevance === "number")
    rows.push(["Relevance", `${Math.round(props.relevance * 100)}%`]);
  if (props.has_profile === false) rows.push(["Profile", "Not analysed yet"]);
  if (props.kind) rows.push(["Kind", String(props.kind)]);
  if (!rows.length) return null;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd className="text-slate-800">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Details({
  projectId,
  details,
  onSelect,
  onClose,
}: {
  projectId: string;
  details: NodeDetails;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const groups = new Map<string, Neighbour[]>();
  for (const n of details.neighbours) {
    const key = relationText(n.relation, n.outgoing);
    groups.set(key, [...(groups.get(key) ?? []), n]);
  }
  const paperKey =
    details.type === "Paper" ? details.id.slice("Paper:".length) : null;
  return (
    <aside
      aria-label={`Details: ${details.label ?? details.id}`}
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p
            className="text-xs font-semibold tracking-wide uppercase"
            style={{ color: NODE_COLOR[details.type] }}
          >
            {TYPE_NAME[details.type] ?? details.type}
          </p>
          <h3 className="mt-1 text-sm font-semibold break-words text-slate-900">
            {details.label ?? details.id}
          </h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="text-slate-400 hover:text-slate-600"
        >
          ×
        </button>
      </div>
      <PropRows props={details.props} />
      {paperKey && (
        <Link
          href={`/projects/${projectId}/literature/papers/${paperKey}`}
          className="inline-block text-sm font-medium text-indigo-700 hover:underline"
        >
          Open paper
        </Link>
      )}
      {groups.size === 0 && (
        <p className="text-sm text-slate-500">No connections.</p>
      )}
      {[...groups.entries()].map(([relation, items]) => (
        <div key={relation}>
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            {relation} ({items.length})
          </p>
          <ul className="mt-1 space-y-1">
            {items.map((n) => (
              <li key={`${relation}-${n.id}`}>
                <button
                  type="button"
                  onClick={() => onSelect(n.id)}
                  className="flex w-full items-start gap-2 text-left text-sm text-slate-800 hover:text-indigo-700"
                >
                  <span
                    aria-hidden
                    className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
                    style={{ background: NODE_COLOR[n.type] }}
                  />
                  <span className="min-w-0 break-words">
                    {n.label ?? n.id}
                    {n.props.proposed === true && (
                      <span className="ml-1 text-xs text-slate-500">
                        (proposed)
                      </span>
                    )}
                    {typeof n.props.score === "number" && (
                      <span className="ml-1 text-xs text-slate-500">
                        {Math.round(n.props.score * 100)}%
                      </span>
                    )}
                    {typeof n.props.topic === "string" && (
                      <span className="block text-xs text-slate-500">
                        {n.props.topic}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </aside>
  );
}

/** L9: the project's knowledge graph — papers linked to the methods, datasets and metrics they use. */
export function GraphPanel({
  projectId,
  canEdit,
  busy,
  selected,
  refreshKey,
  onBuild,
}: {
  projectId: string;
  canEdit: boolean;
  busy: boolean;
  selected: number;
  refreshKey: number;
  onBuild: () => void;
}) {
  const [types, setTypes] = useState<string[]>(NODE_TYPES.map((t) => t.type));
  const [graph, setGraph] = useState<Subgraph | null>(null);
  const [details, setDetails] = useState<NodeDetails | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Toggling filters quickly starts overlapping requests: only the latest one may update the graph
  useEffect(() => {
    let current = true;
    graphApi
      .get(projectId, types)
      .then((g) => {
        if (!current) return;
        setGraph(g);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!current) return;
        setError(
          err instanceof Error
            ? err.message
            : "Could not load the knowledge graph",
        );
      });
    return () => {
      current = false;
    };
  }, [projectId, types, refreshKey]);

  const select = useCallback(
    async (nodeId: string) => {
      try {
        setDetails(await graphApi.node(projectId, nodeId));
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Could not load that node",
        );
      }
    },
    [projectId],
  );

  const placed = useMemo(() => (graph ? layout(graph) : null), [graph]);
  const toggle = (type: string) =>
    setTypes((current) =>
      current.includes(type)
        ? current.filter((t) => t !== type)
        : [...current, type],
    );
  const empty = graph !== null && graph.nodes.length === 0;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    graph?.nodes.forEach((n) => (c[n.type] = (c[n.type] ?? 0) + 1));
    return c;
  }, [graph]);

  return (
    <section aria-labelledby="graph-heading" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="graph-heading" className="text-lg font-semibold">
            Knowledge graph
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            How your selected papers connect through the methods, datasets and
            metrics they use, their limitations and conflicts, and your research
            questions. Rebuild after changing the selection or analysing more
            papers.
          </p>
        </div>
        {canEdit && (
          <Button onClick={onBuild} disabled={busy || selected === 0}>
            {empty || graph === null ? "Build graph" : "Rebuild graph"}
          </Button>
        )}
      </div>

      {error && <ErrorAlert>{error}</ErrorAlert>}

      <fieldset className="flex flex-wrap gap-2">
        <legend className="sr-only">Node types to show</legend>
        {NODE_TYPES.map((t) => (
          <label
            key={t.type}
            className="flex cursor-pointer items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-sm"
          >
            <input
              type="checkbox"
              checked={types.includes(t.type)}
              onChange={() => toggle(t.type)}
              className="accent-indigo-600"
            />
            <span
              aria-hidden
              className="h-2.5 w-2.5 rounded-full"
              style={{ background: t.color }}
            />
            {t.label}
            {counts[t.type] !== undefined && (
              <span className="text-xs text-slate-400">{counts[t.type]}</span>
            )}
          </label>
        ))}
      </fieldset>

      {graph === null && !error ? (
        <Spinner label="Loading the knowledge graph" />
      ) : empty ? (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">
          {types.length === 0
            ? "Choose at least one node type to show."
            : selected === 0
              ? "Select papers on the Discover tab, then build the graph."
              : "Nothing to show yet. Build the graph from your selected papers."}
        </p>
      ) : (
        placed && (
          <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
            <svg
              role="img"
              aria-label={`Knowledge graph with ${placed.nodes.length} nodes and ${placed.edges.length} links`}
              viewBox={placed.box}
              className="h-[36rem] w-full rounded-xl border border-slate-200 bg-white"
            >
              <g stroke="#cbd5e1" strokeWidth={1}>
                {placed.edges.map((e, i) => {
                  const s = e.source as Placed;
                  const t = e.target as Placed;
                  return (
                    <line
                      key={i}
                      x1={s.x}
                      y1={s.y}
                      x2={t.x}
                      y2={t.y}
                      stroke={e.type === "CONTRADICTS" ? "#dc2626" : undefined}
                      strokeDasharray={
                        e.type === "CONTRADICTS" ? "4 3" : undefined
                      }
                    />
                  );
                })}
              </g>
              {placed.nodes.map((n) => {
                const active = details?.id === n.id;
                return (
                  <g
                    key={n.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`${TYPE_NAME[n.type] ?? n.type}: ${n.label ?? n.id}`}
                    aria-pressed={active}
                    transform={`translate(${n.x},${n.y})`}
                    className="cursor-pointer focus:outline-none"
                    onClick={() => void select(n.id)}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        void select(n.id);
                      }
                    }}
                  >
                    <title>{n.label ?? n.id}</title>
                    <circle
                      r={n.r}
                      fill={NODE_COLOR[n.type] ?? "#94a3b8"}
                      stroke={active ? "#0f172a" : "#fff"}
                      strokeWidth={active ? 3 : 1.5}
                    />
                    {n.type !== "Limitation" && (
                      <text
                        y={n.r + 11}
                        textAnchor="middle"
                        fontSize={10}
                        fill="#334155"
                        stroke="#fff"
                        strokeWidth={3}
                        paintOrder="stroke"
                      >
                        {shortLabel(n)}
                      </text>
                    )}
                  </g>
                );
              })}
            </svg>
            {details ? (
              <Details
                projectId={projectId}
                details={details}
                onSelect={(nodeId) => void select(nodeId)}
                onClose={() => setDetails(null)}
              />
            ) : (
              <p className="rounded-xl border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
                Click a node to see its details and connections — for example,
                which papers use a method.
              </p>
            )}
          </div>
        )
      )}
    </section>
  );
}
