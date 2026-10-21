"use client";

import { useState } from "react";

import { Badge, Button, ErrorAlert } from "@/components/ui";
import {
  literatureApi,
  PROFILE_LABEL,
  sectionAnchor,
  type Evidence,
  type PaperProfile,
  type ProfileListField,
  type ProfileTextField,
  type ProfileValue,
  type ProfileWrite,
} from "@/lib/literature";

const TEXT_FIELDS: ProfileTextField[] = ["problem", "method", "results"];
const LIST_FIELDS: ProfileListField[] = [
  "dataset",
  "metrics",
  "limitations",
  "tech_stack",
];
const FIELD =
  "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

/** Evidence chips: each opens the passage the value came from, with a link to its section. */
function EvidenceChips({
  evidence,
  label,
}: {
  evidence: Evidence[];
  label: string;
}) {
  const [open, setOpen] = useState<number | null>(null);
  if (evidence.length === 0) {
    return (
      <span className="text-xs text-amber-700">no evidence (added by you)</span>
    );
  }
  const shown = open !== null ? evidence[open] : null;
  return (
    <span className="inline">
      {evidence.map((e, i) => (
        <button
          key={e.chunk_id}
          type="button"
          aria-label={`Evidence ${i + 1} for ${label}: ${e.section ?? "passage"}${e.page ? `, page ${e.page}` : ""}`}
          aria-expanded={open === i}
          onClick={() => setOpen(open === i ? null : i)}
          className={`ml-1 rounded px-1 text-[11px] font-semibold ${
            open === i
              ? "bg-indigo-600 text-white"
              : "bg-indigo-100 text-indigo-800 hover:bg-indigo-200"
          }`}
        >
          {e.page ? `p.${e.page}` : `E${i + 1}`}
        </button>
      ))}
      {shown && (
        <span
          role="note"
          className="mt-2 block rounded-md border border-indigo-200 bg-indigo-50/60 p-2.5 text-xs"
        >
          <span className="block font-medium text-slate-700">
            {shown.section ?? "Passage"}
            {shown.page && ` · p. ${shown.page}`}
          </span>
          <span className="mt-1 block leading-relaxed text-slate-700">
            {shown.text}
          </span>
          {shown.section && shown.section !== "Abstract" && (
            <a
              href={`#${sectionAnchor(shown.section)}`}
              className="mt-1 inline-block font-medium text-indigo-700 hover:underline"
            >
              Go to this section
            </a>
          )}
        </span>
      )}
    </span>
  );
}

function toWrite(profile: PaperProfile): ProfileWrite {
  const list = (items: ProfileValue[]) =>
    items.map((i) => ({
      value: i.value,
      evidence: i.evidence.map((e) => e.chunk_id),
    }));
  return {
    problem: profile.problem?.value ?? null,
    method: profile.method?.value ?? null,
    results: profile.results?.value ?? null,
    dataset: list(profile.dataset),
    metrics: list(profile.metrics),
    limitations: list(profile.limitations),
    tech_stack: list(profile.tech_stack),
  };
}

/** The paper's structured profile (L6): every value with the passages it came from; the researcher corrects it. */
export function ProfilePanel({
  projectId,
  paperId,
  profile,
  canEdit,
  analysing,
  onAnalyse,
  onSaved,
}: {
  projectId: string;
  paperId: string;
  profile: PaperProfile | null;
  canEdit: boolean;
  analysing: boolean;
  onAnalyse: () => void;
  onSaved: (profile: PaperProfile) => void;
}) {
  const [draft, setDraft] = useState<ProfileWrite | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const clean: ProfileWrite = { ...draft };
      for (const f of TEXT_FIELDS) clean[f] = draft[f]?.trim() || null;
      for (const f of LIST_FIELDS)
        clean[f] = draft[f]
          .filter((i) => i.value.trim())
          .map((i) => ({ ...i, value: i.value.trim() }));
      onSaved(await literatureApi.saveProfile(projectId, paperId, clean));
      setDraft(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save the profile",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
      aria-labelledby="profile-heading"
      className="space-y-3 rounded-xl border border-slate-200 bg-white p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="profile-heading" className="text-base font-semibold">
            Paper profile
          </h2>
          <p className="text-sm text-slate-500">
            Extracted by the AI from this paper's passages only. Click a page
            chip to read the passage behind a value.
          </p>
        </div>
        {canEdit && !draft && (
          <div className="flex gap-2">
            {profile && (
              <Button
                variant="secondary"
                className="h-8 px-3 text-xs"
                onClick={() => setDraft(toWrite(profile))}
              >
                Edit
              </Button>
            )}
            <Button
              variant={profile ? "secondary" : "primary"}
              className="h-8 px-3 text-xs"
              onClick={onAnalyse}
              loading={analysing}
            >
              {profile ? "Analyse again" : "Analyse this paper"}
            </Button>
          </div>
        )}
      </div>
      {error && <ErrorAlert>{error}</ErrorAlert>}
      {profile && profile.edited_fields.length > 0 && !draft && (
        <p className="text-xs text-slate-500">
          You edited:{" "}
          {profile.edited_fields
            .map((f) => PROFILE_LABEL[f as ProfileTextField] ?? f)
            .join(", ")}
          . Analysing again replaces your edits.
        </p>
      )}

      {!profile && !draft && (
        <p className="text-sm text-slate-500">Not analysed yet.</p>
      )}

      {profile && !draft && (
        <dl className="space-y-3 text-sm">
          {TEXT_FIELDS.map((f) => (
            <div key={f}>
              <dt className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {PROFILE_LABEL[f]}
                {profile.edited_fields.includes(f) && (
                  <Badge className="ml-2 bg-slate-100 text-slate-600">
                    Edited
                  </Badge>
                )}
              </dt>
              <dd className="mt-0.5 text-slate-800">
                {profile[f] ? (
                  <>
                    {profile[f]!.value}
                    <EvidenceChips
                      evidence={profile[f]!.evidence}
                      label={PROFILE_LABEL[f]}
                    />
                  </>
                ) : (
                  <span className="text-slate-400">
                    Not stated in the paper
                  </span>
                )}
              </dd>
            </div>
          ))}
          {LIST_FIELDS.map((f) => (
            <div key={f}>
              <dt className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {PROFILE_LABEL[f]}
                {profile.edited_fields.includes(f) && (
                  <Badge className="ml-2 bg-slate-100 text-slate-600">
                    Edited
                  </Badge>
                )}
              </dt>
              <dd className="mt-0.5">
                {profile[f].length === 0 ? (
                  <span className="text-slate-400">None stated</span>
                ) : (
                  <ul className="space-y-1">
                    {profile[f].map((item) => (
                      <li key={item.value} className="text-slate-800">
                        {item.value}
                        <EvidenceChips
                          evidence={item.evidence}
                          label={`${PROFILE_LABEL[f]}: ${item.value}`}
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {draft && (
        <div className="space-y-4">
          {TEXT_FIELDS.map((f) => (
            <label key={f} className="block space-y-1 text-sm">
              <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {PROFILE_LABEL[f]}
              </span>
              <textarea
                className={FIELD}
                rows={3}
                value={draft[f] ?? ""}
                onChange={(e) => setDraft({ ...draft, [f]: e.target.value })}
                aria-label={PROFILE_LABEL[f]}
              />
            </label>
          ))}
          {LIST_FIELDS.map((f) => (
            <fieldset key={f} className="space-y-1.5 text-sm">
              <legend className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {PROFILE_LABEL[f]}
              </legend>
              {draft[f].map((item, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input
                    className={FIELD}
                    value={item.value}
                    aria-label={`${PROFILE_LABEL[f]} ${i + 1}`}
                    onChange={(e) => {
                      const next = [...draft[f]];
                      next[i] = { ...item, value: e.target.value };
                      setDraft({ ...draft, [f]: next });
                    }}
                  />
                  <span className="w-16 shrink-0 text-right text-[11px] text-slate-500">
                    {item.evidence.length
                      ? `${item.evidence.length} source${item.evidence.length > 1 ? "s" : ""}`
                      : "yours"}
                  </span>
                  <Button
                    variant="secondary"
                    className="h-8 px-2 text-xs"
                    aria-label={`Remove ${PROFILE_LABEL[f]} ${i + 1}`}
                    onClick={() =>
                      setDraft({
                        ...draft,
                        [f]: draft[f].filter((_, j) => j !== i),
                      })
                    }
                  >
                    Remove
                  </Button>
                </div>
              ))}
              <button
                type="button"
                className="text-xs font-medium text-indigo-700 hover:underline"
                onClick={() =>
                  setDraft({
                    ...draft,
                    [f]: [...draft[f], { value: "", evidence: [] }],
                  })
                }
              >
                + Add {PROFILE_LABEL[f].toLowerCase()}
              </button>
            </fieldset>
          ))}
          <div className="flex gap-2">
            <Button onClick={() => void save()} loading={saving}>
              Save profile
            </Button>
            <Button
              variant="secondary"
              onClick={() => setDraft(null)}
              disabled={saving}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
