"""Embedding service: batching, normalisation, dimension checks, retries, Gemini request shape."""

import math
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import get_settings
from app.services.embeddings import EmbeddingService, FakeEmbeddings, GeminiEmbeddings, Kind
from app.services.llm_providers import PerLoop

DIM = get_settings().embedding_dim


def service(provider: Any, **overrides: Any) -> EmbeddingService:
    sleeps: list[float] = []

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    svc = EmbeddingService(provider, get_settings().model_copy(update=overrides), sleep=record)
    svc.sleeps = sleeps  # type: ignore[attr-defined]
    return svc


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_vectors_have_configured_dim_and_unit_norm() -> None:
    vectors = await service(FakeEmbeddings()).embed_documents(["graph neural networks", "protein folding"])

    assert len(vectors) == 2
    for v in vectors:
        assert len(v) == DIM
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


async def test_fake_embeddings_rank_overlapping_text_higher() -> None:
    svc = service(FakeEmbeddings())
    docs = await svc.embed_documents(
        ["transformers for machine translation", "soil nitrogen in wheat fields", "attention in neural translation"]
    )
    query = await svc.embed_query("neural machine translation")

    scores = [cosine(query, d) for d in docs]
    assert scores[1] < scores[0] and scores[1] < scores[2]
    assert await svc.embed_query("same text") == await svc.embed_query("same text")  # deterministic


async def test_documents_are_batched() -> None:
    fake = FakeEmbeddings()
    vectors = await service(fake, embedding_batch_size=100).embed_documents([f"text {i}" for i in range(250)])

    assert len(vectors) == 250
    assert fake.calls == [(100, "document"), (100, "document"), (50, "document")]


async def test_empty_input_makes_no_call() -> None:
    fake = FakeEmbeddings()
    assert await service(fake).embed_documents([]) == []
    assert fake.calls == []


async def test_wrong_dimension_is_rejected() -> None:
    class WrongDim(FakeEmbeddings):
        async def embed(self, texts: list[str], *, kind: Kind, dim: int, titles: list[str | None]) -> list[list[float]]:
            return [[1.0] * (dim - 1) for _ in texts]

    with pytest.raises(ValueError, match="expected EMBEDDING_DIM"):
        await service(WrongDim()).embed_query("x")


async def test_transient_errors_are_retried() -> None:
    class Flaky(FakeEmbeddings):
        failures = 2

        async def embed(self, texts: list[str], *, kind: Kind, dim: int, titles: list[str | None]) -> list[list[float]]:
            if self.failures:
                self.failures -= 1
                raise ConnectionError("reset")
            return await super().embed(texts, kind=kind, dim=dim, titles=titles)

        def is_retryable(self, exc: BaseException) -> bool:
            return isinstance(exc, ConnectionError)

    svc = service(Flaky())
    assert len(await svc.embed_query("x")) == DIM
    assert svc.sleeps == [1, 2]  # type: ignore[attr-defined]


async def test_quota_errors_wait_as_long_as_the_api_asks() -> None:
    """Gemini's free tier allows 100 embedded texts per minute and says how long to wait (live, Step 4.2)."""

    class Quota(FakeEmbeddings):
        failures = 2

        async def embed(self, texts: list[str], *, kind: Kind, dim: int, titles: list[str | None]) -> list[list[float]]:
            if self.failures == 2:
                self.failures -= 1
                raise RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded ... Please retry in 47.372185136s.")
            if self.failures == 1:
                self.failures -= 1
                raise RuntimeError("429 {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '300s'}")
            return await super().embed(texts, kind=kind, dim=dim, titles=titles)

        def is_retryable(self, exc: BaseException) -> bool:
            return True

    svc = service(Quota())
    assert len(await svc.embed_query("x")) == DIM
    assert svc.sleeps == [48.372185136, 90.0]  # type: ignore[attr-defined]  # capped at 90 s


async def test_gemini_embeds_each_text_separately_with_task_prefixes() -> None:
    """gemini-embedding-2 merges a plain list of strings into ONE vector, so each text must be its own Content."""
    provider = GeminiEmbeddings("test-key", "gemini-embedding-2", timeout_s=30)
    captured: dict[str, Any] = {}

    async def embed_content(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * DIM) for _ in kwargs["contents"]])

    provider._clients = PerLoop(lambda: SimpleNamespace(models=SimpleNamespace(embed_content=embed_content)))
    svc = service(provider)

    await svc.embed_documents(["Attention is all you need.", "BERT."], titles=["Transformers", None])
    texts = [c.parts[0].text for c in captured["contents"]]
    assert texts == ["title: Transformers | text: Attention is all you need.", "title: none | text: BERT."]
    assert captured["config"].output_dimensionality == DIM
    assert captured["config"].task_type is None

    await svc.embed_query("which datasets?")
    assert [c.parts[0].text for c in captured["contents"]] == ["task: search result | query: which datasets?"]


async def test_legacy_gemini_model_uses_task_type() -> None:
    provider = GeminiEmbeddings("test-key", "gemini-embedding-001", timeout_s=30)
    captured: dict[str, Any] = {}

    async def embed_content(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * DIM)])

    provider._clients = PerLoop(lambda: SimpleNamespace(models=SimpleNamespace(embed_content=embed_content)))
    await service(provider).embed_query("q")

    assert captured["config"].task_type == "RETRIEVAL_QUERY"
    assert captured["contents"][0].parts[0].text == "q"
