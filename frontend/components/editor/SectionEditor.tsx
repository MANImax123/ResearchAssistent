"use client";

import { EditorContent, useEditor, useEditorState, type Editor } from "@tiptap/react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { CITATION_KEY, cleanMarkdown, sectionExtensions } from "@/components/editor/extensions";
import { VersionHistory } from "@/components/editor/VersionHistory";
import { Badge, Button, ErrorAlert, Spinner } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { SECTION_LABEL, sectionsApi, SOURCE_BADGE, type SectionDetail, type SectionType, type Version } from "@/lib/sections";

const AUTOSAVE_DELAY_MS = 1500;

type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";

/**
 * Rich-text editor for one paper section. Stored as Markdown; every save is a version.
 * Typing autosaves (merged into your recent autosave); "Save version" always creates a new one.
 */
export function SectionEditor({
  projectId,
  type,
  compact = false,
  onChanged,
}: {
  projectId: string;
  type: SectionType;
  compact?: boolean;
  onChanged?: () => void;
}) {
  const [section, setSection] = useState<SectionDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [milestone, setMilestone] = useState<number | null>(null); // "Saved as vN" after Save version
  const [historyOpen, setHistoryOpen] = useState(false);

  const base = useRef<string | null>(null); // version the editor content is based on
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inFlight = useRef(false);
  const pending = useRef(false);

  const editor = useEditor({
    extensions: sectionExtensions(),
    content: "",
    editable: false,
    immediatelyRender: false,
    editorProps: { attributes: { class: "prose-section", "aria-label": `${SECTION_LABEL[type]} text` } },
    onUpdate: () => scheduleSave(),
  });

  const load = useCallback(async () => {
    try {
      const detail = await sectionsApi.get(projectId, type);
      setSection(detail);
      setLoadError(null);
      base.current = detail.current_version?.id ?? null;
      setSaveState("saved");
      if (editor) {
        editor.commands.setContent(detail.content, { emitUpdate: false, contentType: "markdown" });
        editor.setEditable(detail.editable, false); // emitting "update" here would look like an unsaved edit
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Could not load section");
    }
  }, [projectId, type, editor]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (autosave: boolean, changeNote?: string) => {
      if (!editor) return;
      if (inFlight.current) {
        pending.current = true;
        return;
      }
      clearTimeout(timer.current);
      inFlight.current = true;
      setSaveState("saving");
      setSaveError(null);
      try {
        const { version, coalesced } = await sectionsApi.save(projectId, type, {
          content: cleanMarkdown(editor.getMarkdown()),
          base_version_id: base.current,
          autosave,
          change_note: changeNote ?? null,
        });
        base.current = version.id;
        setSection((prev) => prev && { ...prev, current_version: version, version_count: prev.version_count + (coalesced ? 0 : 1) });
        setSaveState(pending.current ? "dirty" : "saved");
        setMilestone(autosave ? null : version.number);
        if (!coalesced) onChanged?.();
      } catch (err) {
        const conflict = err instanceof ApiError && err.status === 409;
        setSaveState(conflict ? "conflict" : "error");
        setSaveError(err instanceof Error ? err.message : "Could not save");
      } finally {
        inFlight.current = false;
        if (pending.current) {
          pending.current = false;
          timer.current = setTimeout(() => void save(true), AUTOSAVE_DELAY_MS);
        }
      }
    },
    [editor, projectId, type, onChanged],
  );

  const saveRef = useRef(save);
  saveRef.current = save;

  function scheduleSave() {
    setMilestone(null);
    setSaveState((s) => (s === "conflict" ? s : "dirty"));
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void saveRef.current(true), AUTOSAVE_DELAY_MS);
  }

  // Don't lose unsaved typing when leaving the page
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (saveState === "dirty" || saveState === "saving") e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [saveState]);

  useEffect(() => () => clearTimeout(timer.current), []);

  if (loadError) return <ErrorAlert>{loadError}</ErrorAlert>;
  if (!section || !editor) return <Spinner label="Loading section" />;

  const source = section.current_version?.source;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {!compact && <span className="font-medium text-slate-900">{SECTION_LABEL[type]}</span>}
          {source && <Badge className={SOURCE_BADGE[source].className}>{SOURCE_BADGE[source].label}</Badge>}
          {section.current_version && (
            <span className="text-xs text-slate-500">v{section.current_version.number}</span>
          )}
          <SaveIndicator
            state={saveState}
            at={section.current_version?.created_at}
            milestone={saveState === "saved" ? milestone : null}
          />
        </div>
        <div className="flex gap-2">
          {section.editable && (
            <Button
              variant="secondary"
              className="h-8 px-3"
              onClick={() => void save(false, "Saved by researcher")}
              disabled={saveState === "saving" || saveState === "conflict"}
            >
              Save version
            </Button>
          )}
          {section.exists && (
            <Button variant="secondary" className="h-8 px-3" onClick={() => setHistoryOpen(true)}>
              History ({section.version_count})
            </Button>
          )}
        </div>
      </div>

      {saveState === "conflict" && (
        <ErrorAlert
          action={
            <Button variant="secondary" className="h-8" onClick={() => void load()}>
              Load latest
            </Button>
          }
        >
          {saveError} Your unsaved changes here would be replaced.
        </ErrorAlert>
      )}
      {saveState === "error" && saveError && (
        <ErrorAlert
          action={
            <Button variant="secondary" className="h-8" onClick={() => void save(true)}>
              Retry
            </Button>
          }
        >
          Not saved: {saveError}
        </ErrorAlert>
      )}
      {!section.editable && section.edit_blocked_reason && (
        <p className="text-xs text-slate-500">Read-only: {section.edit_blocked_reason}.</p>
      )}

      <div className="overflow-hidden rounded-lg border border-slate-300 bg-white focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/20">
        {section.editable && <Toolbar editor={editor} />}
        <div className={`overflow-auto px-4 py-3 ${compact ? "max-h-96 min-h-40" : "min-h-80"}`}>
          <EditorContent editor={editor} />
        </div>
      </div>

      {historyOpen && (
        <VersionHistory
          projectId={projectId}
          type={type}
          currentId={section.current_version?.id ?? null}
          canRestore={section.editable}
          onClose={() => setHistoryOpen(false)}
          onRestored={(v: Version) => {
            setHistoryOpen(false);
            base.current = v.id;
            editor.commands.setContent(v.content, { emitUpdate: false, contentType: "markdown" });
            setSection((prev) => prev && { ...prev, current_version: v, version_count: prev.version_count + 1 });
            setSaveState("saved");
            onChanged?.();
          }}
        />
      )}
    </div>
  );
}

function SaveIndicator({ state, at, milestone }: { state: SaveState; at?: string; milestone: number | null }) {
  const text: Record<SaveState, string> = {
    saved: milestone ? `Saved as v${milestone}` : at ? `Saved ${relativeTime(at)}` : "No changes",
    dirty: "Unsaved changes",
    saving: "Saving…",
    error: "Not saved",
    conflict: "Changed elsewhere",
  };
  const tone = state === "error" || state === "conflict" ? "text-red-600" : state === "saved" ? "text-slate-500" : "text-amber-700";
  return (
    <span role="status" aria-live="polite" className={`text-xs ${tone}`}>
      {text[state]}
    </span>
  );
}

function Toolbar({ editor }: { editor: Editor }) {
  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      h2: e.isActive("heading", { level: 2 }),
      h3: e.isActive("heading", { level: 3 }),
      bold: e.isActive("bold"),
      italic: e.isActive("italic"),
      bullet: e.isActive("bulletList"),
      ordered: e.isActive("orderedList"),
      table: e.isActive("table"),
    }),
  });
  const [inserting, setInserting] = useState<"citation" | "figure" | null>(null);
  const [value, setValue] = useState("");

  const chain = () => editor.chain().focus();
  const keyValid = CITATION_KEY.test(value.trim());

  function insert() {
    if (inserting === "citation" && keyValid) chain().insertCitation(value.trim()).run();
    if (inserting === "figure" && value.trim()) chain().insertFigurePlaceholder(value.trim()).run();
    setInserting(null);
    setValue("");
  }

  return (
    <div className="border-b border-slate-200 bg-slate-50 px-2 py-1.5">
      <div role="toolbar" aria-label="Formatting" className="flex flex-wrap items-center gap-1">
        <Tool label="Heading 2" pressed={active.h2} onClick={() => chain().toggleHeading({ level: 2 }).run()}>
          H2
        </Tool>
        <Tool label="Heading 3" pressed={active.h3} onClick={() => chain().toggleHeading({ level: 3 }).run()}>
          H3
        </Tool>
        <Tool label="Bold" pressed={active.bold} onClick={() => chain().toggleBold().run()}>
          <strong>B</strong>
        </Tool>
        <Tool label="Italic" pressed={active.italic} onClick={() => chain().toggleItalic().run()}>
          <em>I</em>
        </Tool>
        <Tool label="Bullet list" pressed={active.bullet} onClick={() => chain().toggleBulletList().run()}>
          • List
        </Tool>
        <Tool label="Numbered list" pressed={active.ordered} onClick={() => chain().toggleOrderedList().run()}>
          1. List
        </Tool>
        <Tool
          label="Insert table"
          pressed={active.table}
          onClick={() => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
        >
          Table
        </Tool>
        {active.table && (
          <>
            <Tool label="Add row" onClick={() => chain().addRowAfter().run()}>
              + Row
            </Tool>
            <Tool label="Add column" onClick={() => chain().addColumnAfter().run()}>
              + Col
            </Tool>
            <Tool label="Delete table" onClick={() => chain().deleteTable().run()}>
              Delete table
            </Tool>
          </>
        )}
        <span className="mx-1 h-5 w-px bg-slate-300" aria-hidden />
        <Tool label="Insert citation" pressed={inserting === "citation"} onClick={() => setInserting("citation")}>
          @ Cite
        </Tool>
        <Tool label="Insert figure placeholder" pressed={inserting === "figure"} onClick={() => setInserting("figure")}>
          Figure
        </Tool>
      </div>
      {inserting && (
        <form
          className="mt-1.5 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            insert();
          }}
        >
          <label htmlFor="insert-value" className="text-xs text-slate-600">
            {inserting === "citation" ? "Citation key" : "Figure caption"}
          </label>
          <input
            id="insert-value"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={inserting === "citation" ? "e.g. vaswani2017" : "e.g. System architecture"}
            className="h-8 w-56 rounded-md border border-slate-300 px-2 text-sm focus:border-indigo-500 focus:outline-none"
          />
          <Button type="submit" className="h-8 px-3" disabled={inserting === "citation" ? !keyValid : !value.trim()}>
            Insert
          </Button>
          <Button variant="secondary" className="h-8 px-3" onClick={() => setInserting(null)}>
            Cancel
          </Button>
          {inserting === "citation" && (
            <span className="text-xs text-slate-500">Keys are checked against your verified references later (Step 8).</span>
          )}
        </form>
      )}
    </div>
  );
}

function Tool({
  label,
  pressed,
  onClick,
  children,
}: {
  label: string;
  pressed?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      onMouseDown={(e) => e.preventDefault()} // keep the editor's selection
      onClick={onClick}
      className={`h-8 rounded px-2 text-sm ${pressed ? "bg-indigo-100 text-indigo-700" : "text-slate-700 hover:bg-slate-200"}`}
    >
      {children}
    </button>
  );
}
