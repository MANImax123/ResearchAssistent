import type { Editor } from "@tiptap/core";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SectionEditor } from "@/components/editor/SectionEditor";
import type { SectionDetail, Version } from "@/lib/sections";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const v1: Version = {
  id: "v1",
  number: 1,
  source: "ai",
  created_by: null,
  created_at: new Date().toISOString(),
  change_note: "Drafted by stub agent",
  agent_run_id: null,
  content: "Transformers [@vaswani2017] changed NLP.",
};

function detail(patch: Partial<SectionDetail> = {}): SectionDetail {
  const { content, ...meta } = v1;
  return {
    id: "s1",
    type: "abstract",
    stage: "idea",
    status: "draft",
    exists: true,
    version_count: 1,
    current_version: meta,
    word_count: 4,
    editable: true,
    edit_blocked_reason: null,
    content,
    ...patch,
  };
}

type Route = (url: string, init: RequestInit) => Response | undefined;

/** fetch mock that answers by method + URL suffix and records calls. */
function mockApi(routes: Route) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return routes(url, init) ?? json(404, { detail: `unmocked ${init.method ?? "GET"} ${url}` });
    }),
  );
  return calls;
}

async function editorFor(label: string): Promise<Editor> {
  const el = await screen.findByLabelText(label);
  await waitFor(() => expect((el as unknown as { editor?: Editor }).editor).toBeDefined());
  return (el as unknown as { editor: Editor }).editor;
}

describe("SectionEditor", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("loads the current version with its badge and renders citation chips", async () => {
    mockApi((url) => (url.endsWith("/sections/abstract") ? json(200, detail()) : undefined));
    render(<SectionEditor projectId="p1" type="abstract" />);

    expect(await screen.findByText("AI draft")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector('[data-citation="vaswani2017"]')).not.toBeNull());
    expect(screen.getByRole("toolbar", { name: "Formatting" })).toBeInTheDocument();
  });

  it("opening a section is not an edit: no unsaved state, no save", async () => {
    const calls = mockApi((url) => (url.endsWith("/sections/abstract") ? json(200, detail()) : undefined));
    render(<SectionEditor projectId="p1" type="abstract" />);
    await editorFor("Abstract text");

    await new Promise((r) => setTimeout(r, 2000)); // longer than the autosave delay
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("autosaves edits as markdown against the base version", async () => {
    const calls = mockApi((url, init) => {
      if (url.endsWith("/sections/abstract")) return json(200, detail());
      if (url.endsWith("/sections/abstract/versions") && init.method === "POST") {
        const body = JSON.parse(String(init.body));
        return json(201, {
          version: { ...v1, id: "v2", number: 2, source: "ai_edited", change_note: "Autosave", content: body.content },
          coalesced: false,
        });
      }
    });
    render(<SectionEditor projectId="p1" type="abstract" />);
    const editor = await editorFor("Abstract text");
    await waitFor(() => expect(editor.getMarkdown()).toContain("[@vaswani2017]"));

    editor.commands.focus("end");
    editor.commands.insertContent(" More text.");

    expect(await screen.findByText("Unsaved changes")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true), { timeout: 4000 });
    const save = calls.find((c) => c.method === "POST")!.body as Record<string, unknown>;
    expect(save).toMatchObject({ autosave: true, base_version_id: "v1" });
    expect(save.content).toContain("[@vaswani2017]");
    expect(save.content).toContain("More text.");
    expect(await screen.findByText("Edited")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
  });

  it("Save version creates an explicit (non-autosave) version", async () => {
    const calls = mockApi((url, init) => {
      if (url.endsWith("/sections/abstract")) return json(200, detail());
      if (init.method === "POST") return json(201, { version: { ...v1, id: "v2", number: 2, source: "ai_edited" }, coalesced: false });
    });
    render(<SectionEditor projectId="p1" type="abstract" />);
    await editorFor("Abstract text");

    await userEvent.click(screen.getByRole("button", { name: "Save version" }));

    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ autosave: false }));
  });

  it("shows a conflict when the section changed elsewhere", async () => {
    mockApi((url, init) => {
      if (url.endsWith("/sections/abstract")) return json(200, detail());
      if (init.method === "POST")
        return json(409, { detail: "This section changed since you opened it (now v3); reload to continue" });
    });
    render(<SectionEditor projectId="p1" type="abstract" />);
    await editorFor("Abstract text");

    await userEvent.click(screen.getByRole("button", { name: "Save version" }));

    expect(await screen.findByText("Changed elsewhere")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("now v3");
    expect(screen.getByRole("button", { name: "Load latest" })).toBeInTheDocument();
  });

  it("is read-only with the reason when the stage isn't under review", async () => {
    mockApi((url) =>
      url.endsWith("/sections/abstract")
        ? json(200, detail({ editable: false, edit_blocked_reason: "Go back to this stage to edit it", status: "approved" }))
        : undefined,
    );
    render(<SectionEditor projectId="p1" type="abstract" />);
    const editor = await editorFor("Abstract text");

    expect(screen.getByText("Read-only: Go back to this stage to edit it.")).toBeInTheDocument();
    expect(screen.queryByRole("toolbar")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save version" })).not.toBeInTheDocument();
    expect(editor.isEditable).toBe(false);
  });

  it("history: preview, compare and restore an older version", async () => {
    const v2 = { ...v1, id: "v2", number: 2, source: "ai_edited" as const, change_note: "Autosave", content: "Rewritten abstract." };
    const restored = { ...v1, id: "v3", number: 3, change_note: "Restored from v1" };
    const { content: _c, ...v1meta } = v1;
    const { content: _c2, ...v2meta } = v2;
    const calls = mockApi((url, init) => {
      if (url.endsWith("/sections/abstract")) return json(200, detail({ current_version: v2meta, content: v2.content, version_count: 2 }));
      if (url.endsWith("/sections/abstract/versions")) return json(200, [v2meta, v1meta]);
      if (url.endsWith("/versions/v1") && !init.method) return json(200, v1);
      if (url.includes("/diff?from_version=v1&to_version=v2"))
        return json(200, { from_version: 1, to_version: 2, unified: "--- v1\n+++ v2\n@@ -1 +1 @@\n-Transformers [@vaswani2017] changed NLP.\n+Rewritten abstract.", added: 1, removed: 1 });
      if (url.endsWith("/versions/v1/restore")) return json(201, restored);
    });
    render(<SectionEditor projectId="p1" type="abstract" />);
    const editor = await editorFor("Abstract text");

    await userEvent.click(await screen.findByRole("button", { name: "History (2)" }));
    const drawer = await screen.findByRole("dialog", { name: "Abstract: version history" });
    await userEvent.click(await within(drawer).findByRole("button", { name: /v1/ }));

    await userEvent.click(await within(drawer).findByRole("tab", { name: "Changes vs current" }));
    const diff = await within(drawer).findByLabelText("Differences");
    expect(diff).toHaveTextContent("-Transformers [@vaswani2017] changed NLP.");
    expect(diff).toHaveTextContent("+Rewritten abstract.");

    await userEvent.click(within(drawer).getByRole("button", { name: "Restore v1" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/versions/v1/restore"))).toBe(true);
    expect(editor.getMarkdown()).toContain("Transformers [@vaswani2017] changed NLP.");
    expect(screen.getByText("v3")).toBeInTheDocument();
  });
});
