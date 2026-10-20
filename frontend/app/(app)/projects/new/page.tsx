"use client";

import { useRouter } from "next/navigation";

import { ProjectForm } from "@/components/ProjectForm";
import { projectsApi } from "@/lib/api";

export default function NewProjectPage() {
  const router = useRouter();

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New project</h1>
        <p className="mt-1 text-sm text-slate-500">Start with a working title and your idea. Everything can change later.</p>
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-6">
        <ProjectForm
          submitLabel="Create project"
          onSubmit={async (values) => {
            const project = await projectsApi.create(values);
            router.push(`/projects/${project.id}`);
          }}
          onCancel={() => router.push("/dashboard")}
        />
      </div>
    </div>
  );
}
