"use client";

import { useState, type ReactNode } from "react";

import { Badge, Button, ErrorAlert, TextArea, TextField } from "@/components/ui";
import { nextId, planProblems, type IdeaPlan, type ResearchQuestion } from "@/lib/idea";

type Save = (plan: IdeaPlan) => Promise<void>;

/** The research plan as cards; each card edits its part, and saving sends the whole (validated) plan. */
export function PlanCards({ plan, editable, onSave }: { plan: IdeaPlan; editable: boolean; onSave: Save }) {
  return (
    <div className="space-y-4">
      <TitleCard plan={plan} editable={editable} onSave={onSave} />
      <TextCard label="Refined idea" field="refined_idea" plan={plan} editable={editable} onSave={onSave} />
      <DomainCard plan={plan} editable={editable} onSave={onSave} />
      <div className="grid gap-4 lg:grid-cols-2">
        <TextCard label="Problem statement" field="problem_statement" plan={plan} editable={editable} onSave={onSave} />
        <TextCard label="Motivation" field="motivation" plan={plan} editable={editable} onSave={onSave} />
      </div>
      <TextCard label="Scope" field="scope" plan={plan} editable={editable} onSave={onSave} />
      <ObjectivesCard plan={plan} editable={editable} onSave={onSave} />
      <QuestionsCard plan={plan} editable={editable} onSave={onSave} />
      <div className="grid gap-4 lg:grid-cols-2">
        <ListCard label="Expected contributions" field="expected_contributions" plan={plan} editable={editable} onSave={onSave} />
        <ListCard label="Subtopics" field="subtopics" plan={plan} editable={editable} onSave={onSave} />
      </div>
      <ListCard
        label="Possible research directions"
        field="research_directions"
        plan={plan}
        editable={editable}
        onSave={onSave}
        note="Suggestions to explore, not established findings."
      />
    </div>
  );
}

// ─── Card shell ───────────────────────────────────────────────────────

function Card({
  title,
  note,
  editable,
  editing,
  onEdit,
  onCancel,
  onSave,
  problems,
  children,
}: {
  title: string;
  note?: string;
  editable: boolean;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: () => Promise<void>;
  problems?: string[];
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label={title} className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          {note && <p className="text-xs text-slate-500">{note}</p>}
        </div>
        {editable && !editing && (
          <Button variant="secondary" className="h-8 px-3" onClick={onEdit} aria-label={`Edit ${title}`}>
            Edit
          </Button>
        )}
      </div>
      {children}
      {editing && (
        <div className="mt-3 space-y-2">
          {problems && problems.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-amber-800" aria-label="Problems to fix">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {error && <ErrorAlert>{error}</ErrorAlert>}
          <div className="flex gap-2">
            <Button className="h-8 px-3" onClick={() => void save()} loading={busy} disabled={Boolean(problems?.length)}>
              Save
            </Button>
            <Button variant="secondary" className="h-8 px-3" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function useCardEdit<T>(initial: () => T) {
  const [draft, setDraft] = useState<T | null>(null);
  return {
    editing: draft !== null,
    draft,
    setDraft,
    start: () => setDraft(initial()),
    cancel: () => setDraft(null),
  };
}

// ─── Cards ─────────────────────────────────────────────────────────────

type TextField_ = "refined_idea" | "problem_statement" | "motivation" | "scope";
type ListField = "expected_contributions" | "subtopics" | "research_directions";

function TextCard({ label, field, plan, editable, onSave }: { label: string; field: TextField_; plan: IdeaPlan; editable: boolean; onSave: Save }) {
  const edit = useCardEdit(() => plan[field]);
  return (
    <Card
      title={label}
      editable={editable}
      editing={edit.editing}
      onEdit={edit.start}
      onCancel={edit.cancel}
      onSave={async () => {
        await onSave({ ...plan, [field]: edit.draft ?? "" });
        edit.cancel();
      }}
      problems={edit.editing && !edit.draft?.trim() ? ["Can't be empty"] : []}
    >
      {edit.editing ? (
        <TextArea label={label} id={`edit-${field}`} rows={5} value={edit.draft ?? ""} onChange={(e) => edit.setDraft(e.target.value)} />
      ) : (
        <p className="text-sm whitespace-pre-wrap text-slate-700">{plan[field]}</p>
      )}
    </Card>
  );
}

function ListCard({
  label,
  field,
  plan,
  editable,
  onSave,
  note,
}: {
  label: string;
  field: ListField;
  plan: IdeaPlan;
  editable: boolean;
  onSave: Save;
  note?: string;
}) {
  const edit = useCardEdit(() => plan[field].join("\n"));
  const items = (edit.draft ?? "").split("\n").map((s) => s.trim()).filter(Boolean);
  return (
    <Card
      title={label}
      note={note}
      editable={editable}
      editing={edit.editing}
      onEdit={edit.start}
      onCancel={edit.cancel}
      onSave={async () => {
        await onSave({ ...plan, [field]: items });
        edit.cancel();
      }}
      problems={edit.editing && items.length < 2 ? ["Keep at least 2 items"] : []}
    >
      {edit.editing ? (
        <TextArea
          label={`${label} (one per line)`}
          id={`edit-${field}`}
          rows={5}
          value={edit.draft ?? ""}
          onChange={(e) => edit.setDraft(e.target.value)}
        />
      ) : (
        <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
          {plan[field].map((item, i) => (
            <li key={`${i}-${item}`}>{item}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DomainCard({ plan, editable, onSave }: { plan: IdeaPlan; editable: boolean; onSave: Save }) {
  const edit = useCardEdit(() => ({ domain: plan.domain, keywords: plan.keywords.join(", ") }));
  const keywords = (edit.draft?.keywords ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  return (
    <Card
      title="Domain & keywords"
      editable={editable}
      editing={edit.editing}
      onEdit={edit.start}
      onCancel={edit.cancel}
      onSave={async () => {
        await onSave({ ...plan, domain: edit.draft?.domain.trim() ?? plan.domain, keywords });
        edit.cancel();
      }}
      problems={edit.editing && keywords.length < 3 ? ["Give at least 3 keywords"] : []}
    >
      {edit.editing && edit.draft ? (
        <div className="space-y-3">
          <TextField label="Domain" id="edit-domain" value={edit.draft.domain} onChange={(e) => edit.setDraft({ ...edit.draft!, domain: e.target.value })} />
          <TextField
            label="Keywords (comma-separated)"
            id="edit-keywords"
            value={edit.draft.keywords}
            onChange={(e) => edit.setDraft({ ...edit.draft!, keywords: e.target.value })}
          />
        </div>
      ) : (
        <div className="space-y-2 text-sm text-slate-700">
          <p>{plan.domain}</p>
          <div className="flex flex-wrap gap-1.5">
            {plan.keywords.map((k, i) => (
              <Badge key={`${i}-${k}`} className="bg-indigo-100 text-indigo-700">
                {k}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function TitleCard({ plan, editable, onSave }: { plan: IdeaPlan; editable: boolean; onSave: Save }) {
  const current = plan.title ?? plan.title_options[0];
  const edit = useCardEdit(() => current);
  const custom = edit.draft !== null && !plan.title_options.includes(edit.draft);
  return (
    <Card
      title="Title"
      note="Pick one of the suggestions or write your own."
      editable={editable}
      editing={edit.editing}
      onEdit={edit.start}
      onCancel={edit.cancel}
      onSave={async () => {
        await onSave({ ...plan, title: edit.draft?.trim() || current });
        edit.cancel();
      }}
      problems={edit.editing && (edit.draft ?? "").trim().length < 3 ? ["Title is too short"] : []}
    >
      {edit.editing ? (
        <fieldset className="space-y-2">
          <legend className="sr-only">Choose a title</legend>
          {plan.title_options.map((t) => (
            <label key={t} className="flex items-start gap-2 text-sm text-slate-800">
              <input type="radio" name="title" className="mt-1" checked={edit.draft === t} onChange={() => edit.setDraft(t)} />
              {t}
            </label>
          ))}
          <label className="flex items-start gap-2 text-sm text-slate-800">
            <input type="radio" name="title" className="mt-2.5" checked={custom} onChange={() => edit.setDraft("")} />
            <input
              aria-label="Your own title"
              placeholder="Your own title"
              value={custom ? (edit.draft ?? "") : ""}
              onFocus={() => custom || edit.setDraft("")}
              onChange={(e) => edit.setDraft(e.target.value)}
              className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </label>
        </fieldset>
      ) : (
        <div className="space-y-2">
          <p className="text-lg font-semibold text-slate-900">{current}</p>
          {!plan.title_options.includes(current) && <Badge className="bg-emerald-100 text-emerald-700">Your title</Badge>}
        </div>
      )}
    </Card>
  );
}

function ObjectivesCard({ plan, editable, onSave }: { plan: IdeaPlan; editable: boolean; onSave: Save }) {
  const edit = useCardEdit(() => plan.objectives.map((o) => ({ ...o })));
  const objectives = edit.draft ?? plan.objectives;
  const problems = edit.editing ? planProblems({ ...plan, objectives }).filter((p) => !p.startsWith("Keep at least 2")) : [];
  return (
    <Card
      title="Objectives"
      note="Each objective is served by at least one research question."
      editable={editable}
      editing={edit.editing}
      onEdit={edit.start}
      onCancel={edit.cancel}
      onSave={async () => {
        await onSave({ ...plan, objectives });
        edit.cancel();
      }}
      problems={[...problems, ...(objectives.some((o) => !o.text.trim()) ? ["Objectives can't be empty"] : [])]}
    >
      <ol className="space-y-2">
        {objectives.map((o, i) => (
          <li key={o.id} className="flex items-start gap-3 text-sm">
            <Badge className="mt-1 bg-slate-100 text-slate-700">{o.id}</Badge>
            {edit.editing ? (
              <>
                <textarea
                  aria-label={`Objective ${o.id}`}
                  rows={2}
                  value={o.text}
                  onChange={(e) => edit.setDraft(objectives.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
                  className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none"
                />
                <Button
                  variant="danger-outline"
                  className="h-8 px-2"
                  aria-label={`Remove ${o.id}`}
                  onClick={() => edit.setDraft(objectives.filter((_, j) => j !== i))}
                >
                  ✕
                </Button>
              </>
            ) : (
              <span className="text-slate-700">{o.text}</span>
            )}
          </li>
        ))}
      </ol>
      {edit.editing && objectives.length < 6 && (
        <Button
          variant="secondary"
          className="mt-2 h-8 px-3"
          onClick={() => edit.setDraft([...objectives, { id: nextId("O", objectives), text: "" }])}
        >
          + Add objective
        </Button>
      )}
    </Card>
  );
}

function QuestionsCard({ plan, editable, onSave }: { plan: IdeaPlan; editable: boolean; onSave: Save }) {
  const edit = useCardEdit(() => plan.research_questions.map((q) => ({ ...q, linked_objective_ids: [...q.linked_objective_ids] })));
  const questions = edit.draft ?? plan.research_questions;
  const set = (i: number, patch: Partial<ResearchQuestion>) =>
    edit.setDraft(questions.map((q, j) => (j === i ? { ...q, ...patch } : q)));
  const problems = edit.editing ? planProblems({ ...plan, research_questions: questions }) : [];
  return (
    <Card
      title="Research questions & hypotheses"
      note="Each one serves at least one objective."
      editable={editable}
      editing={edit.editing}
      onEdit={edit.start}
      onCancel={edit.cancel}
      onSave={async () => {
        await onSave({ ...plan, research_questions: questions });
        edit.cancel();
      }}
      problems={[...problems, ...(questions.some((q) => !q.text.trim()) ? ["Questions can't be empty"] : [])]}
    >
      <ol className="space-y-3">
        {questions.map((q, i) => (
          <li key={q.id} className="space-y-1.5 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className="bg-slate-100 text-slate-700">{q.id}</Badge>
              {edit.editing ? (
                <select
                  aria-label={`${q.id} type`}
                  value={q.kind}
                  onChange={(e) => set(i, { kind: e.target.value as ResearchQuestion["kind"] })}
                  className="h-7 rounded border border-slate-300 px-1 text-xs"
                >
                  <option value="question">Question</option>
                  <option value="hypothesis">Hypothesis</option>
                </select>
              ) : (
                <Badge className={q.kind === "hypothesis" ? "bg-amber-100 text-amber-800" : "bg-sky-100 text-sky-700"}>
                  {q.kind}
                </Badge>
              )}
              <span className="text-xs text-slate-500">serves</span>
              {edit.editing
                ? plan.objectives.map((o) => (
                    <label key={o.id} className="flex items-center gap-1 text-xs">
                      <input
                        type="checkbox"
                        aria-label={`${q.id} serves ${o.id}`}
                        checked={q.linked_objective_ids.includes(o.id)}
                        onChange={(e) =>
                          set(i, {
                            linked_objective_ids: e.target.checked
                              ? [...q.linked_objective_ids, o.id]
                              : q.linked_objective_ids.filter((x) => x !== o.id),
                          })
                        }
                      />
                      {o.id}
                    </label>
                  ))
                : q.linked_objective_ids.map((id) => (
                    <Badge key={id} className="bg-indigo-50 text-indigo-700">
                      {id}
                    </Badge>
                  ))}
              {edit.editing && (
                <Button
                  variant="danger-outline"
                  className="ml-auto h-7 px-2"
                  aria-label={`Remove ${q.id}`}
                  onClick={() => edit.setDraft(questions.filter((_, j) => j !== i))}
                >
                  ✕
                </Button>
              )}
            </div>
            {edit.editing ? (
              <textarea
                aria-label={`${q.id} text`}
                rows={2}
                value={q.text}
                onChange={(e) => set(i, { text: e.target.value })}
                className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none"
              />
            ) : (
              <p className="text-slate-700">{q.text}</p>
            )}
          </li>
        ))}
      </ol>
      {edit.editing && questions.length < 8 && (
        <Button
          variant="secondary"
          className="mt-2 h-8 px-3"
          onClick={() =>
            edit.setDraft([...questions, { id: nextId("RQ", questions), text: "", kind: "question", linked_objective_ids: [] }])
          }
        >
          + Add question
        </Button>
      )}
    </Card>
  );
}
