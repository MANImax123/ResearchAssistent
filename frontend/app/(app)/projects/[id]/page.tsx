"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { FilesPanel } from "@/components/FilesPanel";
import { ProjectForm } from "@/components/ProjectForm";
import { StageTracker } from "@/components/StageTracker";
import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import { ApiError, projectsApi, type ProjectDetail } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { venueLabel } from "@/lib/stages";

export default function ProjectOverviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(() => {
    setError(null);
    projectsApi
      .get(id)
      .then(setProject)
      .catch((err: Error) => setError({ status: err instanceof ApiError ? err.status : 0, message: err.message }));
  }, [id]);

  useEffect(load, [load]);

  if (error?.status === 404 || error?.status === 422) {
    return (
      <div className="mx-auto max-w-md py-16 text-center">
        <h1 className="text-lg font-semibold">Project not found</h1>
        <p className="mt-1 text-sm text-slate-500">It may have been deleted, or you don&apos;t have access to it.</p>
        <Link href="/dashboard" className="mt-4 inline-block text-sm font-medium text-indigo-600">
          ← Back to dashboard
        </Link>
      </div>
    );
  }
  if (error) {
    return (
      <ErrorAlert
        action={
          <Button variant="secondary" className="h-8" onClick={load}>
            Retry
          </Button>
        }
      >
        Could not load project: {error.message}
      </ErrorAlert>
    );
  }
  if (!project) return <Spinner label="Loading project" />;

  const canEdit = project.role === "owner" || project.role === "editor";
  const venue = venueLabel(project.target_venue);

  return (
    <div className="space-y-8">
      <div>
        <Link href="/dashboard" className="text-sm text-slate-500 hover:text-slate-700">
          ← Dashboard
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight break-words">{project.title}</h1>
            <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-slate-500">
              <span>{[project.domain, venue].filter(Boolean).join(" · ") || "No domain set"}</span>
              <span aria-hidden>·</span>
              <span>Updated {relativeTime(project.updated_at)}</span>
              {project.role !== "owner" && <Badge className="bg-slate-100 text-slate-600">{project.role}</Badge>}
            </p>
          </div>
          {!editing && (
            <div className="flex gap-3">
              {canEdit && (
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  Edit
                </Button>
              )}
              {project.role === "owner" && (
                <Button variant="danger-outline" onClick={() => setConfirmDelete(true)}>
                  Delete
                </Button>
              )}
            </div>
          )}
        </div>
      </div>

      {editing ? (
        <section className="rounded-xl border border-slate-200 bg-white p-6">
          <h2 className="mb-5 text-base font-semibold">Edit project</h2>
          <ProjectForm
            initial={project}
            submitLabel="Save changes"
            onSubmit={async (values) => {
              setProject(await projectsApi.update(project.id, values));
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        </section>
      ) : (
        <section className="rounded-xl border border-slate-200 bg-white p-6">
          <h2 className="text-sm font-semibold text-slate-900">Research idea</h2>
          {project.raw_idea ? (
            <p className="mt-2 text-sm whitespace-pre-wrap text-slate-700">{project.raw_idea}</p>
          ) : (
            <p className="mt-2 text-sm text-slate-500">
              No idea written yet.{" "}
              {canEdit && (
                <button type="button" className="font-medium text-indigo-600" onClick={() => setEditing(true)}>
                  Add one
                </button>
              )}
            </p>
          )}
        </section>
      )}

      <FilesPanel projectId={project.id} canEdit={canEdit} />

      <section className="space-y-3">
        <h2 className="text-base font-semibold">Stages</h2>
        <StageTracker stages={project.stages} current={project.current_stage} />
      </section>

      {confirmDelete && (
        <ConfirmDialog
          title="Delete this project?"
          confirmLabel="Delete project"
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            await projectsApi.remove(project.id);
            router.replace("/dashboard");
          }}
        >
          <p>
            <strong className="font-medium text-slate-900">{project.title}</strong> and everything in it (papers,
            sections, files) will be permanently deleted.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
