/**
 * Editor extensions for paper sections. Content is stored as Markdown:
 *   citation chip       [@vaswani2017]          (resolved to a verified reference in Step 8.2)
 *   figure placeholder  {{figure: Model overview}}   (on its own line; real figures arrive in 5.2 / 6.2)
 */
import { InputRule, mergeAttributes, Node, type Extensions } from "@tiptap/core";
import { Markdown } from "@tiptap/markdown";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";

export const CITATION_KEY = /^[A-Za-z0-9_:.\-/]+$/;

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    citation: { insertCitation: (key: string) => ReturnType };
    figurePlaceholder: { insertFigurePlaceholder: (caption: string) => ReturnType };
  }
}

export const Citation = Node.create({
  name: "citation",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return { key: { default: "" } };
  },

  parseHTML() {
    return [{ tag: "span[data-citation]", getAttrs: (el) => ({ key: (el as HTMLElement).dataset.citation }) }];
  },

  renderHTML({ node, HTMLAttributes }) {
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-citation": node.attrs.key,
        class: "citation-chip",
        title: `Citation: ${node.attrs.key}`,
      }),
      `@${node.attrs.key}`,
    ];
  },

  markdownTokenizer: {
    name: "citation",
    level: "inline",
    start: (src: string) => src.indexOf("[@"),
    tokenize: (src: string) => {
      const match = /^\[@([A-Za-z0-9_:.\-/]+)\]/.exec(src);
      return match ? { type: "citation", raw: match[0], key: match[1] } : undefined;
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode("citation", { key: token.key }),
  renderMarkdown: (node) => `[@${node.attrs?.key ?? ""}]`,

  addCommands() {
    return {
      insertCitation:
        (key: string) =>
        ({ commands }) =>
          commands.insertContent({ type: this.name, attrs: { key } }),
    };
  },

  addInputRules() {
    // Typing "[@smith2020]" turns into a chip
    return [
      new InputRule({
        find: /\[@([A-Za-z0-9_:.\-/]+)\]$/,
        handler: ({ state, range, match }) => {
          state.tr.replaceWith(range.from, range.to, this.type.create({ key: match[1] }));
        },
      }),
    ];
  },
});

export const FigurePlaceholder = Node.create({
  name: "figurePlaceholder",
  group: "block",
  atom: true,
  selectable: true,
  draggable: true,

  addAttributes() {
    return { caption: { default: "" } };
  },

  parseHTML() {
    return [{ tag: "div[data-figure]", getAttrs: (el) => ({ caption: (el as HTMLElement).dataset.figure }) }];
  },

  renderHTML({ node, HTMLAttributes }) {
    return [
      "div",
      mergeAttributes(HTMLAttributes, { "data-figure": node.attrs.caption, class: "figure-placeholder" }),
      `Figure: ${node.attrs.caption || "untitled"}`,
    ];
  },

  markdownTokenizer: {
    name: "figurePlaceholder",
    level: "block",
    start: (src: string) => src.indexOf("{{figure:"),
    tokenize: (src: string) => {
      const match = /^\{\{figure:\s*([^}\n]*?)\s*\}\}[ \t]*(?:\n|$)/.exec(src);
      return match ? { type: "figurePlaceholder", raw: match[0], caption: match[1] } : undefined;
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode("figurePlaceholder", { caption: token.caption }),
  renderMarkdown: (node) => `{{figure: ${node.attrs?.caption ?? ""}}}`,

  addCommands() {
    return {
      insertFigurePlaceholder:
        (caption: string) =>
        ({ commands }) =>
          commands.insertContent({ type: this.name, attrs: { caption } }),
    };
  },
});

/**
 * The serializer HTML-escapes &, < and > in text ("R&D" → "R&amp;D"). That's valid Markdown, but the
 * rest of the pipeline (word counts, citation scanning, LLM prompts, LaTeX/DOCX export) reads raw
 * text, so turn them back into plain characters wherever that can't change the meaning.
 * Code spans and fenced code blocks are left untouched.
 */
export function cleanMarkdown(markdown: string): string {
  const unescape = (text: string) =>
    text
      .replace(/&amp;(?![A-Za-z0-9#]+;)/g, "&") // not if the result would read as another entity
      .replace(/&lt;(?=[\s\d=])/g, "<") // "a < b", "<= 3": can't start an HTML tag
      .replace(/(?<=\S[^\n]*)&gt;/g, ">"); // not at line start, where ">" means a blockquote
  // Split out fenced blocks and inline code; only unescape the prose parts
  return markdown
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) => (i % 2 === 1 ? part : unescape(part)))
    .join("");
}

export function sectionExtensions(): Extensions {
  return [
    StarterKit.configure({ heading: { levels: [2, 3, 4] }, link: { openOnClick: false } }),
    TableKit.configure({ table: { resizable: false } }),
    Citation,
    FigurePlaceholder,
    Markdown,
  ];
}
