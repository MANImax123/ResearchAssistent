import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LLMTestCard, type LLMTestResult } from "@/components/LLMTestCard";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const result: LLMTestResult = {
  data: {
    working_title: "Grounded multi-agent paper writing",
    keywords: ["LLM agents", "RAG"],
    research_questions: ["Does grounding reduce citation errors?"],
    one_line_summary: "A human-in-the-loop paper assistant.",
  },
  run: { agent_run_id: "r1", provider: "gemini", model: "gemini-3.8-flash", tokens_in: 120, tokens_out: 85, latency_ms: 2400 },
  embedding: { provider: "gemini", model: "gemini-embedding-2", dimensions: 1536, norm: 1 },
};

describe("LLMTestCard", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("shows model, tokens, latency, embedding and the structured answer", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(200, result));
    vi.stubGlobal("fetch", fetchMock);
    render(<LLMTestCard />);

    await userEvent.click(screen.getByRole("button", { name: "Test AI connection" }));

    expect(await screen.findByText("Grounded multi-agent paper writing")).toBeInTheDocument();
    expect(screen.getByText("gemini-3.8-flash")).toBeInTheDocument();
    expect(screen.getByText("120 / 85")).toBeInTheDocument();
    expect(screen.getByText("2.4 s")).toBeInTheDocument();
    expect(screen.getByText("1536 dims")).toBeInTheDocument();
    expect(screen.getByText("Does grounding reduce citation errors?")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/debug\/llm-test$/);
  });

  it("shows the configuration error from the server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json(503, { detail: "LLM provider 'gemini' is not configured: set GEMINI_API_KEY in .env" })),
    );
    render(<LLMTestCard />);

    await userEvent.click(screen.getByRole("button", { name: "Test AI connection" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("set GEMINI_API_KEY in .env");
  });
});
