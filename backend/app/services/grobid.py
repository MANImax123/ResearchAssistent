"""GROBID client and TEI → ParsedDocument (Step 4.3).

GROBID's TEI lists sections as flat `<div>`s numbered "4", "4.1", "4.2.1"…; maths glyphs sometimes
come out as tiny unnumbered divs (heading "𝑗"), which are merged back into the previous section.
`<figure>` elements also catch definitions and equations, so only real Figure/Table captions are kept.
"""

import re
import xml.etree.ElementTree as ET

import httpx

from app.core.logging import get_logger
from app.services.pdf import CAPTION, Caption, ParsedDocument, Section, SectionKind, classify, clean

log = get_logger(__name__)

NS = {"t": "http://www.tei-c.org/ns/1.0"}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


class GrobidError(Exception):
    pass


class GrobidClient:
    def __init__(self, base_url: str, timeout_s: float = 180.0, transport: httpx.AsyncBaseTransport | None = None):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    async def process(self, pdf: bytes, filename: str = "paper.pdf") -> str:
        """TEI XML for the PDF's full text (with page coordinates for paragraphs, headings and figures)."""
        data = {"teiCoordinates": ["p", "head", "figure"], "segmentSentences": "0", "consolidateHeader": "0"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    f"{self._base_url}/api/processFulltextDocument",
                    files={"input": (filename, pdf, "application/pdf")},
                    data=data,
                )
        except httpx.HTTPError as exc:
            raise GrobidError(f"GROBID unreachable ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise GrobidError(f"GROBID answered HTTP {response.status_code}: {response.text[:200]}")
        return response.text


def _text(node: ET.Element | None) -> str:
    return clean("".join(node.itertext())) if node is not None else ""


def _page(node: ET.Element | None) -> int | None:
    coords = node.get("coords", "") if node is not None else ""
    head = coords.split(",", 1)[0]
    return int(head) if head.isdigit() else None


def _is_glyph_heading(head: str, number: str | None) -> bool:
    """Maths symbols GROBID mistook for section headings ("𝑗", "𝛽")."""
    return number is None and (len(head) <= 3 or not re.search(r"[A-Za-z]{3,}", head))


def parse_tei(tei: str) -> ParsedDocument:
    root = ET.fromstring(tei)  # noqa: S314 - TEI from our own GROBID container
    title = _text(root.find(".//t:teiHeader//t:titleStmt/t:title", NS)) or None
    abstract = _text(root.find(".//t:profileDesc/t:abstract", NS)) or None

    sections: list[Section] = []
    for div in root.findall(".//t:text/t:body/t:div", NS):
        head = div.find("t:head", NS)
        heading = _text(head)
        number = (head.get("n") or "").strip().rstrip(".") or None if head is not None else None  # "2." → "2"
        paragraphs = [_text(p) for p in div.findall("t:p", NS)]
        text = "\n\n".join(p for p in paragraphs if p)
        if sections and (not heading or _is_glyph_heading(heading, number)):
            sections[-1].text = "\n\n".join(x for x in (sections[-1].text, heading, text) if x)
            continue
        page = _page(head) or _page(div.find("t:p", NS))
        sections.append(Section(title=heading or "Untitled", text=text, number=number, page=page))

    for div in root.findall(".//t:text/t:back/t:div[@type='annex']/t:div", NS):
        heading = _text(div.find("t:head", NS)) or "Appendix"
        text = "\n\n".join(_text(p) for p in div.findall("t:p", NS))
        if text:
            sections.append(Section(title=heading, text=text, kind="appendix", page=_page(div.find("t:head", NS))))

    # Kinds: a subsection without a telling heading inherits its nearest ancestor's ("2.3.1" → "2.3" → "2")
    kinds: dict[str, SectionKind] = {}
    for section in sections:
        if section.kind == "appendix":
            continue
        parent: SectionKind | None = None
        parts = section.number.split(".") if section.number else []
        for depth in range(len(parts) - 1, 0, -1):
            if (ancestor := ".".join(parts[:depth])) in kinds:
                parent = kinds[ancestor]
                break
        section.kind = classify(section.title, parent)
        if section.number:
            kinds[section.number] = section.kind

    captions: list[Caption] = []
    for figure in root.findall(".//t:text//t:figure", NS):
        description = _text(figure.find("t:figDesc", NS))
        fig_head = _text(figure.find("t:head", NS))
        is_table = figure.get("type") == "table"
        match = CAPTION.match(fig_head) or CAPTION.match(description)
        if not description or not (match or is_table):
            continue  # definitions, equations and stray boxes GROBID tagged as figures
        number = match.group(3) if match else (_text(figure.find("t:label", NS)) or "?")
        label = f"{'Table' if is_table else 'Figure'} {number}"
        text = CAPTION.sub("", description, count=1).lstrip(" :.") if CAPTION.match(description) else description
        captions.append(Caption("table" if is_table else "figure", label, text, _page(figure)))

    pages = [p for p in (_page(n) for n in root.iter() if n.get("coords")) if p]
    references = len(root.findall(".//t:back//t:listBibl/t:biblStruct", NS))
    return ParsedDocument("grobid", title, abstract, sections, captions, max(pages, default=None), references)
