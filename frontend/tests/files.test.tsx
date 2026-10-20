import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FilesPanel } from "@/components/FilesPanel";
import type { ProjectFile } from "@/lib/api";
import { checkFile, formatSize } from "@/lib/files";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const existing: ProjectFile = {
  id: "f1",
  kind: "paper_pdf",
  filename: "attention.pdf",
  mime: "application/pdf",
  size: 2_150_000,
  uploaded_by: "u1",
  created_at: new Date().toISOString(),
};

/** Minimal XMLHttpRequest stand-in: records the request and answers with `response`. */
function stubXhr(response: { status: number; body: unknown }) {
  const sent: { url: string; data: FormData | null; headers: Record<string, string> }[] = [];
  class FakeXhr {
    status = 0;
    responseText = "";
    upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    private req = { url: "", data: null as FormData | null, headers: {} as Record<string, string> };
    open(_method: string, url: string) {
      this.req.url = url;
    }
    setRequestHeader(name: string, value: string) {
      this.req.headers[name] = value;
    }
    send(data: FormData) {
      this.req.data = data;
      sent.push(this.req);
      setTimeout(() => {
        this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent);
        this.status = response.status;
        this.responseText = JSON.stringify(response.body);
        this.onload?.();
      }, 0);
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return sent;
}

describe("FilesPanel", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("lists existing files with kind, size and actions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [existing])));
    render(<FilesPanel projectId="p1" canEdit />);

    const list = await screen.findByRole("list", { name: "Project files" });
    const row = within(list).getByText("attention.pdf").closest("li")!;
    expect(row).toHaveTextContent("Paper (PDF)");
    expect(row).toHaveTextContent("2.1 MB");
    expect(within(row).getByRole("button", { name: "Download" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Delete attention.pdf" })).toBeInTheDocument();
  });

  it("hides upload and delete for viewers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [existing])));
    render(<FilesPanel projectId="p1" canEdit={false} />);

    await screen.findByText("attention.pdf");
    expect(screen.queryByTestId("dropzone")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("uploads a dropped file as the selected kind and adds it to the list", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [])));
    const created = { ...existing, id: "f2", kind: "result", filename: "results.csv", size: 30 };
    const sent = stubXhr({ status: 201, body: created });
    render(<FilesPanel projectId="p1" canEdit />);
    await screen.findByText("No files uploaded yet.");

    await userEvent.selectOptions(screen.getByLabelText("File type"), "result");
    const file = new File(["model,acc\nours,0.9\n"], "results.csv", { type: "text/csv" });
    fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [file] } });

    const list = await screen.findByRole("list", { name: "Project files" });
    expect(within(list).getByText("results.csv")).toBeInTheDocument();
    expect(within(list).getByText("Results")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("list", { name: "Uploads" })).not.toBeInTheDocument());
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toMatch(/\/projects\/p1\/files$/);
    expect(sent[0].data!.get("kind")).toBe("result");
    expect((sent[0].data!.get("file") as File).name).toBe("results.csv");
  });

  it("rejects a disallowed type before uploading", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [])));
    const sent = stubXhr({ status: 201, body: {} });
    render(<FilesPanel projectId="p1" canEdit />);
    await screen.findByText("No files uploaded yet.");

    const file = new File(["a,b"], "data.csv", { type: "text/csv" });
    fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent(".csv not allowed for Paper (PDF)");
    expect(sent).toHaveLength(0);
  });

  it("shows the server's error when an upload is rejected", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, [])));
    stubXhr({ status: 422, body: { detail: "File content does not look like a .pdf file" } });
    render(<FilesPanel projectId="p1" canEdit />);
    await screen.findByText("No files uploaded yet.");

    const file = new File(["<html>"], "paper.pdf", { type: "application/pdf" });
    fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("does not look like a .pdf file");
  });
});

describe("file helpers", () => {
  it.each([
    [{ name: "paper.PDF", size: 10 }, "paper_pdf", null],
    [{ name: "notes", size: 10 }, "notes", "Files without an extension not allowed for Notes"],
    [{ name: "empty.csv", size: 0 }, "dataset", "File is empty"],
    [{ name: "huge.csv", size: 51 * 1024 * 1024 }, "dataset", "File is larger than 50 MB"],
  ] as const)("checkFile(%o, %s)", (file, kind, expected) => {
    const result = checkFile(file, kind);
    if (expected === null) expect(result).toBeNull();
    else expect(result).toContain(expected);
  });

  it.each([
    [512, "512 B"],
    [2048, "2.0 KB"],
    [2_150_000, "2.1 MB"],
    [52_428_800, "50 MB"],
  ])("formatSize(%i) = %s", (bytes, expected) => {
    expect(formatSize(bytes)).toBe(expected);
  });
});
