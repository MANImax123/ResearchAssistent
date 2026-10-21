"""Find and download a paper's PDF from legal open-access sources (Step 4.3).

Tried in order: arXiv (the paper's arXiv id), the open-access location a search source reported
(Semantic Scholar `openAccessPdf`, OpenAlex `best_oa_location`), then Unpaywall's best OA PDF for the
DOI (needs `UNPAYWALL_EMAIL`). Nothing else: no scraping of publisher pages, no shadow libraries.
A download only counts if it really is a PDF (publishers often answer with an HTML landing page).
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

import httpx

from app.core.logging import get_logger
from app.services.academic.http import USER_AGENT

log = get_logger(__name__)

PdfSource = Literal["upload", "arxiv", "open_access", "unpaywall"]
# Hosts that ask for slow, spaced requests (arXiv: one every 3 s)
HOST_SPACING_S = {"arxiv.org": 3.0, "export.arxiv.org": 3.0}
DEFAULT_SPACING_S = 0.5


@dataclass
class Candidate:
    source: PdfSource
    url: str


@dataclass
class FetchResult:
    data: bytes | None
    source: PdfSource | None
    url: str | None
    attempts: list[str] = field(default_factory=list)  # "arxiv: HTTP 404", "open_access: not a PDF (text/html)"


class PdfFetcher:
    def __init__(
        self,
        *,
        unpaywall_email: str = "",
        max_bytes: int = 50 * 1024 * 1024,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._email = unpaywall_email
        self._max_bytes = max_bytes
        self._timeout = timeout_s
        self._transport = transport
        self._last: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def _space(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        async with self._lock:
            wait = self._last.get(host, 0.0) + HOST_SPACING_S.get(host, DEFAULT_SPACING_S) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
            headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.5"},
        )

    def candidates(self, *, arxiv_id: str | None, pdf_url: str | None) -> list[Candidate]:
        """Locations known without asking anyone (Unpaywall is only asked if these fail)."""
        found: list[Candidate] = []
        if arxiv_id:
            found.append(Candidate("arxiv", f"https://arxiv.org/pdf/{arxiv_id}"))
        if pdf_url and not (arxiv_id and "arxiv.org" in pdf_url):
            found.append(Candidate("open_access", pdf_url))
        return found

    async def _unpaywall(self, doi: str) -> str | None:
        url = f"https://api.unpaywall.org/v2/{doi}"
        try:
            await self._space(url)
            async with self._client() as client:
                response = await client.get(url, params={"email": self._email})
            if response.status_code != 200:
                return None
            best = (response.json() or {}).get("best_oa_location") or {}
            pdf = best.get("url_for_pdf")
            return pdf if isinstance(pdf, str) and pdf else None
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("unpaywall_failed", doi=doi, error=type(exc).__name__)
            return None

    async def download(self, url: str) -> tuple[bytes | None, str]:
        """(bytes, "") for a real PDF, else (None, why not)."""
        try:
            await self._space(url)
            async with self._client() as client, client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None, f"HTTP {response.status_code}"
                declared = int(response.headers.get("content-length") or 0)
                if declared > self._max_bytes:
                    return None, f"too large ({declared // (1024 * 1024)} MB)"
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        return None, "too large"
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data.lstrip()[:5].startswith(b"%PDF-"):
                    kind = response.headers.get("content-type", "unknown").split(";")[0]
                    return None, f"not a PDF ({kind})"
                return data, ""
        except httpx.HTTPError as exc:
            return None, f"network error ({type(exc).__name__})"

    async def fetch(self, *, arxiv_id: str | None, pdf_url: str | None, doi: str | None) -> FetchResult:
        result = FetchResult(None, None, None)
        for candidate in self.candidates(arxiv_id=arxiv_id, pdf_url=pdf_url):
            if await self._try(candidate, result):
                return result
        if doi and self._email and (url := await self._unpaywall(doi)):
            await self._try(Candidate("unpaywall", url), result)
        return result

    async def _try(self, candidate: Candidate, result: FetchResult) -> bool:
        data, problem = await self.download(candidate.url)
        if data is None:
            result.attempts.append(f"{candidate.source}: {problem}")
            return False
        result.data, result.source, result.url = data, candidate.source, candidate.url
        return True
