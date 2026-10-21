import type { ReactNode } from "react";

import { Badge } from "@/components/ui";
import { STAGE_LABEL, STAGES, STATUS_META, type StageName, type StageStatus } from "@/lib/stages";

type TrackerStage = { name: StageName; status: StageStatus; blocked_by?: StageName[] };

export function StageTracker({
  stages,
  current,
  actions,
}: {
  stages: TrackerStage[];
  current: StageName;
  /** Buttons shown at the end of a row (e.g. Run, Go back). */
  actions?: (stage: StageName) => ReactNode;
}) {
  const byName = new Map(stages.map((s) => [s.name, s]));

  return (
    <ol className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
      {STAGES.map((stage, i) => {
        const info = byName.get(stage.name);
        const status = info?.status ?? "not_started";
        const meta = STATUS_META[status];
        const isCurrent = stage.name === current;
        const waitingOn = status === "not_started" ? (info?.blocked_by ?? []) : [];
        return (
          <li
            key={stage.name}
            aria-current={isCurrent ? "step" : undefined}
            className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-5 ${isCurrent ? "bg-indigo-50/60" : ""}`}
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
              <p className="text-xs text-slate-500 sm:truncate">
                {waitingOn.length > 0
                  ? `Needs ${waitingOn.map((s) => STAGE_LABEL[s]).join(" and ")} approved`
                  : stage.description}
              </p>
            </div>
            {/* On narrow screens the buttons take their own line, aligned with the text */}
            <div className="flex w-full flex-wrap items-center gap-2 pl-12 sm:w-auto sm:pl-0">
              {actions?.(stage.name)}
              <Badge className={meta.className}>{meta.label}</Badge>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
