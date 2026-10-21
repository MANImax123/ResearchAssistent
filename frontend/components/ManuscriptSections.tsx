"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, ErrorAlert, Spinner } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { SECTION_LABEL, sectionsApi, SOURCE_BADGE, type SectionSummary } from "@/lib/sections";

const STATUS: Record<SectionSummary["status"], { label: string; className: string }> = {
  empty: { label: "Not written", className: "bg-slate-100 text-slate-500" },
  draft: { label: "Draft", className: "bg-amber-100 text-amber-800" },
  approved: { label: "Approved", className: "bg-emerald-100 text-emerald-700" },
  stale: { label: "Needs review", className: "bg-orange-100 text-orange-800" },
};

/** The paper's sections in manuscript order, linking to the full-page editor. */
export function ManuscriptSections({ projectId, refreshKey }: { projectId: string; refreshKey?: unknown }) {
  const [sections, setSections] = useState<SectionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    sectionsApi
      .list(projectId)
      .then(setSections)
      .catch((err: Error) => setError(err.message));
  }, [projectId, refreshKey]);

  if (error) return <ErrorAlert>Could not load sections: {error}</ErrorAlert>;
  if (!sections) return <Spinner label="Loading sections" />;

  return (
    <ul aria-label="Manuscript sections" className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
      {sections.map((s) => {
        const status = STATUS[s.status];
        const source = s.current_version?.source;
        return (
          <li key={s.type}>
            <Link
              href={`/projects/${projectId}/sections/${s.type}`}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-slate-50 sm:px-5"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-slate-900">{SECTION_LABEL[s.type]}</span>
                <span className="block text-xs text-slate-500">
                  {s.current_version
                    ? `${s.word_count} words · v${s.current_version.number} · updated ${relativeTime(s.current_version.created_at)}`
                    : s.stage
                      ? "Written when its stage runs"
                      : "Not written yet"}
                </span>
              </span>
              <span className="flex items-center gap-2">
                {source && <Badge className={SOURCE_BADGE[source].className}>{SOURCE_BADGE[source].label}</Badge>}
                <Badge className={status.className}>{status.label}</Badge>
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
