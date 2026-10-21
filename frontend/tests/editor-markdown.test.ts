import { Editor } from "@tiptap/core";
import { afterEach, describe, expect, it } from "vitest";

import { cleanMarkdown, sectionExtensions } from "@/components/editor/extensions";

let editor: Editor | null = null;

function load(markdown: string): Editor {
  editor = new Editor({ extensions: sectionExtensions(), content: markdown, contentType: "markdown" });
  return editor;
}

afterEach(() => {
  editor?.destroy();
  editor = null;
});

const SAMPLE = `## Related work

Transformers [@vaswani2017] replaced recurrence; BERT [@devlin2019] added pre-training.

- **Encoder** models
- *Decoder* models

{{figure: Model overview}}

| Model | Accuracy |
| --- | --- |
| Baseline | 0.81 |
| Ours | 0.87 |`;

describe("section markdown", () => {
  it("parses citations into chips and figures into placeholders", () => {
    const doc = load(SAMPLE).getJSON();
    const nodes = JSON.stringify(doc);

    expect(nodes).toContain('"type":"citation","attrs":{"key":"vaswani2017"}');
    expect(nodes).toContain('"type":"citation","attrs":{"key":"devlin2019"}');
    expect(nodes).toContain('"type":"figurePlaceholder","attrs":{"caption":"Model overview"}');
    expect(nodes).toContain('"type":"table"');
  });

  it("round-trips markdown without losing structure", () => {
    const out = load(SAMPLE).getMarkdown();

    expect(out).toContain("## Related work");
    expect(out).toContain("Transformers [@vaswani2017] replaced recurrence; BERT [@devlin2019] added pre-training.");
    expect(out).toMatch(/- \*\*Encoder\*\* models/);
    expect(out).toContain("{{figure: Model overview}}");
    expect(out).toMatch(/\| Baseline\s*\| 0\.81\s*\|/);
    // And it's stable: parsing the output again gives the same markdown
    editor?.destroy();
    expect(load(out).getMarkdown()).toBe(out);
  });

  it("insert commands produce the stored syntax", () => {
    const e = load("Intro");
    e.commands.focus("end");
    e.commands.insertCitation("smith2020");
    e.commands.insertFigurePlaceholder("Results chart");

    const out = e.getMarkdown();
    expect(out).toContain("[@smith2020]");
    expect(out).toContain("{{figure: Results chart}}");
  });

  it("stores &, < and > as plain characters, stably", () => {
    const cases: [string, string][] = [
      ["Idea & Problem", "Idea & Problem"],
      ["AT&T and R&D", "AT&T and R&D"],
      ["a < b > c and x <= 3", "a < b > c and x <= 3"],
      ["**bold & co**", "**bold & co**"],
      ["`code & more <b>`", "`code & more <b>`"],
    ];
    for (const [input, expected] of cases) {
      const once = cleanMarkdown(load(input).getMarkdown());
      expect(once.trim()).toBe(expected);
      editor?.destroy();
      expect(cleanMarkdown(load(once).getMarkdown())).toBe(once); // stable on reload
      editor?.destroy();
    }
  });

  it("keeps escapes where they change meaning", () => {
    // "<b>" typed as text must not become an HTML tag; a leading ">" must not become a quote
    expect(cleanMarkdown("show &lt;b&gt; literally")).toBe("show &lt;b> literally");
    expect(cleanMarkdown("&gt; not a quote")).toBe("&gt; not a quote");
    expect(cleanMarkdown("write &amp;copy; as text")).toBe("write &amp;copy; as text");
    const fenced = "```\nx &amp; y\n```";
    expect(cleanMarkdown(fenced)).toBe(fenced);
  });

  it("leaves ordinary brackets and links alone", () => {
    const out = load("See [the docs](https://example.org) and [not a citation].").getMarkdown();
    expect(out).toContain("[the docs](https://example.org)");
    // Plain brackets may come back escaped (\[...\]) — equivalent Markdown, never a citation
    expect(out.replace(/\\([[\]])/g, "$1")).toContain("[not a citation]");
    expect(JSON.stringify(editor?.getJSON())).not.toContain('"citation"');
    editor?.destroy();
    expect(load(out).getMarkdown()).toBe(out);
  });
});
