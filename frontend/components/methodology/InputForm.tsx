"use client";

import { Button } from "@/components/ui";
import {
  EMPTY_ROW,
  type FieldName,
  type ListField,
  type MethodologyInput,
  type TextField,
} from "@/lib/methodology";

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:bg-slate-50";

const TEXT_FIELDS: { name: TextField; label: string; hint: string; rows: number }[] = [
  { name: "architecture", label: "System architecture", hint: "Components and how they connect (or upload a diagram).", rows: 4 },
  { name: "algorithms", label: "Algorithms / model", hint: "The model or algorithm, its layers or steps, loss, training procedure.", rows: 5 },
  { name: "workflow", label: "Workflow", hint: "The end-to-end pipeline, from raw data to evaluation.", rows: 4 },
  { name: "preprocessing", label: "Preprocessing", hint: "Cleaning, filtering, normalisation, featurisation.", rows: 4 },
  { name: "experimental_setup", label: "Experimental setup", hint: "Train/validation/test splits, baselines, hardware, number of runs.", rows: 5 },
  { name: "reproducibility", label: "Reproducibility", hint: "Code and data availability, random seeds, environment.", rows: 3 },
]; // prettier-ignore

type Column<K extends ListField> = {
  key: keyof MethodologyInput[K][number];
  label: string;
  wide?: boolean;
};

const LIST_FIELDS: {
  [K in ListField]: { label: string; hint: string; columns: Column<K>[] };
} = {
  datasets: {
    label: "Datasets",
    hint: "Name, where it comes from, its size and the features you use.",
    columns: [
      { key: "name", label: "Name" },
      { key: "source", label: "Source" },
      { key: "size", label: "Size" },
      { key: "features", label: "Features", wide: true },
    ],
  },
  hyperparameters: {
    label: "Hyperparameters",
    hint: "Name and value of each setting you tuned or fixed.",
    columns: [
      { key: "name", label: "Name" },
      { key: "value", label: "Value" },
      { key: "notes", label: "Notes", wide: true },
    ],
  },
  tools: {
    label: "Tools, frameworks and libraries",
    hint: "With versions where they matter.",
    columns: [
      { key: "name", label: "Name" },
      { key: "version", label: "Version" },
      { key: "purpose", label: "Used for", wide: true },
    ],
  },
  metrics: {
    label: "Evaluation metrics",
    hint: "What you report, and why if it isn't obvious.",
    columns: [
      { key: "name", label: "Metric" },
      { key: "notes", label: "Notes", wide: true },
    ],
  },
};

const ORDER: (TextField | ListField)[] = [
  "architecture", "algorithms", "workflow", "datasets", "preprocessing", "experimental_setup", "hyperparameters",
  "tools", "metrics", "reproducibility",
]; // prettier-ignore

function Flag({ missing }: { missing: boolean }) {
  return missing ? (
    <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700">
      Missing
    </span>
  ) : null;
}

function ListEditor<K extends ListField>({
  field,
  rows,
  disabled,
  onChange,
}: {
  field: K;
  rows: MethodologyInput[K];
  disabled: boolean;
  onChange: (rows: MethodologyInput[K]) => void;
}) {
  const spec = LIST_FIELDS[field] as { label: string; columns: Column<K>[] };
  type Row = MethodologyInput[K][number];
  const update = (i: number, key: keyof Row, value: string) =>
    onChange(
      rows.map((r, j) =>
        j === i ? { ...r, [key]: value } : r,
      ) as MethodologyInput[K],
    );
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex flex-wrap items-start gap-2">
          {spec.columns.map((c) => (
            <input
              key={String(c.key)}
              aria-label={`${spec.label} ${i + 1}: ${c.label}`}
              placeholder={c.label}
              className={`${INPUT} ${c.wide ? "min-w-48 flex-[2]" : "min-w-32 flex-1"}`}
              value={String((row as Row)[c.key as keyof Row] ?? "")}
              disabled={disabled}
              onChange={(e) => update(i, c.key as keyof Row, e.target.value)}
            />
          ))}
          {!disabled && (
            <button
              type="button"
              aria-label={`Remove ${spec.label.toLowerCase()} row ${i + 1}`}
              className="mt-2 text-slate-400 hover:text-red-600"
              onClick={() =>
                onChange(rows.filter((_, j) => j !== i) as MethodologyInput[K])
              }
            >
              ×
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <Button
          type="button"
          variant="secondary"
          className="h-8"
          onClick={() =>
            onChange([...rows, { ...EMPTY_ROW[field] }] as MethodologyInput[K])
          }
        >
          Add{" "}
          {field === "datasets"
            ? "dataset"
            : field === "hyperparameters"
              ? "hyperparameter"
              : field === "tools"
                ? "tool"
                : "metric"}
        </Button>
      )}
    </div>
  );
}

/** The researcher's methodology details. Their wording is kept as entered (A4.5). */
export function InputForm({
  value,
  missing,
  disabled,
  onChange,
}: {
  value: MethodologyInput;
  missing: Set<FieldName>;
  disabled: boolean;
  onChange: (value: MethodologyInput) => void;
}) {
  return (
    <div className="space-y-6">
      {ORDER.map((name) => {
        const text = TEXT_FIELDS.find((f) => f.name === name);
        if (text) {
          return (
            <div key={name} id={`field-${name}`} className="scroll-mt-24">
              <label
                htmlFor={`input-${name}`}
                className="text-sm font-medium text-slate-800"
              >
                {text.label}
                <Flag missing={missing.has(name as FieldName)} />
              </label>
              <p className="text-xs text-slate-500">{text.hint}</p>
              <textarea
                id={`input-${name}`}
                className={`${INPUT} mt-1`}
                rows={text.rows}
                value={value[text.name]}
                disabled={disabled}
                onChange={(e) =>
                  onChange({ ...value, [text.name]: e.target.value })
                }
              />
            </div>
          );
        }
        const list = name as ListField;
        return (
          <div key={name} id={`field-${name}`} className="scroll-mt-24">
            <p className="text-sm font-medium text-slate-800">
              {LIST_FIELDS[list].label}
              <Flag missing={missing.has(list)} />
            </p>
            <p className="mb-2 text-xs text-slate-500">
              {LIST_FIELDS[list].hint}
            </p>
            <ListEditor
              field={list}
              rows={value[list]}
              disabled={disabled}
              onChange={(rows) => onChange({ ...value, [list]: rows })}
            />
          </div>
        );
      })}
      <div>
        <label
          htmlFor="input-notes"
          className="text-sm font-medium text-slate-800"
        >
          Anything else
        </label>
        <p className="text-xs text-slate-500">
          Free text: design decisions, constraints, things you tried.
        </p>
        <textarea
          id="input-notes"
          className={`${INPUT} mt-1`}
          rows={4}
          value={value.notes}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, notes: e.target.value })}
        />
      </div>
    </div>
  );
}
