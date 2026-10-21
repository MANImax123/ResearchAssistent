"""Embeddings for RAG: batched, retried, dimension-checked and L2-normalised.

Vectors must have exactly EMBEDDING_DIM values (the `paper_chunks.embedding` column).
Documents and queries are embedded differently (asymmetric retrieval), so callers say which.
"""

import asyncio
import hashlib
import math
import re
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Literal, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.llm import LLMNotConfiguredError
from app.services.llm_providers import PerLoop, is_network_error

log = get_logger(__name__)

Kind = Literal["document", "query"]


class EmbeddingProvider(Protocol):
    name: str

    async def embed(self, texts: list[str], *, kind: Kind, dim: int, titles: list[str | None]) -> list[list[float]]: ...

    def is_retryable(self, exc: BaseException) -> bool: ...


class GeminiEmbeddings:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_s: float) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._model = model
        http = types.HttpOptions(timeout=int(timeout_s * 1000))
        self._clients = PerLoop(lambda: genai.Client(api_key=api_key, http_options=http).aio)
        # gemini-embedding-001 takes a task_type; newer models take the task as a text prefix instead
        self._legacy = model.startswith("gemini-embedding-001")

    async def embed(self, texts: list[str], *, kind: Kind, dim: int, titles: list[str | None]) -> list[list[float]]:
        types = self._types
        if self._legacy:
            task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
            inputs = texts
            config = types.EmbedContentConfig(output_dimensionality=dim, task_type=task)
        else:
            inputs = [_task_prefix(t, kind, title) for t, title in zip(texts, titles, strict=True)]
            config = types.EmbedContentConfig(output_dimensionality=dim)
        # One Content per text: a plain list of strings would be merged into a single embedding
        contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in inputs]
        resp = await self._clients.get().models.embed_content(model=self._model, contents=contents, config=config)
        return [list(e.values or []) for e in (resp.embeddings or [])]

    def is_retryable(self, exc: BaseException) -> bool:
        from google.genai import errors

        if isinstance(exc, errors.ServerError):
            return True
        if isinstance(exc, errors.ClientError):
            return exc.code in (408, 429)
        return is_network_error(exc)


def _task_prefix(text: str, kind: Kind, title: str | None) -> str:
    if kind == "query":
        return f"task: search result | query: {text}"
    return f"title: {title or 'none'} | text: {text}"


class OpenAIEmbeddings:
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout_s: float) -> None:
        import openai

        self._openai = openai
        self._model = model
        self._clients = PerLoop(lambda: openai.AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=0))

    async def embed(self, texts: list[str], *, kind: Kind, dim: int, titles: list[str | None]) -> list[list[float]]:
        resp = await self._clients.get().embeddings.create(model=self._model, input=texts, dimensions=dim)
        return [list(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]

    def is_retryable(self, exc: BaseException) -> bool:
        o = self._openai
        return isinstance(exc, o.APITimeoutError | o.APIConnectionError | o.RateLimitError | o.InternalServerError)


class FakeEmbeddings:
    """Deterministic hashed bag-of-words vectors: texts sharing words end up close together,
    which keeps retrieval tests meaningful without an API."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[int, Kind]] = []

    async def embed(self, texts: list[str], *, kind: Kind, dim: int, titles: list[str | None]) -> list[list[float]]:
        self.calls.append((len(texts), kind))
        return [_hashed_bow(t, dim) for t in texts]

    def is_retryable(self, exc: BaseException) -> bool:
        return False


def _hashed_bow(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    if not any(vec):
        vec[0] = 1.0
    return vec


def normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class EmbeddingService:
    def __init__(
        self, provider: EmbeddingProvider, settings: Settings, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        self.provider = provider
        self.settings = settings
        self._sleep = sleep

    @property
    def dim(self) -> int:
        return self.settings.embedding_dim

    async def embed_documents(self, texts: list[str], titles: list[str | None] | None = None) -> list[list[float]]:
        titles = titles or [None] * len(texts)
        if len(titles) != len(texts):
            raise ValueError("titles must match texts one-to-one")
        vectors: list[list[float]] = []
        size = self.settings.embedding_batch_size
        for start in range(0, len(texts), size):
            vectors += await self._embed(texts[start : start + size], "document", titles[start : start + size])
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "query", [None]))[0]

    async def _embed(self, texts: list[str], kind: Kind, titles: list[str | None]) -> list[list[float]]:
        if not texts:
            return []
        attempts = self.settings.llm_max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                vectors = await self.provider.embed(texts, kind=kind, dim=self.dim, titles=titles)
                break
            except Exception as exc:
                if attempt == attempts or not self.provider.is_retryable(exc):
                    raise
                delay = min(2 ** (attempt - 1), 30)
                log.warning(
                    "embedding_retry", attempt=attempt, delay_s=delay, error=f"{type(exc).__name__}: {exc}"[:300]
                )
                await self._sleep(delay)
        if len(vectors) != len(texts):
            raise ValueError(f"Expected {len(texts)} embeddings, got {len(vectors)}")
        for v in vectors:
            if len(v) != self.dim:
                raise ValueError(f"Embedding has {len(v)} dimensions, expected EMBEDDING_DIM={self.dim}")
        return [normalize(v) for v in vectors]


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    s = get_settings()
    if s.embedding_provider == "fake":
        return FakeEmbeddings()
    if s.embedding_provider == "gemini":
        if not s.gemini_api_key:
            raise LLMNotConfiguredError("Embedding provider 'gemini' is not configured: set GEMINI_API_KEY in .env")
        return GeminiEmbeddings(s.gemini_api_key, s.embedding_model, s.llm_timeout_s)
    if not s.openai_api_key:
        raise LLMNotConfiguredError("Embedding provider 'openai' is not configured: set OPENAI_API_KEY in .env")
    return OpenAIEmbeddings(s.openai_api_key, s.embedding_model, s.llm_timeout_s)


def get_embeddings() -> EmbeddingService:
    return EmbeddingService(get_embedding_provider(), get_settings())
