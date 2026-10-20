"use client";

import { useState } from "react";

import { Badge, Button, ErrorAlert } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";

export type LLMTestResult = {
  data: { working_title: string; keywords: string[]; research_questions: string[]; one_line_summary: string };
  run: { agent_run_id: string; provider: string; model: string; tokens_in: number; tokens_out: number; latency_ms: number };
  embedding: { provider: string; model: string; dimensions: number; norm: number } | null;
};

export function LLMTestCard() {
  const [result, setResult] = useState<LLMTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await apiFetch<LLMTestResult>("/debug/llm-test", { method: "POST", json: {} }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setError("The AI test is only available in development (APP_ENV=dev).");
      else setError(err instanceof Error ? err.message : "Test failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold">AI connection</h2>
        <p className="mt-1 text-sm text-slate-500">
          Sends a sample research idea to the configured model and asks for a structured answer, then embeds it.
          Each call is logged with its token usage.
        </p>
      </div>
      <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
        <Button onClick={run} loading={running}>
          Test AI connection
        </Button>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        {result && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
              <Stat label="Model" value={result.run.model} sub={result.run.provider} />
              <Stat label="Tokens in / out" value={`${result.run.tokens_in} / ${result.run.tokens_out}`} />
              <Stat label="Latency" value={`${(result.run.latency_ms / 1000).toFixed(1)} s`} />
              <Stat
                label="Embedding"
                value={result.embedding ? `${result.embedding.dimensions} dims` : "—"}
                sub={result.embedding?.model}
              />
            </dl>
            <div className="space-y-3 rounded-lg bg-slate-50 p-4 text-sm">
              <p className="font-medium text-slate-900">{result.data.working_title}</p>
              <p className="text-slate-600">{result.data.one_line_summary}</p>
              <div className="flex flex-wrap gap-1.5">
                {result.data.keywords.map((k, i) => (
                  <Badge key={`${i}-${k}`} className="bg-indigo-100 text-indigo-700">
                    {k}
                  </Badge>
                ))}
              </div>
              <ol className="list-decimal space-y-1 pl-5 text-slate-700">
                {result.data.research_questions.map((q, i) => (
                  <li key={`${i}-${q}`}>{q}</li>
                ))}
              </ol>
            </div>
            <p className="text-xs text-slate-400">Logged as agent run {result.run.agent_run_id}</p>
          </div>
        )}
      </div>
    </section>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="truncate font-medium text-slate-900" title={value}>
        {value}
      </dd>
      {sub && <dd className="truncate text-xs text-slate-400">{sub}</dd>}
    </div>
  );
}
