"use client";

import { SectionEditor } from "@/components/editor/SectionEditor";
import { Badge, Button } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { AbstractInfo } from "@/lib/idea";

/** The working abstract: drafted from the plan, edited in place, approved with the plan. */
export function AbstractPanel({
  projectId,
  abstract,
  venue,
  canAct,
  busy,
  onDraft,
  onChanged,
}: {
  projectId: string;
  abstract: AbstractInfo;
  venue: string | null;
  canAct: boolean;
  busy: boolean;
  onDraft: () => void;
  onChanged: () => void;
}) {
  const { content, word_count: words, min_words: min, max_words: max } = abstract;
  const inRange = words >= min && words <= max;

  return (
    <section aria-labelledby="abstract-heading" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="abstract-heading" className="text-base font-semibold">
            Working abstract
          </h2>
          <p className="text-sm text-slate-500">
            An early abstract written from the plan. It's approved together with the plan and finalised from your
            results later.
          </p>
        </div>
        {canAct && (
          <Button variant={content ? "secondary" : "primary"} onClick={onDraft} disabled={busy}>
            {content ? "Redraft abstract" : "Draft abstract"}
          </Button>
        )}
      </div>

      <p className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>
          Target {min}–{max} words{venue ? ` (${venue})` : ""}
        </span>
        {content && (
          <Badge className={inRange ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-800"}>
            {words} words{inRange ? "" : words < min ? " · too short" : " · too long"}
          </Badge>
        )}
        {abstract.edited && <Badge className="bg-slate-100 text-slate-600">Edited by you</Badge>}
        {abstract.generated_at && <span>Drafted {relativeTime(abstract.generated_at)}</span>}
      </p>

      {abstract.outdated && (
        <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          The plan changed after this abstract was drafted. Check it still matches, or redraft it.
        </p>
      )}

      {content !== null ? (
        <SectionEditor projectId={projectId} type="abstract" compact onChanged={onChanged} />
      ) : (
        <p className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">
          No abstract yet.{canAct ? " Draft one once you're happy with the plan." : ""}
        </p>
      )}
    </section>
  );
}
