"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { FilesPanel } from "@/components/FilesPanel";
import { PlanCards } from "@/components/idea/PlanCards";
import { JobProgress } from "@/components/JobProgress";
import { Badge, Button, ErrorAlert, Spinner, TextArea } from "@/components/ui";
import { projectsApi, type ProjectDetail } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { ideaApi, type IdeaPlan, type IdeaWorkspace } from "@/lib/idea";
import { TERMINAL, useJob, type Job } from "@/lib/jobs";
import { STATUS_META } from "@/lib/stages";
import { workflowApi } from "@/lib/workflow";

export default function IdeaWorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [ws, setWs] = useState<IdeaWorkspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rawIdea, setRawIdea] = useState("");
  const [ideaSaved, setIdeaSaved] = useState(true);
  const [instructions, setInstructions] = useState("");
  const [job, setJobStart] = useState<Job | null>(null);
  const { job: polled, error: pollError } = useJob(job?.id ?? null, job);
  const [rejecting, setRejecting] = useState(false);
  const [rejectComment, setRejectComment] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, w] = await Promise.all([projectsApi.get(id), ideaApi.get(id)]);
      setProject(p);
      setWs(w);
      setRawIdea(w.raw_idea ?? "");
      setIdeaSaved(true);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the Idea workspace");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!polled || !TERMINAL.has(polled.status)) return;
    if (polled.status === "failed") setError(`The Idea agent failed: ${polled.error ?? "unknown error"}`);
    setJobStart(null);
    setInstructions("");
    void load();
  }, [polled, load]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  if (error && !ws) return <ErrorAlert>{error}</ErrorAlert>;
  if (!ws || !project) return <Spinner label="Loading the Idea workspace" />;

  const canEdit = project.role === "owner" || project.role === "editor";
  const running = ws.status === "running" || job !== null;
  const reviewing = ws.status === "awaiting_review";
  const hasPlan = ws.plan !== null;
  const status = STATUS_META[ws.status];

  async function saveIdea() {
    await run(async () => {
      await projectsApi.update(id, { raw_idea: rawIdea.trim() || null });
      setIdeaSaved(true);
    });
  }

  async function generate() {
    await run(async () => {
      if (!ideaSaved) await projectsApi.update(id, { raw_idea: rawIdea.trim() || null });
      setIdeaSaved(true);
      const started =
        ws!.status === "not_started" || ws!.status === "rejected" || ws!.status === "stale"
          ? await workflowApi.run(id, "idea", instructions)
          : await workflowApi.regenerate(id, "idea", instructions);
      setJobStart(started.job);
      setWs({ ...ws!, status: "running" });
    });
  }

  async function savePlan(plan: IdeaPlan) {
    setWs(await ideaApi.savePlan(id, plan));
  }

  return (
    <div className="space-y-8">
      <div>
        <Link href={`/projects/${id}`} className="text-sm text-slate-500 hover:text-slate-700">
          ← {project.title}
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Idea &amp; Problem</h1>
          <Badge className={status.className}>{status.label}</Badge>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          Describe your idea, add notes, and let the agent turn it into a research plan. You review and edit every
          part before approving.
        </p>
      </div>

      {error && (
        <ErrorAlert
          action={
            <Button variant="secondary" className="h-8" onClick={() => setError(null)}>
              Dismiss
            </Button>
          }
        >
          {error}
        </ErrorAlert>
      )}

      <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
        <TextArea
          label="Your research idea"
          id="raw-idea"
          rows={5}
          value={rawIdea}
          disabled={!canEdit || running}
          onChange={(e) => {
            setRawIdea(e.target.value);
            setIdeaSaved(false);
          }}
          placeholder="What do you want to investigate, and why? Rough notes are fine."
        />
        {canEdit && !ideaSaved && (
          <Button variant="secondary" className="h-8 px-3" onClick={() => void saveIdea()} loading={busy}>
            Save idea
          </Button>
        )}
      </section>

      <FilesPanel projectId={id} canEdit={canEdit} kinds={["notes"]} title="Notes for the agent" />

      {canEdit && (
        <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
          <TextArea
            label={hasPlan ? "What should change in the next version? (optional)" : "Instructions for the agent (optional)"}
            id="instructions"
            rows={3}
            value={instructions}
            disabled={running}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder={hasPlan ? "e.g. Add an objective on fairness; make RQ2 a hypothesis" : "e.g. Keep it to one paper's worth of work"}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void generate()} loading={busy && !running} disabled={running || !rawIdea.trim()}>
              {running ? "Agent is working…" : hasPlan ? "Regenerate plan" : "Generate plan"}
            </Button>
            {ws.status === "approved" && (
              <span className="text-xs text-slate-500">Regenerating an approved plan marks later stages as needing review.</span>
            )}
            {!rawIdea.trim() && <span className="text-xs text-slate-500">Write your idea first.</span>}
          </div>
          {job && polled && <JobProgress key={job.id} job={polled} label="Idea agent" pollError={pollError} />}
        </section>
      )}

      {hasPlan && (
        <section className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <h2 className="text-base font-semibold">Research plan</h2>
            <span className="text-xs text-slate-500">
              {ws.generated_at && `Generated ${relativeTime(ws.generated_at)}`}
              {!ws.editable && ws.edit_blocked_reason && ` · Read-only: ${ws.edit_blocked_reason}`}
            </span>
          </div>
          <PlanCards plan={ws.plan!} editable={ws.editable && !running} onSave={savePlan} />
        </section>
      )}

      {hasPlan && reviewing && canEdit && !running && (
        <section aria-label="Review" className="space-y-3 rounded-xl border-2 border-amber-300 bg-amber-50/40 p-5">
          <h2 className="text-base font-semibold">Ready to approve?</h2>
          <p className="text-sm text-slate-600">
            Approving makes this plan the basis for the literature survey and every later stage.
          </p>
          {rejecting && (
            <TextArea
              label="What's wrong with this plan?"
              id="reject-plan"
              rows={3}
              value={rejectComment}
              onChange={(e) => setRejectComment(e.target.value)}
              hint="Required. The agent sees this when you generate again."
            />
          )}
          <div className="flex flex-wrap gap-2">
            {!rejecting ? (
              <>
                <Button
                  onClick={() =>
                    run(async () => {
                      await workflowApi.approve(id, "idea");
                    }).then(load)
                  }
                  loading={busy}
                >
                  Approve plan
                </Button>
                <Button variant="danger-outline" onClick={() => setRejecting(true)} disabled={busy}>
                  Reject
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="danger"
                  disabled={!rejectComment.trim()}
                  loading={busy}
                  onClick={() =>
                    run(async () => {
                      await workflowApi.reject(id, "idea", rejectComment.trim());
                      setRejecting(false);
                      setRejectComment("");
                    }).then(load)
                  }
                >
                  Reject plan
                </Button>
                <Button variant="secondary" onClick={() => setRejecting(false)} disabled={busy}>
                  Cancel
                </Button>
              </>
            )}
          </div>
        </section>
      )}

      {ws.status === "approved" && (
        <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          Plan approved. Next: the Literature Survey, from the{" "}
          <Link href={`/projects/${id}`} className="font-medium underline">
            project page
          </Link>
          .
        </p>
      )}
    </div>
  );
}
