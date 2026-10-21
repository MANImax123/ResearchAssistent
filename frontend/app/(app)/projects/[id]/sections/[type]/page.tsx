"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { SectionEditor } from "@/components/editor/SectionEditor";
import { SECTION_LABEL, type SectionType } from "@/lib/sections";

export default function SectionPage() {
  const { id, type } = useParams<{ id: string; type: string }>();

  if (!(type in SECTION_LABEL)) {
    return (
      <div className="mx-auto max-w-md py-16 text-center">
        <h1 className="text-lg font-semibold">Unknown section</h1>
        <Link href={`/projects/${id}`} className="mt-4 inline-block text-sm font-medium text-indigo-600">
          ← Back to project
        </Link>
      </div>
    );
  }

  const section = type as SectionType;
  return (
    <div className="space-y-6">
      <div>
        <Link href={`/projects/${id}`} className="text-sm text-slate-500 hover:text-slate-700">
          ← Project
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">{SECTION_LABEL[section]}</h1>
        <p className="mt-1 text-sm text-slate-500">
          Changes save automatically. Use <strong className="font-medium">Save version</strong> to mark a milestone, and{" "}
          <strong className="font-medium">History</strong> to compare or restore earlier versions.
        </p>
      </div>
      <SectionEditor projectId={id} type={section} />
    </div>
  );
}
