"use client";

import { useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";

export type JobStatus = "pending" | "running" | "success" | "failed" | "cancelled";

export type Job = {
  id: string;
  project_id: string | null;
  type: string;
  status: JobStatus;
  progress: number;
  message: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export const TERMINAL: ReadonlySet<JobStatus> = new Set(["success", "failed", "cancelled"]);

export const jobsApi = {
  get: (id: string) => apiFetch<Job>(`/jobs/${encodeURIComponent(id)}`),
  list: (projectId?: string) => apiFetch<Job[]>(projectId ? `/jobs?project_id=${encodeURIComponent(projectId)}` : "/jobs"),
  startDemo: (body: { steps?: number; delay_s?: number; fail_at?: number; project_id?: string }) =>
    apiFetch<Job>("/jobs/demo", { method: "POST", json: body }),
};

const POLL_MS = 1000;
const MAX_POLL_MS = 5000;

/**
 * Polls a job until it reaches a terminal status.
 * Pass the job returned when it was started as `initial` to render immediately.
 * Network errors back off (up to 5 s) and keep polling; the last error is exposed.
 */
export function useJob(jobId: string | null, initial?: Job | null): { job: Job | null; error: string | null } {
  const [job, setJob] = useState<Job | null>(initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const delay = useRef(POLL_MS);

  useEffect(() => {
    setJob(initial ?? null);
    setError(null);
    delay.current = POLL_MS;
    if (!jobId) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        const next = await jobsApi.get(jobId);
        if (cancelled) return;
        setJob(next);
        setError(null);
        delay.current = POLL_MS;
        if (TERMINAL.has(next.status)) return;
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not fetch job status");
        delay.current = Math.min(delay.current * 2, MAX_POLL_MS);
      }
      timer = setTimeout(poll, delay.current);
    };

    if (!initial || !TERMINAL.has(initial.status)) timer = setTimeout(poll, initial ? delay.current : 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // `initial` only seeds the state for a new job id, so it's deliberately not a dependency
  }, [jobId]);

  return { job, error };
}
