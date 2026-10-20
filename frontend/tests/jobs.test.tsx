import { act, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobProgress } from "@/components/JobProgress";
import { useJob, type Job } from "@/lib/jobs";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function job(patch: Partial<Job>): Job {
  return {
    id: "j1",
    project_id: null,
    type: "demo",
    status: "pending",
    progress: 0,
    message: "Waiting for a worker",
    result: null,
    error: null,
    created_at: "2026-10-20T12:00:00Z",
    updated_at: "2026-10-20T12:00:00Z",
    ...patch,
  };
}

describe("useJob", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.unstubAllGlobals();
  });
  afterEach(() => vi.useRealTimers());

  it("polls pending → running → success, then stops", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, job({ status: "running", progress: 40, message: "Step 2 of 5" })))
      .mockResolvedValueOnce(json(200, job({ status: "success", progress: 100, message: "Done", result: { ok: 1 } })));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useJob("j1", job({})));
    expect(result.current.job?.status).toBe("pending");

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(result.current.job).toMatchObject({ status: "running", progress: 40 });

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(result.current.job).toMatchObject({ status: "success", progress: 100, result: { ok: 1 } });

    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchMock).toHaveBeenCalledTimes(2); // no polling after a terminal status
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/jobs\/j1$/);
  });

  it("keeps polling with backoff after a network error and recovers", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(json(200, job({ status: "failed", progress: 33, error: "RuntimeError: boom" })));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useJob("j1", job({})));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(result.current.error).toContain("Cannot reach the server");

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(fetchMock).toHaveBeenCalledTimes(1); // backed off to 2 s
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.job?.status).toBe("failed");
    expect(result.current.error).toBeNull();
  });

  it("does nothing without a job id", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useJob(null));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(result.current.job).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("JobProgress", () => {
  it("shows progress, status and message", () => {
    render(<JobProgress job={job({ status: "running", progress: 60, message: "Step 3 of 5" })} label="Demo job" />);

    expect(screen.getByRole("progressbar", { name: "Demo job" })).toHaveAttribute("aria-valuenow", "60");
    expect(screen.getByText("60%")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Step 3 of 5")).toBeInTheDocument();
  });

  it("shows the error for a failed job", () => {
    render(<JobProgress job={job({ status: "failed", progress: 33, error: "RuntimeError: boom" })} />);

    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("RuntimeError: boom");
  });
});
