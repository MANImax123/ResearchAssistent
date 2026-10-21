"use client";

import { EditorContent, useEditor } from "@tiptap/react";
import { useEffect } from "react";

import { sectionExtensions } from "@/components/editor/extensions";

/** Read-only rendering of section Markdown (same extensions as the editor, so chips/figures/tables match). */
export function MarkdownView({ markdown }: { markdown: string }) {
  const editor = useEditor({
    extensions: sectionExtensions(),
    content: markdown,
    contentType: "markdown",
    editable: false,
    immediatelyRender: false,
    editorProps: { attributes: { class: "prose-section" } },
  });

  useEffect(() => {
    editor?.commands.setContent(markdown, { emitUpdate: false, contentType: "markdown" });
  }, [editor, markdown]);

  return <EditorContent editor={editor} />;
}
