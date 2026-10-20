import Link from "next/link";

import { Badge } from "@/components/ui";
import type { ProjectSummary } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { STAGE_LABEL, STAGES, venueLabel } from "@/lib/stages";

export function ProjectCard({ project }: { project: ProjectSummary }) {
  const stageNumber = STAGES.findIndex((s) => s.name === project.current_stage) + 1;
  const venue = venueLabel(project.target_venue);

  return (
    <Link
      href={`/projects/${project.id}`}
      className="group flex w-full flex-col rounded-xl border border-slate-200 bg-white p-5 shadow-xs transition hover:border-indigo-300 hover:shadow-md focus-visible:outline-2 focus-visible:outline-indigo-600"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="line-clamp-2 font-semibold text-slate-900 group-hover:text-indigo-700">{project.title}</h2>
        {project.role !== "owner" && <Badge className="bg-slate-100 text-slate-600">{project.role}</Badge>}
      </div>
      <p className="mt-1 text-sm text-slate-500">
        {[project.domain, venue].filter(Boolean).join(" · ") || "No domain set"}
      </p>
      <div className="mt-5 flex items-end justify-between gap-3 text-xs text-slate-500">
        <span>
          <span className="block text-slate-400">
            Stage {stageNumber} of {STAGES.length}
          </span>
          <span className="font-medium text-slate-700">{STAGE_LABEL[project.current_stage]}</span>
        </span>
        <time dateTime={project.updated_at}>Updated {relativeTime(project.updated_at)}</time>
      </div>
    </Link>
  );
}
