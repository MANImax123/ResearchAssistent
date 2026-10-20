"use client";

import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { user } = useAuth();
  const firstName = user?.name.split(" ")[0];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Welcome, {firstName}</h1>
        <p className="mt-1 text-sm text-slate-500">Your research projects will appear here.</p>
      </div>

      <section className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
        <h2 className="text-base font-semibold text-slate-900">No projects yet</h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
          A project takes your research idea through literature survey, methodology, results and every other
          stage, ending in a formatted manuscript.
        </p>
      </section>
    </div>
  );
}
