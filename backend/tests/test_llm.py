"""LLM gateway: structured parsing, retries, repair, agent_runs logging, providers, prompts, debug endpoint."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts.base import PromptRenderError, PromptTemplate
from app.api.v1 import debug as debug_api
from app.core.config import get_settings
from app.db.models import AgentRun
from app.db.models.enums import RunStatus, StageName
from app.main import app
from app.schemas.debug import IdeaSnapshot
from app.services import llm as llm_module
from app.services.embeddings import EmbeddingService, FakeEmbeddings
from app.services.llm import LLMError, LLMNotConfiguredError, LLMService, inline_refs
from app.services.llm_providers import FakeLLM, GeminiProvider, OpenAIProvider, TransientFakeError, example_from_schema

SNAPSHOT = {
    "working_title": "Grounded multi-agent paper writing",
    "keywords": ["LLM agents", "RAG", "citations"],
    "research_questions": ["Does grounding cut citation errors?", "Do approvals improve quality?"],
    "one_line_summary": "A human-in-the-loop system for writing research papers.",
}


class Author(BaseModel):
    name: str


class Paper(BaseModel):
    title: str
    authors: list[Author] = Field(min_length=1)


def session_factory_for(session: AsyncSession) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


@pytest.fixture
def sleeps() -> list[float]:
    return []


def make_service(db_session: AsyncSession, fake: FakeLLM, sleeps: list[float], **settings_overrides: Any) -> LLMService:
    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    defaults = {
        "llm_model": "main-model",
        "llm_model_fast": "fast-model",
        "llm_model_fallback": "",  # fallback has its own tests
        "llm_max_retries": 3,
        "llm_retries_before_fallback": 1,
    }
    settings = get_settings().model_copy(update={**defaults, **settings_overrides})
    return LLMService(fake, settings, session_factory_for(db_session), sleep=record_sleep)


async def get_run(db_session: AsyncSession, run_id: uuid.UUID) -> AgentRun:
    run = await db_session.scalar(
        select(AgentRun).where(AgentRun.id == run_id).execution_options(populate_existing=True)
    )
    assert run is not None
    return run


# ─── generate_structured / generate_text ──────────────────────────────


async def test_structured_output_parses_and_logs_agent_run(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([SNAPSHOT])
    llm = make_service(db_session, fake, sleeps)
    project_id = None

    result = await llm.generate_structured(
        "Idea: X", IdeaSnapshot, agent="idea", system="be brief", stage=StageName.IDEA, prompt_id="idea.refine@1"
    )

    assert isinstance(result.data, IdeaSnapshot)
    assert result.data.keywords == ["LLM agents", "RAG", "citations"]
    assert fake.calls[0].model == "main-model" and fake.calls[0].system == "be brief"
    assert fake.calls[0].json_schema is not None and fake.calls[0].json_schema["title"] == "IdeaSnapshot"

    run = await get_run(db_session, result.run.run_id)
    assert run.status == RunStatus.SUCCESS
    assert (run.agent, run.stage, run.project_id) == ("idea", StageName.IDEA, project_id)
    assert run.model == "fake/main-model"
    assert run.tokens_in > 0 and run.tokens_out > 0
    assert run.tokens_in == result.run.tokens_in and run.tokens_out == result.run.tokens_out
    assert run.latency_ms is not None and run.latency_ms >= 0
    assert run.input_summary == "Idea: X"
    assert run.output == {"data": SNAPSHOT, "prompt": "idea.refine@1"}
    assert run.error is None


async def test_generate_text_uses_fast_model_when_asked(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM(["Plain prose."])
    llm = make_service(db_session, fake, sleeps)

    result = await llm.generate_text("Write a sentence.", agent="writer", fast=True)

    assert result.text == "Plain prose."
    assert fake.calls[0].model == "fast-model"
    assert fake.calls[0].json_schema is None
    run = await get_run(db_session, result.run.run_id)
    assert run.status == RunStatus.SUCCESS and run.output == {"text": "Plain prose.", "prompt": None}


async def test_code_fenced_json_is_accepted(db_session: AsyncSession, sleeps: list[float]) -> None:
    import json

    llm = make_service(db_session, FakeLLM([f"```json\n{json.dumps(SNAPSHOT)}\n```"]), sleeps)

    result = await llm.generate_structured("x", IdeaSnapshot, agent="t")

    assert result.data.working_title == SNAPSHOT["working_title"]


async def test_invalid_output_is_repaired_once(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([{"working_title": "T"}, SNAPSHOT])
    llm = make_service(db_session, fake, sleeps)

    result = await llm.generate_structured("Idea: X", IdeaSnapshot, agent="t")

    assert len(fake.calls) == 2
    repair = fake.calls[1].prompt
    assert repair.startswith("Idea: X") and "did not match the required JSON schema" in repair
    assert "keywords" in repair  # the validation problems are fed back
    run = await get_run(db_session, result.run.run_id)
    assert run.status == RunStatus.SUCCESS
    assert run.tokens_in > len("Idea: X") // 4 + 1  # tokens from both attempts are counted


async def test_invalid_output_twice_fails_the_run(db_session: AsyncSession, sleeps: list[float]) -> None:
    llm = make_service(db_session, FakeLLM(["not json", {"working_title": "T"}]), sleeps)

    with pytest.raises(LLMError) as exc:
        await llm.generate_structured("x", IdeaSnapshot, agent="t")

    run = await get_run(db_session, exc.value.run_id)  # type: ignore[arg-type]
    assert run.status == RunStatus.FAILED
    assert run.error is not None and run.error.startswith("ValidationError")


async def test_transient_errors_are_retried_with_backoff(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([TransientFakeError("429"), TransientFakeError("503"), SNAPSHOT])
    llm = make_service(db_session, fake, sleeps)

    result = await llm.generate_structured("x", IdeaSnapshot, agent="t")

    assert result.data.working_title == SNAPSHOT["working_title"]
    assert sleeps == [1, 2]
    assert len(fake.calls) == 3


async def test_retries_give_up_after_max(db_session: AsyncSession, sleeps: list[float]) -> None:
    llm = make_service(db_session, FakeLLM([TransientFakeError("down")] * 4), sleeps)

    with pytest.raises(LLMError) as exc:
        await llm.generate_text("x", agent="t")

    assert sleeps == [1, 2, 4]
    run = await get_run(db_session, exc.value.run_id)  # type: ignore[arg-type]
    assert run.status == RunStatus.FAILED and "TransientFakeError" in (run.error or "")


async def test_falls_back_when_main_model_stays_overloaded(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([TransientFakeError("503 high demand"), TransientFakeError("504 deadline"), SNAPSHOT])
    llm = make_service(db_session, fake, sleeps, llm_model_fallback="backup-model")

    result = await llm.generate_structured("x", IdeaSnapshot, agent="t")

    assert [c.model for c in fake.calls] == ["main-model", "main-model", "backup-model"]
    assert sleeps == [1]  # one retry on the main model, then straight to the fallback
    run = await get_run(db_session, result.run.run_id)
    assert run.status == RunStatus.SUCCESS and run.model == "fake/backup-model"  # records who actually answered


async def test_fallback_gets_full_retries_then_fails(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([TransientFakeError("503")] * 6)
    llm = make_service(db_session, fake, sleeps, llm_model_fallback="backup-model")

    with pytest.raises(LLMError):
        await llm.generate_text("x", agent="t")

    assert [c.model for c in fake.calls] == ["main-model"] * 2 + ["backup-model"] * 4
    assert sleeps == [1, 1, 2, 4]


async def test_no_fallback_for_non_transient_errors(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([PermissionError("API key invalid")])
    llm = make_service(db_session, fake, sleeps, llm_model_fallback="backup-model")

    with pytest.raises(LLMError, match="API key invalid"):
        await llm.generate_text("x", agent="t")
    assert [c.model for c in fake.calls] == ["main-model"]


async def test_fast_model_is_not_its_own_fallback(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([TransientFakeError("503")] * 4)
    llm = make_service(db_session, fake, sleeps, llm_model_fallback="fast-model")

    with pytest.raises(LLMError):
        await llm.generate_text("x", agent="t", fast=True)
    assert [c.model for c in fake.calls] == ["fast-model"] * 4  # full retries, no duplicate fallback


async def test_non_retryable_error_fails_immediately(db_session: AsyncSession, sleeps: list[float]) -> None:
    llm = make_service(db_session, FakeLLM([PermissionError("bad key")]), sleeps)

    with pytest.raises(LLMError, match="bad key"):
        await llm.generate_text("x", agent="t")
    assert sleeps == []


async def test_slow_calls_time_out_and_retry(db_session: AsyncSession, sleeps: list[float]) -> None:
    class SlowFake(FakeLLM):
        async def complete(self, **kwargs: Any) -> Any:
            if len(self.calls) == 0:
                self.calls.append(None)  # type: ignore[arg-type]
                await asyncio.sleep(1)
            return await super().complete(**kwargs)

    fake = SlowFake(["done"])
    llm = make_service(db_session, fake, sleeps, llm_timeout_s=0.05)

    result = await llm.generate_text("x", agent="t")

    assert result.text == "done"
    assert sleeps == [1]


async def test_nested_schema_sent_without_refs(db_session: AsyncSession, sleeps: list[float]) -> None:
    fake = FakeLLM([{"title": "P", "authors": [{"name": "A"}]}])
    llm = make_service(db_session, fake, sleeps)

    await llm.generate_structured("x", Paper, agent="t")

    schema = fake.calls[0].json_schema
    assert schema is not None and "$defs" not in str(schema) and "$ref" not in str(schema)
    assert schema["properties"]["authors"]["items"]["properties"]["name"]["type"] == "string"


def test_inline_refs_resolves_nested_definitions() -> None:
    inlined = inline_refs(Paper.model_json_schema())
    assert inlined["properties"]["authors"]["items"]["title"] == "Author"


def test_fake_placeholder_satisfies_schema() -> None:
    example = example_from_schema(inline_refs(IdeaSnapshot.model_json_schema()))
    snapshot = IdeaSnapshot.model_validate(example)
    assert len(snapshot.keywords) == 3 and len(snapshot.research_questions) == 2


def test_missing_api_key_raises_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: get_settings().model_copy(update={"llm_provider": "gemini", "gemini_api_key": ""}),
    )
    llm_module.get_provider.cache_clear()
    try:
        with pytest.raises(LLMNotConfiguredError, match="GEMINI_API_KEY"):
            llm_module.get_provider()
    finally:
        llm_module.get_provider.cache_clear()


# ─── Provider adapters (SDK clients stubbed; no network) ────────────────


async def test_gemini_provider_builds_request_and_counts_thinking_tokens() -> None:
    provider = GeminiProvider("test-key", timeout_s=30)
    captured: dict[str, Any] = {}

    async def generate_content(**kwargs: Any) -> Any:
        captured.update(kwargs)
        usage = SimpleNamespace(prompt_token_count=120, candidates_token_count=40, thoughts_token_count=25)
        return SimpleNamespace(text='{"a": 1}', usage_metadata=usage, model_version="gemini-3.8-flash-001")

    provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}

    completion = await provider.complete(
        model="gemini-3.8-flash", prompt="P", system="S", temperature=0.2, max_output_tokens=500, json_schema=schema
    )

    config = captured["config"]
    assert (captured["model"], captured["contents"]) == ("gemini-3.8-flash", "P")
    assert config.system_instruction == "S" and config.temperature == 0.2 and config.max_output_tokens == 500
    assert config.response_mime_type == "application/json" and config.response_json_schema == schema
    assert (completion.tokens_in, completion.tokens_out, completion.model) == (120, 65, "gemini-3.8-flash-001")


def test_gemini_retryable_errors() -> None:
    from google.genai import errors

    provider = GeminiProvider("test-key", timeout_s=30)
    assert provider.is_retryable(errors.ServerError(503, {"error": {"message": "overloaded"}}))
    assert provider.is_retryable(errors.ClientError(429, {"error": {"message": "rate limited"}}))
    assert not provider.is_retryable(errors.ClientError(400, {"error": {"message": "bad request"}}))
    assert provider.is_retryable(TimeoutError())


async def test_openai_provider_builds_request() -> None:
    provider = OpenAIProvider("test-key", timeout_s=30)
    captured: dict[str, Any] = {}

    async def create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        message = SimpleNamespace(content="hello")
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=3)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model="gpt-5.4")

    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    completion = await provider.complete(
        model="gpt-5.4", prompt="P", system="S", temperature=None, max_output_tokens=None, json_schema={"title": "X"}
    )

    assert captured["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "P"}]
    assert "temperature" not in captured  # some reasoning models reject it; only sent when set
    assert captured["response_format"]["json_schema"]["name"] == "X"
    assert (completion.text, completion.tokens_in, completion.tokens_out) == ("hello", 10, 3)


# ─── Prompt templates ──────────────────────────────────────────────────


def test_prompt_template_renders_and_validates() -> None:
    tpl = PromptTemplate(name="x.test", version="2", template='Idea: $idea\nReturn {"title": "..."} for ${field}.')

    assert tpl.id == "x.test@2"
    assert tpl.variables == {"idea", "field"}
    assert tpl.render(idea="GNNs", field="title") == 'Idea: GNNs\nReturn {"title": "..."} for title.'
    with pytest.raises(PromptRenderError, match="missing"):
        tpl.render(idea="GNNs")
    with pytest.raises(PromptRenderError, match="unexpected"):
        tpl.render(idea="GNNs", field="t", extra=1)


# ─── /debug/llm-test ───────────────────────────────────────────────────


async def _login(client: AsyncClient) -> dict[str, str]:
    body = {"email": "dev@example.com", "password": "s3cret-passw0rd", "name": "Dev"}
    await client.post("/api/v1/auth/register", json=body)
    resp = await client.post("/api/v1/auth/login", data={"username": body["email"], "password": body["password"]})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_debug_llm_test_returns_structured_object_and_writes_agent_run(
    api_client: AsyncClient, db_session: AsyncSession, sleeps: list[float]
) -> None:
    headers = await _login(api_client)
    fake = FakeLLM([SNAPSHOT])
    app.dependency_overrides[debug_api._llm] = lambda: make_service(db_session, fake, sleeps)
    app.dependency_overrides[debug_api._embeddings] = lambda: EmbeddingService(FakeEmbeddings(), get_settings())
    try:
        resp = await api_client.post("/api/v1/debug/llm-test", json={"idea": "Grounded paper writing"}, headers=headers)
    finally:
        app.dependency_overrides.pop(debug_api._llm, None)
        app.dependency_overrides.pop(debug_api._embeddings, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == SNAPSHOT
    assert body["run"]["provider"] == "fake" and body["run"]["tokens_in"] > 0 and body["run"]["tokens_out"] > 0
    assert body["embedding"]["dimensions"] == get_settings().embedding_dim
    assert abs(body["embedding"]["norm"] - 1) < 1e-6
    assert "Grounded paper writing" in fake.calls[0].prompt

    run = await get_run(db_session, uuid.UUID(body["run"]["agent_run_id"]))
    assert (run.agent, run.status) == ("debug.llm_test", RunStatus.SUCCESS)
    assert run.output is not None and run.output["prompt"] == "debug.llm_test@1"


async def test_debug_llm_test_503_when_not_configured(api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = await _login(api_client)

    def not_configured() -> LLMService:
        raise LLMNotConfiguredError("LLM provider 'gemini' is not configured: set GEMINI_API_KEY in .env")

    monkeypatch.setattr(debug_api, "get_llm", not_configured)
    resp = await api_client.post("/api/v1/debug/llm-test", json={}, headers=headers)

    assert resp.status_code == 503
    assert "GEMINI_API_KEY" in resp.json()["detail"]


async def test_debug_llm_test_requires_auth(api_client: AsyncClient) -> None:
    assert (await api_client.post("/api/v1/debug/llm-test", json={})).status_code == 401
