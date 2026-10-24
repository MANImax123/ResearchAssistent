"use client";

import { useState } from "react";

import { Badge, Button } from "@/components/ui";
import type { FollowUpQuestion, MissingItem } from "@/lib/methodology";

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none";

function Question({
  q,
  canEdit,
  onUpdate,
}: {
  q: FollowUpQuestion;
  canEdit: boolean;
  onUpdate: (
    status: FollowUpQuestion["status"],
    answer?: string,
  ) => Promise<boolean>;
}) {
  const [answer, setAnswer] = useState(q.answer ?? "");
  const answered = q.status === "answered";
  return (
    <li
      aria-label={`Question about ${q.label}`}
      className={`space-y-2 rounded-lg border p-3 ${answered ? "border-emerald-200 bg-emerald-50/40" : "border-slate-200 bg-white"}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={`#field-${q.field}`}
          className="text-xs font-semibold tracking-wide text-indigo-700 uppercase hover:underline"
        >
          {q.label}
        </a>
        {q.kind === "unclear" && (
          <Badge className="bg-amber-50 text-amber-700">Unclear</Badge>
        )}
        {answered && (
          <Badge className="bg-emerald-100 text-emerald-800">Answered</Badge>
        )}
      </div>
      <p className="text-sm font-medium text-slate-900">{q.question}</p>
      {q.reason && <p className="text-xs text-slate-500">{q.reason}</p>}
      {answered ? (
        <>
          <p className="text-sm whitespace-pre-wrap text-slate-800">
            {q.answer}
          </p>
          {canEdit && (
            <Button
              variant="secondary"
              className="h-8"
              onClick={() => void onUpdate("open")}
            >
              Change answer
            </Button>
          )}
        </>
      ) : (
        canEdit && (
          <>
            <textarea
              aria-label={`Answer: ${q.question}`}
              className={INPUT}
              rows={2}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="Your answer (it is added to the methodology input)"
            />
            <div className="flex gap-2">
              <Button
                className="h-8"
                disabled={!answer.trim()}
                onClick={() => void onUpdate("answered", answer)}
              >
                Save answer
              </Button>
              <Button
                variant="secondary"
                className="h-8"
                onClick={() => void onUpdate("dismissed")}
              >
                Not relevant
              </Button>
            </div>
          </>
        )
      )}
    </li>
  );
}

/** What the methodology is missing (checked in code) and the agent's follow-up questions about it. */
export function FollowUpPanel({
  missing,
  questions,
  canEdit,
  busy,
  unsaved,
  onAsk,
  onUpdate,
}: {
  missing: MissingItem[];
  questions: FollowUpQuestion[];
  canEdit: boolean;
  busy: boolean;
  unsaved: boolean;
  onAsk: () => void;
  onUpdate: (
    q: FollowUpQuestion,
    status: FollowUpQuestion["status"],
    answer?: string,
  ) => Promise<boolean>;
}) {
  const essentials = missing.filter((m) => m.essential);
  const open = questions.filter((q) => q.status === "open");
  const answered = questions.filter((q) => q.status === "answered");
  return (
    <div className="space-y-4">
      <section
        aria-labelledby="missing-heading"
        className="space-y-2 rounded-xl border border-slate-200 bg-white p-4"
      >
        <h2 id="missing-heading" className="text-sm font-semibold">
          What's still missing
        </h2>
        {missing.length === 0 ? (
          <p className="text-sm text-emerald-700">
            Every part of the methodology is filled in.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {missing.map((m, i) => (
              <li key={`${m.field}-${i}`} className="flex gap-2">
                <span
                  className={m.essential ? "text-amber-600" : "text-slate-400"}
                  aria-hidden
                >
                  ●
                </span>
                <span>
                  <a
                    href={`#field-${m.field}`}
                    className="font-medium text-slate-800 hover:underline"
                  >
                    {m.label}
                  </a>
                  {m.essential && (
                    <span className="text-xs text-amber-700"> (essential)</span>
                  )}
                  <span className="block text-xs text-slate-500">
                    {m.reason}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {essentials.length > 0 && (
          <p className="text-xs text-slate-500">
            The Methodology section can't be written faithfully without the
            essentials.
          </p>
        )}
      </section>

      <section
        aria-labelledby="questions-heading"
        className="space-y-3 rounded-xl border border-slate-200 bg-white p-4"
      >
        <div>
          <h2 id="questions-heading" className="text-sm font-semibold">
            Follow-up questions
          </h2>
          <p className="text-xs text-slate-500">
            The agent reads your input, notes and code and asks about what's
            missing or too vague to reproduce. It never fills in values for you.
          </p>
        </div>
        {canEdit && (
          <div className="space-y-1">
            <Button
              variant="secondary"
              onClick={onAsk}
              disabled={busy || unsaved}
            >
              {questions.length ? "Ask again" : "Ask what's missing"}
            </Button>
            {unsaved && (
              <p className="text-xs text-slate-500">Save your changes first.</p>
            )}
          </div>
        )}
        {questions.length === 0 ? (
          <p className="text-sm text-slate-500">No questions yet.</p>
        ) : (
          <ul className="space-y-2">
            {[...open, ...answered].map((q) => (
              <Question
                key={`${q.id}-${q.status}`}
                q={q}
                canEdit={canEdit}
                onUpdate={(s, a) => onUpdate(q, s, a)}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
