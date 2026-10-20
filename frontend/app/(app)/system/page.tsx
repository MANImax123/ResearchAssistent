"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { JobProgress } from "@/components/JobProgress";
import { Badge, Button, ErrorAlert, Spinner, TextField } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { jobsApi, TERMINAL, useJob, type Job } from "@/lib/jobs";

type Health = {
  status: "ok" | "degraded";
  dependencies: Record<string, { status: "ok" | "error"; latency_ms: number; detail: string | null }>;
};

const SERVICE_LABEL: Record<string, string> = {
  database: "PostgreSQL",
  redis: "Redis",
  minio: "MinIO storage",
  neo4j: "Neo4j",
  grobid: "GROBID",
};

function ServiceHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    apiFetch<Health>("/health")
      .then(setHealth)
      .catch((err: Error) => setError(err.message));
  }, []);
  useEffect(load, [load]);

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">Services</h2>
        <Button variant="secondary" className="h-8 px-3" onClick={load}>
          Refresh
        </Button>
      </div>
      {error && <ErrorAlert>Could not reach the API: {error}</ErrorAlert>}
      {!health && !error && <Spinner label="Checking services" />}
      {health && (
        <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {Object.entries(health.dependencies).map(([name, dep]) => (
            <li key={name} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm sm:px-5">
              <span className="font-medium text-slate-800">{SERVICE_LABEL[name] ?? name}</span>
              <span className="flex items-center gap-3">
                {dep.status === "error" && dep.detail && (
                  <span className="max-w-xs truncate text-xs text-red-600" title={dep.detail}>
                    {dep.detail}
                  </span>
                )}
                <span className="text-xs text-slate-400 tabular-nums">{Math.round(dep.latency_ms)} ms</span>
                <Badge
                  className={dep.status === "ok" ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}
                >
                  {dep.status === "ok" ? "OK" : "Error"}
                </Badge>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DemoJob() {
  const [started, setStarted] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const { job, error: pollError } = useJob(started?.id ?? null, started);
  const running = job !== null && !TERMINAL.has(job.status);

  async function start(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setError(null);
    setStarting(true);
    try {
      setStarted(
        await jobsApi.startDemo({
          steps: Number(form.get("steps")),
          delay_s: Number(form.get("delay_s")),
          fail_at: form.get("fail") ? Math.ceil(Number(form.get("steps")) / 2) : undefined,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start job");
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold">Background jobs</h2>
        <p className="mt-1 text-sm text-slate-500">
          Runs a demo job on the worker: it waits between steps and reports progress, like literature search or PDF
          parsing will.
        </p>
      </div>
      <div className="space-y-5 rounded-xl border border-slate-200 bg-white p-5">
        <form onSubmit={start} className="flex flex-wrap items-end gap-4">
          <div className="w-28">
            <TextField label="Steps" id="steps" name="steps" type="number" min={1} max={50} defaultValue={5} />
          </div>
          <div className="w-36">
            <TextField
              label="Seconds per step"
              id="delay_s"
              name="delay_s"
              type="number"
              min={0}
              max={10}
              step="any"
              defaultValue={1}
            />
          </div>
          <label className="flex h-10 items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" name="fail" className="size-4 rounded border-slate-300" />
            Fail halfway
          </label>
          <Button type="submit" loading={starting} disabled={running}>
            {running ? "Job running…" : "Start demo job"}
          </Button>
        </form>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        {/* key: a new job gets a fresh bar instead of animating down from the last one */}
        {job && <JobProgress key={job.id} job={job} label="Demo job" pollError={pollError} />}
      </div>
    </section>
  );
}

export default function SystemPage() {
  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">System</h1>
        <p className="mt-1 text-sm text-slate-500">Status of the services ResearchFlow depends on.</p>
      </div>
      <ServiceHealth />
      <DemoJob />
    </div>
  );
}
