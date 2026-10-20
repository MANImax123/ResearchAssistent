import { Badge } from "@/components/ui";
import type { Job, JobStatus } from "@/lib/jobs";

const STATUS: Record<JobStatus, { label: string; badge: string; bar: string }> = {
  pending: { label: "Queued", badge: "bg-slate-100 text-slate-600", bar: "bg-slate-400" },
  running: { label: "Running", badge: "bg-blue-100 text-blue-700", bar: "bg-indigo-600" },
  success: { label: "Completed", badge: "bg-emerald-100 text-emerald-700", bar: "bg-emerald-600" },
  failed: { label: "Failed", badge: "bg-red-100 text-red-700", bar: "bg-red-500" },
  cancelled: { label: "Cancelled", badge: "bg-slate-100 text-slate-600", bar: "bg-slate-400" },
};

export function JobProgress({ job, label, pollError }: { job: Job; label?: string; pollError?: string | null }) {
  const meta = STATUS[job.status];
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="font-medium text-slate-800">{label ?? job.type}</span>
        <span className="flex items-center gap-2">
          <span className="text-slate-500 tabular-nums">{job.progress}%</span>
          <Badge className={meta.badge}>{meta.label}</Badge>
        </span>
      </div>
      <div
        className={`h-2 overflow-hidden rounded-full ${job.status === "pending" ? "animate-pulse bg-slate-200" : "bg-slate-100"}`}
        role="progressbar"
        aria-valuenow={job.progress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ?? job.type}
      >
        {/* Queued: the pulsing empty track says "waiting"; width only ever animates upward from 0 */}
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${meta.bar}`}
          style={{ width: `${job.status === "pending" ? 0 : Math.max(2, job.progress)}%` }}
        />
      </div>
      {job.status === "failed" && job.error ? (
        <p role="alert" className="text-xs text-red-600">
          {job.error}
        </p>
      ) : (
        job.message && <p className="text-xs text-slate-500">{job.message}</p>
      )}
      {pollError && <p className="text-xs text-amber-700">Connection problem, retrying… ({pollError})</p>}
    </div>
  );
}
