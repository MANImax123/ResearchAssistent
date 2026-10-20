"use client";

import { useState, type FormEvent } from "react";

import { Button, ErrorAlert, Select, TextArea, TextField } from "@/components/ui";
import type { ProjectInput } from "@/lib/api";
import { VENUES } from "@/lib/stages";

type Props = {
  initial?: Partial<ProjectInput>;
  submitLabel: string;
  onSubmit: (values: ProjectInput) => Promise<void>;
  onCancel?: () => void;
};

export function ProjectForm({ initial = {}, submitLabel, onSubmit, onCancel }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const text = (name: string) => String(form.get(name) ?? "").trim();
    const values: ProjectInput = {
      title: text("title"),
      domain: text("domain") || null,
      raw_idea: text("raw_idea") || null,
      target_venue: text("target_venue") || null,
    };
    if (!values.title) {
      setError("Title is required.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5" noValidate>
      {error && <ErrorAlert>{error}</ErrorAlert>}
      <TextField
        label="Title"
        id="title"
        name="title"
        defaultValue={initial.title ?? ""}
        placeholder="Working title for your paper"
        maxLength={500}
        required
      />
      <div className="grid gap-5 sm:grid-cols-2">
        <TextField
          label="Domain"
          id="domain"
          name="domain"
          defaultValue={initial.domain ?? ""}
          placeholder="e.g. Natural Language Processing"
          maxLength={200}
        />
        <Select
          label="Target venue"
          id="target_venue"
          name="target_venue"
          defaultValue={initial.target_venue ?? ""}
          hint="Optional — you can choose later."
        >
          <option value="">Not decided yet</option>
          {VENUES.map((v) => (
            <option key={v.value} value={v.value}>
              {v.label}
            </option>
          ))}
          {initial.target_venue && !VENUES.some((v) => v.value === initial.target_venue) && (
            <option value={initial.target_venue}>{initial.target_venue}</option>
          )}
        </Select>
      </div>
      <TextArea
        label="Research idea"
        id="raw_idea"
        name="raw_idea"
        defaultValue={initial.raw_idea ?? ""}
        rows={6}
        placeholder="Describe your idea in your own words: the problem, why it matters, what you want to try…"
        hint="Rough notes are fine. The Idea stage turns this into a structured plan."
        maxLength={20000}
      />
      <div className="flex flex-wrap justify-end gap-3">
        {onCancel && (
          <Button type="button" variant="secondary" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
        )}
        <Button type="submit" loading={submitting}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
