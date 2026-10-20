import { Badge } from "@/components/ui";
import type { ProjectDetail } from "@/lib/api";
import { STAGES, STATUS_META, type StageName } from "@/lib/stages";

export function StageTracker({ stages, current }: { stages: ProjectDetail["stages"]; current: StageName }) {
  const statusOf = new Map(stages.map((s) => [s.name, s.status]));

  return (
    <ol className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
      {STAGES.map((stage, i) => {
        const status = statusOf.get(stage.name) ?? "not_started";
        const meta = STATUS_META[status];
        const isCurrent = stage.name === current;
        return (
          <li
            key={stage.name}
            aria-current={isCurrent ? "step" : undefined}
            className={`flex items-center gap-4 px-4 py-3 sm:px-5 ${isCurrent ? "bg-indigo-50/60" : ""}`}
          >
            <span
              className={`flex size-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
                status === "approved"
                  ? "bg-emerald-600 text-white"
                  : isCurrent
                    ? "bg-indigo-600 text-white"
                    : "bg-slate-100 text-slate-500"
              }`}
              aria-hidden
            >
              {status === "approved" ? "✓" : i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-slate-900">
                {stage.label}
                {isCurrent && <span className="ml-2 text-xs font-normal text-indigo-600">Current stage</span>}
              </p>
              <p className="text-xs text-slate-500 sm:truncate">{stage.description}</p>
            </div>
            <Badge className={meta.className}>{meta.label}</Badge>
          </li>
        );
      })}
    </ol>
  );
}
