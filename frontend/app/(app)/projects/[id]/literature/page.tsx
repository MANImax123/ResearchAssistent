"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { JobProgress } from "@/components/JobProgress";
import { AddPaper } from "@/components/literature/AddPaper";
import { AskPanel } from "@/components/literature/AskPanel";
import { DiscoverPanel } from "@/components/literature/DiscoverPanel";
import { FullTextPanel } from "@/components/literature/FullTextPanel";
import { PaperTable } from "@/components/literature/PaperTable";
import { QueryEditor } from "@/components/literature/QueryEditor";
import { RankPanel } from "@/components/literature/RankPanel";
import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import { projectsApi, type ProjectDetail } from "@/lib/api";
import { jobsApi, TERMINAL, useJob, type Job } from "@/lib/jobs";
import {
  literatureApi,
  type DiscoverOptions,
  type DiscoverySummary,
  type LiteratureWorkspace,
  type QueryDraft,
} from "@/lib/literature";
import { STATUS_META } from "@/lib/stages";

const JOB_LABEL: Record<string, string> = {
  literature_plan: "Planning agent",
  literature_discover: "Paper discovery",
  literature_rank: "Relevance ranking",
  literature_parse: "Full text",
  literature_index: "Indexing passages",
};

type Tab = "discover" | "fulltext" | "ask";
const TABS: { id: Tab; label: string }[] = [
  { id: "discover", label: "Discover" },
  { id: "fulltext", label: "Full text" },
  { id: "ask", label: "Ask" },
];

export default function LiteratureWorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [ws, setWs] = useState<LiteratureWorkspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<Job | null>(null);
  const { job: polled, error: pollError } = useJob(active?.id ?? null, active);
  const [summary, setSummary] = useState<DiscoverySummary | null>(null);
  const [libraryVersion, setLibraryVersion] = useState(0);
  const [tab, setTab] = useState<Tab>("discover");

  // The tab is kept in the URL hash ("#fulltext", "#ask"), so links and "Back" land on the right tab
  useEffect(() => {
    const follow = () => {
      const hash = window.location.hash.slice(1);
      if (TABS.some((t) => t.id === hash)) setTab(hash as Tab);
    };
    follow();
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);
  const chooseTab = (next: Tab) => {
    setTab(next);
    window.history.replaceState(
      null,
      "",
      next === "discover" ? window.location.pathname : `#${next}`,
    );
  };

  const load = useCallback(async () => {
    try {
      const [p, w] = await Promise.all([
        projectsApi.get(id),
        literatureApi.get(id),
      ]);
      setProject(p);
      setWs(w);
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load the Literature workspace",
      );
    }
  }, [id]);

  // On first load: resume a search that is still running, or show what the last one found
  useEffect(() => {
    void load();
    jobsApi
      .list(id)
      .then((jobs) => {
        const latest = jobs.find((j) => j.type in JOB_LABEL);
        if (latest && !TERMINAL.has(latest.status)) setActive(latest);
        const lastDiscovery = jobs.find(
          (j) => j.type === "literature_discover" && j.status === "success",
        );
        if (lastDiscovery?.result)
          setSummary(lastDiscovery.result as unknown as DiscoverySummary);
      })
      .catch(() => undefined);
  }, [id, load]);

  useEffect(() => {
    if (!polled || !TERMINAL.has(polled.status)) return;
    if (polled.status === "failed")
      setError(
        `${JOB_LABEL[polled.type] ?? "The job"} failed: ${polled.error ?? "unknown error"}`,
      );
    if (
      polled.type === "literature_discover" &&
      polled.status === "success" &&
      polled.result
    ) {
      setSummary(polled.result as unknown as DiscoverySummary);
    }
    setActive(null);
    setLibraryVersion((v) => v + 1);
    void load();
  }, [polled, load]);

  const targetText = useMemo(() => {
    const map: Record<string, string> = {};
    ws?.research_questions.forEach((q) => (map[q.id.toUpperCase()] = q.text));
    ws?.subtopics.forEach((t, i) => (map[`S${i + 1}`] = t));
    return map;
  }, [ws]);

  async function start(action: () => Promise<Job>) {
    setError(null);
    try {
      setActive(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start");
    }
  }

  async function changeSelection(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      setLibraryVersion((v) => v + 1);
      await load();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not change the selection",
      );
    }
  }

  async function saveQueries(queries: QueryDraft[]) {
    try {
      await literatureApi.saveQueries(id, queries);
      await load();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save the queries",
      );
      throw err;
    }
  }

  if (error && !ws) return <ErrorAlert>{error}</ErrorAlert>;
  if (!ws || !project)
    return <Spinner label="Loading the Literature workspace" />;

  const canEdit = ws.editable;
  const busy = active !== null;
  const status = STATUS_META[ws.status];

  return (
    <div className="space-y-8">
      <div>
        <Link
          href={`/projects/${id}`}
          className="text-sm text-slate-500 hover:text-slate-700"
        >
          ← {project.title}
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            Literature Survey
          </h1>
          <Badge className={status.className}>{status.label}</Badge>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          Find the papers your survey will draw on. Every paper comes from a
          real academic source with a verifiable identifier.
        </p>
        <nav
          aria-label="Literature steps"
          className="mt-4 flex gap-1 border-b border-slate-200 text-sm"
        >
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-current={tab === t.id ? "page" : undefined}
              onClick={() => chooseTab(t.id)}
              className={`-mb-px border-b-2 px-3 py-2 font-medium ${
                tab === t.id
                  ? "border-indigo-600 text-indigo-700"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              }`}
            >
              {t.label}
              {t.id === "fulltext" && ws.selected_count > 0 && (
                <span className="ml-1.5 text-xs text-slate-400">
                  {ws.parse_counts.parsed ?? 0}/{ws.selected_count}
                </span>
              )}
            </button>
          ))}
        </nav>
      </div>

      {error && (
        <ErrorAlert
          action={
            <Button
              variant="secondary"
              className="h-8"
              onClick={() => setError(null)}
            >
              Dismiss
            </Button>
          }
        >
          {error}
        </ErrorAlert>
      )}
      {!canEdit && ws.blocked_reason && (
        <p
          role="status"
          className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          {ws.blocked_reason}.
          {ws.blocked_reason.startsWith("Approve") && (
            <>
              {" "}
              <Link
                href={`/projects/${id}/idea`}
                className="font-medium underline"
              >
                Open the Idea workspace
              </Link>
            </>
          )}
        </p>
      )}

      {active && polled && (
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <JobProgress
            key={active.id}
            job={polled}
            label={JOB_LABEL[polled.type] ?? polled.type}
            pollError={pollError}
          />
        </div>
      )}

      {tab === "ask" ? (
        <AskPanel
          projectId={id}
          ws={ws}
          canEdit={canEdit}
          busy={busy}
          onIndex={() => void start(() => literatureApi.indexPassages(id))}
        />
      ) : tab === "fulltext" ? (
        <FullTextPanel
          projectId={id}
          ws={ws}
          canEdit={canEdit}
          busy={busy}
          refreshKey={libraryVersion}
          onParse={(options) =>
            void start(() => literatureApi.parse(id, options))
          }
          onChanged={() => void load()}
        />
      ) : (
        <>
          <QueryEditor
            queries={ws.queries}
            targetText={targetText}
            canEdit={canEdit}
            busy={busy}
            onSave={saveQueries}
            onSuggest={() =>
              void start(() => literatureApi.generateQueries(id))
            }
          />

          <DiscoverPanel
            sources={ws.sources}
            queryCount={ws.queries.length}
            canRun={canEdit}
            busy={busy}
            summary={busy ? null : summary}
            onRun={(options: DiscoverOptions) =>
              void start(() => literatureApi.discover(id, options))
            }
          />

          <RankPanel
            ws={ws}
            canEdit={canEdit}
            busy={busy}
            onRank={(topN) => void start(() => literatureApi.rank(id, topN))}
            onSelectTop={(k, minScore) =>
              void changeSelection(() =>
                literatureApi.selectTop(id, k, minScore),
              )
            }
            onClear={() =>
              void changeSelection(() => literatureApi.clearSelection(id))
            }
          />

          {canEdit && (
            <AddPaper
              projectId={id}
              onAdded={() => {
                setLibraryVersion((v) => v + 1);
                void load();
              }}
            />
          )}

          <PaperTable
            projectId={id}
            canEdit={canEdit}
            refreshKey={libraryVersion}
            rqIds={ws.research_questions.map((q) => q.id)}
            ranked={ws.judged_count > 0}
            onChanged={() => void load()}
          />
        </>
      )}
    </div>
  );
}
