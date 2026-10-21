"""Parsed paper documents: the shared structure GROBID and the PyMuPDF fallback produce, section
classification, and chunking for retrieval (Step 4.3).

A document is a title, an abstract, a list of sections (number, title, kind, first page, text) and
the figure/table captions. Section kinds let the UI and later agents find a paper's methodology,
datasets, results and limitations without re-reading the whole text.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SectionKind = Literal[
    "introduction", "related_work", "methodology", "dataset", "results", "discussion", "limitations",
    "conclusion", "appendix", "other",
]  # fmt: skip

# Checked in order: the first matching pattern decides (dataset before results: "Experimental setup")
_KIND_PATTERNS: list[tuple[SectionKind, re.Pattern[str]]] = [
    ("limitations", re.compile(r"limitation|threats? to validity|shortcoming")),
    ("introduction", re.compile(r"^introduction|^motivation")),
    ("related_work", re.compile(r"related work|background|literature|prior work|previous work|state of the art")),
    ("dataset", re.compile(r"data ?sets?|\bdata\b|benchmarks?|corpus|corpora|experiment(al)? (setup|settings?)|"
                           r"implementation details|materials")),
    ("conclusion", re.compile(r"conclusion|concluding|summary|future work")),
    ("results", re.compile(r"results?|experiments?|evaluation|performance|comparison|ablation|analysis|case stud")),
    ("discussion", re.compile(r"discussion")),
    ("methodology", re.compile(r"method|approach|model|framework|architecture|proposed|preliminar|algorithm|"
                               r"design|overview|formulation|problem (definition|statement)|system|pipeline|training")),
]  # fmt: skip
# "Appendix B", "Supplementary material", or a lettered heading like "A.4 Baseline Descriptions"
# (the letter check is case-sensitive, so "a novel model" isn't an appendix)
_APPENDIX_NAME = re.compile(r"^(appendix|supplementary)", re.IGNORECASE)
_APPENDIX_LETTER = re.compile(r"^[A-H](\.\d+)*\s+[A-Z]")

CAPTION = re.compile(r"^(fig(ure)?\.?|table)\s*(\d+|[ivx]+)\b", re.IGNORECASE)
LIMITATION_WORDS = re.compile(r"\blimitations?\b|\bshortcomings?\b|\bdoes not (handle|address)|\bfuture work\b", re.I)
# "To overcome the limitations of prior methods…" is about other work, not this paper's limitations
PRIOR_WORK_LIMITATION = re.compile(
    r"(overcome|address|tackle|alleviate|mitigate|remedy)\w*\s+(\w+\s+){0,2}limitations?", re.IGNORECASE
)
LIMITATION_SECTIONS = ("limitations", "conclusion", "discussion")


@dataclass
class Section:
    title: str
    text: str
    number: str | None = None  # "4.2" as printed in the paper
    kind: SectionKind = "other"
    page: int | None = None  # first page (1-based)

    @property
    def heading(self) -> str:
        return f"{self.number} {self.title}" if self.number else self.title


@dataclass
class Caption:
    kind: Literal["figure", "table"]
    label: str  # "Figure 3", "Table 2"
    text: str
    page: int | None = None


@dataclass
class ParsedDocument:
    parser: Literal["grobid", "pymupdf"]
    title: str | None
    abstract: str | None
    sections: list[Section]
    captions: list[Caption] = field(default_factory=list)
    page_count: int | None = None
    reference_count: int = 0

    def limitation_mentions(self, max_items: int = 8) -> list[dict[str, Any]]:
        """The paper's own limitations: its Limitations section, else limitation sentences in the
        conclusion/discussion. Sentences about overcoming *other* work's limitations are skipped."""
        found: list[dict[str, Any]] = []
        for section in self.sections:
            if section.kind not in LIMITATION_SECTIONS:
                continue
            whole_section = section.kind == "limitations"
            for sentence in re.split(r"(?<=[.!?])\s+", section.text):
                relevant = whole_section or (
                    LIMITATION_WORDS.search(sentence) and not PRIOR_WORK_LIMITATION.search(sentence)
                )
                if relevant and 30 <= len(sentence) <= 600:
                    found.append({"section": section.heading, "text": sentence.strip(), "page": section.page})
                    if len(found) >= max_items:
                        return found
        return found

    def to_json(self) -> dict[str, Any]:
        return {
            "sections": [{**asdict(s), "heading": s.heading} for s in self.sections],
            "captions": [asdict(c) for c in self.captions],
            "limitations": self.limitation_mentions(),
        }


def classify(title: str, parent_kind: SectionKind | None = None) -> SectionKind:
    """Kind of a section from its heading; subsections without a telling heading inherit their parent's."""
    title = title.strip()
    if _APPENDIX_NAME.match(title) or _APPENDIX_LETTER.match(title):
        return "appendix"
    heading = re.sub(r"^(?:[\d.]+|[ivx]+\.)\s+", "", title.lower())  # "3.2 Datasets", "IV. RESULTS"
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(heading):
            return kind
    return parent_kind or "other"


def clean(text: str) -> str:
    text = text.replace("­", "")  # soft hyphens
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # words hyphenated across lines
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


# ─── PyMuPDF fallback ────────────────────────────────────────────────────

_NUMBERED_HEADING = re.compile(r"^((?:\d+\.)*\d+\.?|[IVX]+\.)\s+([A-Z][^\n]{2,80})$")
_NAMED_HEADINGS = re.compile(
    r"^(abstract|introduction|related work|background|methods?|methodology|materials and methods|experiments?|"
    r"results( and discussion)?|discussion|limitations|conclusions?|references|bibliography|acknowledge?ments?)$",
    re.IGNORECASE,
)
_REFERENCES = re.compile(r"^(references|bibliography)$", re.IGNORECASE)


def parse_with_pymupdf(data: bytes) -> ParsedDocument:
    """Fallback when GROBID is unavailable or fails: headings by numbering/known names, captions by prefix."""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[no-untyped-call]  # untyped library
        pages = [page.get_text() for page in doc]
        meta_title = (doc.metadata or {}).get("title") or None
    sections: list[Section] = []
    captions: list[Caption] = []
    abstract: str | None = None
    current = Section(title="Front matter", text="", page=1)
    in_references = False
    reference_count = 0
    for page_no, page_text in enumerate(pages, start=1):
        for line in clean(page_text).split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if in_references:
                reference_count += bool(re.match(r"^\[\d+\]|^\d+\.\s", stripped))
                continue
            heading = _NUMBERED_HEADING.match(stripped)
            named = _NAMED_HEADINGS.match(stripped.rstrip(":."))
            if heading or named:
                if current.text.strip():
                    sections.append(current)
                if named and _REFERENCES.match(stripped.rstrip(":.")):
                    in_references = True
                    current = Section(title="References", text="")  # not body text; never added
                    continue
                number, title = (heading.group(1).rstrip("."), heading.group(2)) if heading else (None, stripped)
                current = Section(title=title.strip().rstrip(":."), text="", number=number, page=page_no)
                continue
            if CAPTION.match(stripped):
                label = CAPTION.match(stripped)
                assert label is not None
                kind: Literal["figure", "table"] = "table" if stripped.lower().startswith("table") else "figure"
                captions.append(Caption(kind, f"{'Table' if kind == 'table' else 'Figure'} {label.group(3)}",
                                        stripped, page_no))  # fmt: skip
            current.text += (" " if current.text else "") + stripped
    if current.text.strip():
        sections.append(current)

    parent: SectionKind | None = None
    for section in sections:
        top_level = section.number is None or "." not in section.number
        section.kind = classify(section.title, None if top_level else parent)
        if top_level:
            parent = section.kind
        if section.title.lower() == "abstract":
            abstract = section.text
    title = meta_title if meta_title and len(meta_title) > 8 else None
    body = [s for s in sections if s.title.lower() != "abstract"]
    return ParsedDocument("pymupdf", title, abstract, body, captions, len(pages), reference_count)


# ─── Chunking ────────────────────────────────────────────────────────────

TARGET_WORDS = 450  # ≈ 600 tokens (1 token ≈ 0.75 words)
MAX_WORDS = 600  # ≈ 800 tokens
OVERLAP_WORDS = 60  # ≈ 80 tokens


@dataclass
class TextChunk:
    section: str
    text: str
    page: int | None


def chunk_document(document: ParsedDocument) -> list[TextChunk]:
    """~500–800-token chunks that never cross a section; consecutive chunks overlap by ~80 tokens.

    The abstract is its own chunk; references are never chunked (they aren't in `sections`).
    """
    chunks: list[TextChunk] = []
    if document.abstract:
        chunks.append(TextChunk("Abstract", document.abstract, 1))
    for section in document.sections:
        words = section.text.split()
        if not words:
            continue
        if len(words) <= MAX_WORDS:
            chunks.append(TextChunk(section.heading, " ".join(words), section.page))
            continue
        start = 0
        while start < len(words):
            end = min(start + TARGET_WORDS, len(words))
            if len(words) - end < TARGET_WORDS // 3:  # don't leave a tiny tail: absorb it (stays ≤ MAX_WORDS)
                end = len(words) if len(words) - start <= MAX_WORDS else end
            chunks.append(TextChunk(section.heading, " ".join(words[start:end]), section.page))
            if end == len(words):
                break
            start = end - OVERLAP_WORDS
    return chunks
