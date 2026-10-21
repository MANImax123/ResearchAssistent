"""L4 Document Parsing: get each paper's PDF (legal open access only), parse it, chunk it.

Per paper: PDF (uploaded, or fetched from arXiv / the reported open-access location / Unpaywall) →
GROBID full-text TEI → sections, captions, limitations (PyMuPDF if GROBID fails) → section-bounded
chunks for retrieval (embedded in Step 4.4). `parse_status` tracks every step; a paper without any
open-access PDF becomes "upload needed" with what was tried.
"""

import asyncio
import io
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import delete

from app.core.logging import get_logger
from app.db.models import File, Paper, PaperChunk, PaperDocument
from app.db.models.enums import FileKind, ParseStatus
from app.services.grobid import GrobidClient, GrobidError, parse_tei
from app.services.llm import SessionFactory
from app.services.pdf import ParsedDocument, chunk_document, parse_with_pymupdf
from app.services.pdf_fetch import PdfFetcher, PdfSource
from app.services.storage import Storage, object_key

log = get_logger(__name__)

CONCURRENCY = 3
MIN_WORDS = 200  # less text than this: the PDF is probably scanned images

Progress = Callable[[str], Awaitable[None]]


@dataclass
class ParseOutcome:
    paper_id: uuid.UUID
    status: ParseStatus
    detail: str  # e.g. "grobid · 24 sections · 31 chunks" or the reason it failed


@dataclass
class Tools:
    storage: Storage
    fetcher: PdfFetcher
    grobid: GrobidClient


async def _set_status(
    session_factory: SessionFactory, paper_id: uuid.UUID, status: ParseStatus, error: str | None = None
) -> None:
    async with session_factory() as session:
        paper = await session.get(Paper, paper_id)
        if paper is not None:
            paper.parse_status = status
            paper.parse_error = error
            await session.commit()


async def _load_pdf(
    session_factory: SessionFactory, paper: Paper, tools: Tools
) -> tuple[bytes, uuid.UUID, PdfSource, str | None] | str:
    """(pdf, file id, source, url), or the reason none could be obtained."""
    if paper.pdf_file_id is not None:
        async with session_factory() as session:
            file = await session.get(File, paper.pdf_file_id)
            document = await session.get(PaperDocument, paper.id)
        if file is not None:
            reused = document is not None and document.file_id == file.id
            source: PdfSource = document.pdf_source if reused else "upload"  # type: ignore[assignment, union-attr]
            url = document.pdf_url if reused and document else None
            return await tools.storage.get_bytes(file.object_key), file.id, source, url

    result = await tools.fetcher.fetch(arxiv_id=paper.arxiv_id, pdf_url=paper.pdf_url, doi=paper.doi)
    if result.data is None or result.source is None:
        if not result.attempts:
            return "No open-access PDF is known for this paper. Upload the PDF to parse it."
        return f"No open-access PDF could be downloaded ({'; '.join(result.attempts)}). Upload the PDF to parse it."

    file_id = uuid.uuid4()
    name = f"{(paper.doi or paper.arxiv_id or str(paper.id)).replace('/', '_')}.pdf"
    key = object_key(paper.project_id, file_id, name)
    await tools.storage.put(key, io.BytesIO(result.data), len(result.data), "application/pdf")
    async with session_factory() as session:
        session.add(File(id=file_id, project_id=paper.project_id, kind=FileKind.PAPER_PDF, filename=name,
                         object_key=key, mime="application/pdf", size=len(result.data), uploaded_by=None))  # fmt: skip
        row = await session.get(Paper, paper.id)
        if row is not None:
            row.pdf_file_id = file_id
        await session.commit()
    return result.data, file_id, result.source, result.url


async def _parse(pdf: bytes, tools: Tools) -> ParsedDocument:
    try:
        document = parse_tei(await tools.grobid.process(pdf))
        if sum(len(s.text.split()) for s in document.sections) >= MIN_WORDS:
            return document
        log.warning("grobid_little_text", words=sum(len(s.text.split()) for s in document.sections))
    except (GrobidError, ET.ParseError) as exc:
        log.warning("grobid_failed_falling_back", error=str(exc)[:200])
    return parse_with_pymupdf(pdf)


async def parse_paper(paper_id: uuid.UUID, session_factory: SessionFactory, tools: Tools) -> ParseOutcome:
    async with session_factory() as session:
        paper = await session.get(Paper, paper_id)
    if paper is None:
        return ParseOutcome(paper_id, ParseStatus.FAILED, "Paper not found")
    await _set_status(session_factory, paper_id, ParseStatus.PARSING)
    try:
        loaded = await _load_pdf(session_factory, paper, tools)
        if isinstance(loaded, str):
            await _set_status(session_factory, paper_id, ParseStatus.UPLOAD_NEEDED, loaded)
            return ParseOutcome(paper_id, ParseStatus.UPLOAD_NEEDED, loaded)
        pdf, file_id, source, url = loaded
        try:
            document = await _parse(pdf, tools)
        except Exception as exc:  # noqa: BLE001 - a broken PDF fails this paper, not the whole job
            reason = f"The PDF could not be read ({type(exc).__name__})."
            await _set_status(session_factory, paper_id, ParseStatus.FAILED, reason)
            return ParseOutcome(paper_id, ParseStatus.FAILED, reason)
        words = sum(len(s.text.split()) for s in document.sections)
        if words < MIN_WORDS:
            reason = "Almost no text could be extracted: the PDF may be scanned images."
            await _set_status(session_factory, paper_id, ParseStatus.FAILED, reason)
            return ParseOutcome(paper_id, ParseStatus.FAILED, reason)

        chunks = chunk_document(document)
        parsed = document.to_json()
        async with session_factory() as session:
            await session.execute(delete(PaperChunk).where(PaperChunk.paper_id == paper_id))
            await session.execute(delete(PaperDocument).where(PaperDocument.paper_id == paper_id))
            session.add(
                PaperDocument(
                    paper_id=paper_id, file_id=file_id, pdf_source=source, pdf_url=url, parser=document.parser,
                    page_count=document.page_count, title=document.title, abstract=document.abstract,
                    sections=parsed["sections"], captions=parsed["captions"], limitations=parsed["limitations"],
                    reference_count=document.reference_count,
                )
            )  # fmt: skip
            for index, chunk in enumerate(chunks):
                session.add(
                    PaperChunk(paper_id=paper_id, chunk_index=index, section_name=chunk.section[:255],
                               text=chunk.text, page=chunk.page)
                )  # fmt: skip
            row = await session.get(Paper, paper_id)
            if row is not None:
                row.parse_status = ParseStatus.PARSED
                row.parse_error = None
                if not row.abstract and document.abstract:
                    row.abstract = document.abstract  # the PDF fills an abstract the sources didn't have
            await session.commit()
        detail = f"{document.parser} · {len(document.sections)} sections · {len(chunks)} chunks"
        log.info("paper_parsed", paper_id=str(paper_id), source=source, detail=detail)
        return ParseOutcome(paper_id, ParseStatus.PARSED, detail)
    except Exception as exc:
        log.exception("paper_parse_failed", paper_id=str(paper_id))
        reason = f"Parsing failed ({type(exc).__name__}). Try again."
        await _set_status(session_factory, paper_id, ParseStatus.FAILED, reason)
        return ParseOutcome(paper_id, ParseStatus.FAILED, reason)


async def parse_papers(
    paper_ids: list[uuid.UUID],
    session_factory: SessionFactory,
    tools: Tools,
    progress: Callable[[int, str], Awaitable[None]] | None = None,
) -> list[ParseOutcome]:
    """Parse several papers (a few at a time); every paper is queued ("pending") first so the UI shows it."""
    async with session_factory() as session:
        for pid in paper_ids:
            paper = await session.get(Paper, pid)
            if paper is not None:
                paper.parse_status = ParseStatus.PENDING
                paper.parse_error = None
        await session.commit()

    limit = asyncio.Semaphore(CONCURRENCY)
    done = 0
    outcomes: list[ParseOutcome] = []

    async def run(pid: uuid.UUID) -> None:
        nonlocal done
        async with limit:
            outcome = await parse_paper(pid, session_factory, tools)
        outcomes.append(outcome)
        done += 1
        if progress is not None:
            await progress(5 + int(90 * done / max(len(paper_ids), 1)), f"Processed {done}/{len(paper_ids)} papers")

    await asyncio.gather(*(run(pid) for pid in paper_ids))
    return outcomes
