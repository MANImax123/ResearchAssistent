"use client";

import { useCallback, useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { JobProgress } from "@/components/JobProgress";
import { ReviewPanel } from "@/components/ReviewPanel";
import { StageTracker } from "@/components/StageTracker";
import { Button, ErrorAlert, LinkButton, Spinner } from "@/components/ui";
import { TERMINAL, useJob, type Job } from "@/lib/jobs";
import { STAGE_LABEL, type StageName } from "@/lib/stages";
import { workflowApi, type ProjectState, type StageJob } from "@/lib/workflow";

const POLL_WHILE_RUNNING_MS = 2000;

/** Stages with a dedicated workspace page (more get added as their agents are built). */
const STAGE_WORKSPACE: Partial<Record<StageName, (projectId: string) => string>> = {
  idea: (projectId) => `/projects/${projectId}/idea`,
  literature: (projectId) => `/projects/${projectId}/literature`,
};

/** Stage tracker + review panel: runs stages, shows agent progress, applies review actions. */
export function StagesWorkspace({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const [state, setState] = useState<ProjectState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<{ stage: StageName; job: Job } | null>(null);
  const [confirmBack, setConfirmBack] = useState<StageName | null>(null);
  const { job, error: pollError } = useJob(active?.job.id ?? null, active?.job ?? null);

  const load = useCallback(async () => {
    try {
      setState(await workflowApi.state(projectId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load stages");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  // When the agent's job finishes, refresh the stages (and surface a failure)
  useEffect(() => {
    if (!job || !TERMINAL.has(job.status)) return;
    if (job.status === "failed") setError(job.error ?? "The agent failed");
    setActive(null);
    void load();
  }, [job, load]);

  // A stage may be running without a job we know of (e.g. after a page reload): poll until it's done
  const runningStage = state?.stages.find((s) => s.status === "running")?.name;
  useEffect(() => {
    if (!runningStage || active) return;
    const timer = setInterval(() => void load(), POLL_WHILE_RUNNING_MS);
    return () => clearInterval(timer);
  }, [runningStage, active, load]);

  async function startJob(request: Promise<StageJob>) {
    setError(null);
    const started = await request;
    setActive({ stage: started.stage, job: started.job });
    setState((prev) =>
      prev && {
        ...prev,
        stages: prev.stages.map((s) => (s.name === started.stage ? { ...s, status: "running" as const } : s)),
      },
    );
  }

  async function act(request: Promise<ProjectState>) {
    setError(null);
    setState(await request);
  }

  async function guarded(action: () => Promise<void>) {
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    }
  }

  if (!state) {
    return error ? <ErrorAlert>{error}</ErrorAlert> : <Spinner label="Loading stages" />;
  }

  const pending = state.pending_review;
  const stale = state.stages.filter((s) => s.status === "stale");
  const current = (state.state.current_stage as StageName | undefined) ?? "idea";

  return (
    <div className="space-y-5">
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

      {active && job && <JobProgress key={job.id} job={job} label={`Agent: ${STAGE_LABEL[active.stage]}`} pollError={pollError} />}
      {!active && runningStage && <Spinner label={`${STAGE_LABEL[runningStage]} is running`} />}

      {pending && !active && (
        <ReviewPanel
          key={`${pending.stage}-${state.checkpoint?.checkpoint_id}`}
          projectId={projectId}
          stage={pending.stage}
          workspaceHref={STAGE_WORKSPACE[pending.stage]?.(projectId)}
          draft={pending.draft}
          canAct={canEdit}
          handlers={{
            approve: (comment) => act(workflowApi.approve(projectId, pending.stage, comment)),
            reject: (comment) => act(workflowApi.reject(projectId, pending.stage, comment)),
            edit: (content) => act(workflowApi.edit(projectId, pending.stage, content)),
            regenerate: (feedback) => startJob(workflowApi.regenerate(projectId, pending.stage, feedback)),
          }}
        />
      )}

      {stale.length > 0 && (
        <div role="status" className="rounded-lg border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900">
          <strong className="font-medium">Needs review: </strong>
          {stale.map((s) => STAGE_LABEL[s.name]).join(", ")}. An earlier stage changed, so{" "}
          {stale.length === 1 ? "this stage is" : "these stages are"} out of date. Run {stale.length === 1 ? "it" : "them"}{" "}
          again when you&apos;re ready.
        </div>
      )}

      <StageTracker
        stages={state.stages}
        current={current}
        actions={(name) => {
          if (!canEdit || active) return null;
          const s = state.stages.find((x) => x.name === name);
          if (!s) return null;
          const workspace = STAGE_WORKSPACE[name]?.(projectId);
          return (
            <>
              {workspace && (
                <LinkButton variant="secondary" className="h-8 px-3" href={workspace}>
                  Open workspace
                </LinkButton>
              )}
              {s.can_run && (
                <Button className="h-8 px-3" onClick={() => guarded(() => startJob(workflowApi.run(projectId, name)))}>
                  {s.status === "not_started" ? "Run" : "Run again"}
                </Button>
              )}
              {s.can_regenerate && s.status === "approved" && (
                <Button
                  variant="secondary"
                  className="h-8 px-3"
                  onClick={() => guarded(() => startJob(workflowApi.regenerate(projectId, name)))}
                >
                  Regenerate
                </Button>
              )}
              {s.can_go_back && (
                <Button variant="secondary" className="h-8 px-3" onClick={() => setConfirmBack(name)}>
                  Go back
                </Button>
              )}
            </>
          );
        }}
      />

      {confirmBack && (
        <ConfirmDialog
          title={`Go back to ${STAGE_LABEL[confirmBack]}?`}
          confirmLabel="Go back"
          onClose={() => setConfirmBack(null)}
          onConfirm={async () => {
            await act(workflowApi.goBack(projectId, confirmBack));
            setConfirmBack(null);
          }}
        >
          <p>
            The stage reopens for review so you can edit, regenerate or re-approve it. Later stages that were already
            worked on will be marked <strong className="font-medium">Needs review</strong>.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
