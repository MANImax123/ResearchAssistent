"""Development-only endpoints (registered only when APP_ENV=dev)."""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.prompts.debug import LLM_TEST
from app.core.deps import CurrentUser, SettingsDep
from app.schemas.debug import EmbeddingSummary, IdeaSnapshot, LLMTestRequest, LLMTestResponse, RunSummary
from app.services.embeddings import EmbeddingService, get_embeddings
from app.services.llm import LLMError, LLMNotConfiguredError, LLMService, get_llm

router = APIRouter(prefix="/debug", tags=["debug"])


def _llm() -> LLMService:
    try:
        return get_llm()
    except LLMNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


def _embeddings() -> EmbeddingService | LLMNotConfiguredError:
    # Returned rather than raised: only a problem if the request actually asks for an embedding
    try:
        return get_embeddings()
    except LLMNotConfiguredError as exc:
        return exc


@router.post("/llm-test", response_model=LLMTestResponse)
async def llm_test(
    body: LLMTestRequest,
    user: CurrentUser,
    settings: SettingsDep,
    llm: Annotated[LLMService, Depends(_llm)],
    embedder: Annotated[EmbeddingService | LLMNotConfiguredError, Depends(_embeddings)],
) -> LLMTestResponse:
    """Calls the configured model for a small structured object and records an `agent_runs` row.
    Optionally embeds the idea too, to check the embedding model and its dimensions."""
    try:
        result = await llm.generate_structured(
            LLM_TEST.render(idea=body.idea),
            IdeaSnapshot,
            agent="debug.llm_test",
            system=LLM_TEST.system,
            prompt_id=LLM_TEST.id,
        )
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    embedding = None
    if body.include_embedding:
        if isinstance(embedder, LLMNotConfiguredError):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(embedder))
        try:
            vector = await embedder.embed_query(body.idea)
        except Exception as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Embedding call failed: {exc}") from exc
        embedding = EmbeddingSummary(
            provider=embedder.provider.name,
            model=settings.embedding_model,
            dimensions=len(vector),
            norm=round(math.sqrt(sum(v * v for v in vector)), 6),
        )

    run = result.run
    return LLMTestResponse(
        data=result.data,
        run=RunSummary(
            agent_run_id=run.run_id,
            provider=run.provider,
            model=run.model,
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            latency_ms=run.latency_ms,
        ),
        embedding=embedding,
    )
