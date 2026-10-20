"use client";

import { useCallback, useEffect, useState } from "react";

import { ProjectCard } from "@/components/ProjectCard";
import { Button, ErrorAlert, LinkButton, Spinner } from "@/components/ui";
import { projectsApi, type ProjectSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { user } = useAuth();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    projectsApi
      .list()
      .then(setProjects)
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(load, [load]);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Welcome, {user?.name.split(" ")[0]}</h1>
          <p className="mt-1 text-sm text-slate-500">Your research projects</p>
        </div>
        <LinkButton href="/projects/new">New project</LinkButton>
      </div>

      {error && (
        <ErrorAlert
          action={
            <Button variant="secondary" className="h-8" onClick={load}>
              Retry
            </Button>
          }
        >
          Could not load projects: {error}
        </ErrorAlert>
      )}

      {projects === null && !error && <Spinner label="Loading projects" />}

      {projects?.length === 0 && (
        <section className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
          <h2 className="text-base font-semibold text-slate-900">No projects yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
            A project takes your research idea through literature survey, methodology, results and every other
            stage, ending in a formatted manuscript.
          </p>
          <LinkButton href="/projects/new" className="mt-6">
            Create your first project
          </LinkButton>
        </section>
      )}

      {projects && projects.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {projects.map((p) => (
            <li key={p.id} className="flex">
              <ProjectCard project={p} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
