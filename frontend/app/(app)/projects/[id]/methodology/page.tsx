"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { FilesPanel } from "@/components/FilesPanel";
import { JobProgress } from "@/components/JobProgress";
import { FollowUpPanel } from "@/components/methodology/FollowUpPanel";
import { InputForm } from "@/components/methodology/InputForm";
import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import { projectsApi, type ProjectDetail } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { jobsApi, TERMINAL, useJob, type Job } from "@/lib/jobs";
import {
  cleaned,
  methodologyApi,
  type FieldName,
  type FollowUpQuestion,
  type MethodologyInput,
  type MethodologyWorkspace,
} from "@/lib/methodology";
import { STATUS_META } from "@/lib/stages";

export default function MethodologyWorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [ws, setWs] = useState<MethodologyWorkspace | null>(null);
  const [draft, setDraft] = useState<MethodologyInput | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<Job | null>(null);
  const { job: polled, error: pollError } = useJob(active?.id ?? null, active);

  const load = useCallback(
    async (keepDraft = false) => {
      try {
        const [p, w] = await Promise.all([
          projectsApi.get(id),
          methodologyApi.get(id),
        ]);
        setProject(p);
        setWs(w);
        if (!keepDraft) setDraft(w.input);
        setError(null);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Could not load the Methodology workspace",
        );
      }
    },
    [id],
  );

  useEffect(() => {
    void load();
    jobsApi
      .list(id)
      .then((jobs) => {
        const running = jobs.find(
          (j) => j.type === "methodology_follow_up" && !TERMINAL.has(j.status),
        );
        if (running) setActive(running);
      })
      .catch(() => undefined);
  }, [id, load]);

  useEffect(() => {
    if (!polled || !TERMINAL.has(polled.status)) return;
    if (polled.status === "failed")
      setError(
        `The follow-up check failed: ${polled.error ?? "unknown error"}`,
      );
    setActive(null);
    void load(true);
  }, [polled, load]);

  const unsaved = useMemo(
    () =>
      ws !== null &&
      draft !== null &&
      JSON.stringify(cleaned(draft)) !== JSON.stringify(cleaned(ws.input)),
    [ws, draft],
  );

  useEffect(() => {
    if (!unsaved) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  async function save() {
    if (!ws || !draft) return;
    setSaving(true);
    setError(null);
    try {
      const next = await methodologyApi.save(id, draft, ws.version);
      setWs(next);
      setDraft(next.input);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  async function ask() {
    setError(null);
    try {
      setActive(await methodologyApi.askFollowUps(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start");
    }
  }

  async function updateQuestion(
    q: FollowUpQuestion,
    status: FollowUpQuestion["status"],
    answer?: string,
  ) {
    setError(null);
    try {
      await methodologyApi.updateQuestion(id, q.id, status, answer);
      await load(true);
      return true;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save the answer",
      );
      return false;
    }
  }

  if (error && !ws) return <ErrorAlert>{error}</ErrorAlert>;
  if (!ws || !project || !draft)
    return <Spinner label="Loading the Methodology workspace" />;

  const status = STATUS_META[ws.stage_status];
  const missingFields = new Set<FieldName>(ws.missing.map((m) => m.field));
  const busy = active !== null;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/projects/${id}`}
          className="text-sm text-slate-500 hover:text-slate-700"
        >
          ← {project.title}
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Methodology</h1>
          <Badge className={status.className}>{status.label}</Badge>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          Describe how you did the work. Your details are used as written: the
          agent structures and phrases them, but never changes what they say or
          invents values.
        </p>
      </div>

      {error && <ErrorAlert>{error}</ErrorAlert>}
      {ws.blocked_reason && (
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
            label="Follow-up check"
            pollError={pollError}
          />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-6">
          <section
            aria-label="Methodology details"
            className="rounded-xl border border-slate-200 bg-white p-5"
          >
            <InputForm
              value={draft}
              missing={missingFields}
              disabled={!ws.editable || saving}
              onChange={setDraft}
            />
            {ws.editable && (
              <div className="sticky bottom-0 mt-6 flex items-center gap-3 border-t border-slate-100 bg-white pt-4">
                <Button
                  onClick={() => void save()}
                  disabled={!unsaved || saving}
                >
                  {saving ? "Saving…" : "Save"}
                </Button>
                <span className="text-xs text-slate-500">
                  {unsaved
                    ? "Unsaved changes"
                    : ws.updated_at
                      ? `Saved ${relativeTime(ws.updated_at)}`
                      : "Nothing saved yet"}
                </span>
              </div>
            )}
          </section>
          <FilesPanel
            projectId={id}
            canEdit={ws.editable}
            kinds={["diagram", "code", "notes"]}
            title="Diagrams, code and notes"
            onChanged={() => void load(true)}
          />
        </div>
        <FollowUpPanel
          missing={ws.missing}
          questions={ws.questions}
          canEdit={ws.editable}
          busy={busy}
          unsaved={unsaved}
          onAsk={() => void ask()}
          onUpdate={updateQuestion}
        />
      </div>
    </div>
  );
}
