"use client";

import { useState } from "react";

import { Badge, Button } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { CRITIC_LABEL, FIELD_LABEL, type CriticName, type CritiqueSet } from "@/lib/idea";

const SCORE_TONE = ["", "bg-red-500", "bg-orange-500", "bg-amber-500", "bg-lime-500", "bg-emerald-600"];

/** The three critiques side by side; the researcher picks which suggestions the revision applies. */
export function CritiquePanel({
  critiques,
  canAct,
  busy,
  onRequest,
  onApply,
}: {
  critiques: CritiqueSet | null;
  canAct: boolean;
  busy: boolean;
  onRequest: () => void;
  onApply: (suggestionIds: string[]) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const applied = new Set(critiques?.applied ?? []);

  const toggle = (id: string) => setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <section aria-labelledby="critiques-heading" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="critiques-heading" className="text-base font-semibold">
            Critiques
          </h2>
          <p className="text-sm text-slate-500">
            Three independent critics review the plan before the literature survey. You choose which suggestions to
            apply; nothing else in the plan changes.
          </p>
        </div>
        {canAct && (
          <Button variant={critiques ? "secondary" : "primary"} onClick={onRequest} disabled={busy}>
            {critiques ? "Ask the critics again" : "Ask the critics"}
          </Button>
        )}
      </div>

      {critiques && (
        <>
          <p className="text-xs text-slate-500">
            Round {critiques.round} · {relativeTime(critiques.generated_at)}
            {critiques.applied.length > 0 && ` · applied: ${critiques.applied.join(", ")}`}
          </p>
          <div className="grid gap-4 lg:grid-cols-3">
            {(Object.keys(CRITIC_LABEL) as CriticName[]).map((name) => {
              const c = critiques.critiques[name];
              if (!c) return null;
              return (
                <article key={name} aria-label={`${CRITIC_LABEL[name].title} critique`} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
                  <header>
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="font-semibold text-slate-900">{CRITIC_LABEL[name].title}</h3>
                      <span className="flex items-center gap-1" aria-label={`Score ${c.score} out of 5`}>
                        {[1, 2, 3, 4, 5].map((i) => (
                          <span
                            key={i}
                            aria-hidden
                            className={`size-2.5 rounded-full ${i <= c.score ? SCORE_TONE[c.score] : "bg-slate-200"}`}
                          />
                        ))}
                        <span className="ml-1 text-xs font-medium text-slate-600">{c.score}/5</span>
                      </span>
                    </div>
                    <p className="text-xs text-slate-500">{CRITIC_LABEL[name].question}</p>
                  </header>
                  <p className="text-sm text-slate-700">{c.summary}</p>
                  <PointList title="Strengths" items={c.strengths} tone="text-emerald-700" mark="✓" />
                  <PointList title="Risks" items={c.risks} tone="text-amber-700" mark="!" />
                  <div className="space-y-2">
                    <h4 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Suggestions</h4>
                    {c.suggestions.map((s) => {
                      const done = applied.has(s.id);
                      return (
                        <label
                          key={s.id}
                          className={`block rounded-lg border p-2.5 text-sm ${
                            done ? "border-emerald-200 bg-emerald-50/50" : selected.includes(s.id) ? "border-indigo-300 bg-indigo-50/50" : "border-slate-200"
                          }`}
                        >
                          <span className="flex items-start gap-2">
                            {canAct && !done && (
                              <input
                                type="checkbox"
                                className="mt-1"
                                checked={selected.includes(s.id)}
                                onChange={() => toggle(s.id)}
                                aria-label={`Apply ${s.id}: ${s.title}`}
                              />
                            )}
                            <span className="min-w-0">
                              <span className="font-medium text-slate-900">
                                {s.id}. {s.title}
                              </span>
                              {done && <Badge className="ml-2 bg-emerald-100 text-emerald-700">Applied</Badge>}
                              <span className="mt-0.5 block text-slate-600">{s.detail}</span>
                              <span className="mt-1 flex flex-wrap gap-1">
                                {s.affects.map((f) => (
                                  <Badge key={f} className="bg-slate-100 text-slate-600">
                                    {FIELD_LABEL[f] ?? f}
                                  </Badge>
                                ))}
                              </span>
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </article>
              );
            })}
          </div>
          {canAct && (
            <div className="flex flex-wrap items-center gap-3">
              <Button
                onClick={() => {
                  onApply(selected);
                  setSelected([]);
                }}
                disabled={busy || selected.length === 0}
              >
                Apply {selected.length || ""} selected suggestion{selected.length === 1 ? "" : "s"}
              </Button>
              <span className="text-xs text-slate-500">
                Only the plan parts named on the selected suggestions are revised.
              </span>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function PointList({ title, items, tone, mark }: { title: string; items: string[]; tone: string; mark: string }) {
  return (
    <div>
      <h4 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{title}</h4>
      <ul className="mt-1 space-y-1 text-sm text-slate-700">
        {items.map((item, i) => (
          <li key={`${i}-${item}`} className="flex gap-2">
            <span aria-hidden className={`font-semibold ${tone}`}>
              {mark}
            </span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
