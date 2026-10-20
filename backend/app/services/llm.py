"""The single gateway for LLM calls (A3). Agents call `generate_text` / `generate_structured`; nothing
else talks to a model provider.

Every call:
  * writes an `agent_runs` row (running → success | failed) with model, tokens, latency, output;
  * retries transient provider errors (timeouts, 429, 5xx) with exponential backoff;
  * for structured output, validates against the Pydantic schema and retries once with the
    validation error fed back to the model.
"""

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import AgentRun
from app.db.models.enums import RunStatus, StageName
from app.services.llm_providers import FakeLLM, GeminiProvider, LLMProvider, OpenAIProvider

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

INPUT_SUMMARY_CHARS = 1000
STRUCTURED_REPAIR_ATTEMPTS = 1


class LLMError(Exception):
    def __init__(self, message: str, run_id: uuid.UUID | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id


class LLMNotConfiguredError(LLMError):
    pass


@dataclass(frozen=True)
class RunInfo:
    run_id: uuid.UUID
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


@dataclass(frozen=True)
class TextResult:
    text: str
    run: RunInfo


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    data: T
    run: RunInfo


@dataclass
class _Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


class LLMService:
    def __init__(
        self,
        provider: LLMProvider,
        settings: Settings,
        session_factory: SessionFactory,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self._session_factory = session_factory
        self._sleep = sleep

    async def generate_text(
        self,
        prompt: str,
        *,
        agent: str,
        system: str | None = None,
        fast: bool = False,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        project_id: uuid.UUID | None = None,
        stage: StageName | None = None,
        prompt_id: str | None = None,
    ) -> TextResult:
        """Free-text generation (prose sections only, A3)."""
        model = self._model(fast)
        run_id = await self._start_run(agent, stage, project_id, model, prompt)
        usage = _Usage(model=model)
        started = time.perf_counter()
        try:
            completion = await self._complete_with_retries(model, prompt, system, temperature, max_output_tokens, None)
            usage = _Usage(completion.tokens_in, completion.tokens_out, completion.model)
        except Exception as exc:
            await self._fail_run(run_id, exc, usage, started)
            raise LLMError(f"LLM call failed: {exc}", run_id) from exc
        run = await self._finish_run(run_id, {"text": completion.text, "prompt": prompt_id}, usage, started)
        return TextResult(text=completion.text, run=run)

    async def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        agent: str,
        system: str | None = None,
        fast: bool = False,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        project_id: uuid.UUID | None = None,
        stage: StageName | None = None,
        prompt_id: str | None = None,
    ) -> StructuredResult[T]:
        """Generation constrained to `schema`; the result is a validated instance of it."""
        model = self._model(fast)
        json_schema = inline_refs(schema.model_json_schema())
        run_id = await self._start_run(agent, stage, project_id, model, prompt)
        usage = _Usage(model=model)
        started = time.perf_counter()
        attempt_prompt = prompt
        try:
            for attempt in range(STRUCTURED_REPAIR_ATTEMPTS + 1):
                completion = await self._complete_with_retries(
                    model, attempt_prompt, system, temperature, max_output_tokens, json_schema
                )
                usage = _Usage(
                    usage.tokens_in + completion.tokens_in, usage.tokens_out + completion.tokens_out, completion.model
                )
                try:
                    data = schema.model_validate_json(_strip_code_fence(completion.text))
                    break
                except ValidationError as exc:
                    if attempt == STRUCTURED_REPAIR_ATTEMPTS:
                        raise
                    log.warning(
                        "llm_invalid_structured_output", agent=agent, run_id=str(run_id), errors=exc.error_count()
                    )
                    attempt_prompt = _repair_prompt(prompt, completion.text, exc)
        except Exception as exc:
            await self._fail_run(run_id, exc, usage, started)
            raise LLMError(f"LLM call failed: {exc}", run_id) from exc
        output = {"data": data.model_dump(mode="json"), "prompt": prompt_id}
        run = await self._finish_run(run_id, output, usage, started)
        return StructuredResult(data=data, run=run)

    # ── internals ──────────────────────────────────────────────────

    def _model(self, fast: bool) -> str:
        return self.settings.llm_model_fast if fast else self.settings.llm_model

    async def _complete_with_retries(
        self,
        model: str,
        prompt: str,
        system: str | None,
        temperature: float | None,
        max_output_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> Any:
        """Retry transient errors on `model`; if it's still failing (e.g. overloaded), try the fallback model."""
        fallback = self.settings.llm_model_fallback
        models = [model] + ([fallback] if fallback and fallback != model else [])
        for index, current in enumerate(models):
            is_last = index == len(models) - 1
            retries = self.settings.llm_max_retries if is_last else self.settings.llm_retries_before_fallback
            try:
                return await self._retry(current, retries, prompt, system, temperature, max_output_tokens, json_schema)
            except Exception as exc:
                if is_last or not self._is_transient(exc):
                    raise
                log.warning("llm_fallback", model=current, fallback=models[index + 1], error=_short(exc))
        raise AssertionError("unreachable")

    async def _retry(
        self,
        model: str,
        retries: int,
        prompt: str,
        system: str | None,
        temperature: float | None,
        max_output_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> Any:
        attempts = retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(
                    self.provider.complete(
                        model=model,
                        prompt=prompt,
                        system=system,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        json_schema=json_schema,
                    ),
                    timeout=self.settings.llm_timeout_s,
                )
            except Exception as exc:
                if attempt == attempts or not self._is_transient(exc):
                    raise
                delay = min(2 ** (attempt - 1), 30)
                log.warning("llm_retry", model=model, attempt=attempt, delay_s=delay, error=_short(exc))
                await self._sleep(delay)
        raise AssertionError("unreachable")

    def _is_transient(self, exc: BaseException) -> bool:
        return isinstance(exc, TimeoutError) or self.provider.is_retryable(exc)

    async def _start_run(
        self, agent: str, stage: StageName | None, project_id: uuid.UUID | None, model: str, prompt: str
    ) -> uuid.UUID:
        run = AgentRun(
            id=uuid.uuid4(),
            agent=agent,
            stage=stage,
            project_id=project_id,
            model=model,
            input_summary=prompt[:INPUT_SUMMARY_CHARS],
            status=RunStatus.RUNNING,
        )
        async with self._session_factory() as session:
            session.add(run)
            await session.commit()
        return run.id

    async def _finish_run(self, run_id: uuid.UUID, output: dict[str, Any], usage: _Usage, started: float) -> RunInfo:
        latency_ms = round((time.perf_counter() - started) * 1000)
        await self._update_run(
            run_id,
            status=RunStatus.SUCCESS,
            output=output,
            model=usage.model,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            latency_ms=latency_ms,
        )
        log.info(
            "llm_call",
            run_id=str(run_id),
            model=usage.model,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            latency_ms=latency_ms,
        )
        return RunInfo(run_id, self.provider.name, usage.model, usage.tokens_in, usage.tokens_out, latency_ms)

    async def _fail_run(self, run_id: uuid.UUID, exc: BaseException, usage: _Usage, started: float) -> None:
        await self._update_run(
            run_id,
            status=RunStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            model=usage.model,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        log.warning("llm_call_failed", run_id=str(run_id), error=f"{type(exc).__name__}: {exc}"[:300])

    async def _update_run(self, run_id: uuid.UUID, **values: Any) -> None:
        async with self._session_factory() as session:
            await session.execute(update(AgentRun).where(AgentRun.id == run_id).values(**values))
            await session.commit()


# ─── helpers ──────────────────────────────────────────────────────────


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace `$ref`s to `$defs` with the definitions themselves (providers differ in $ref support)."""
    defs: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].split("/")[-1]]
                merged = {**resolve(target), **{k: v for k, v in node.items() if k != "$ref"}}
                return merged
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    result: dict[str, Any] = resolve(schema)
    return result


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _strip_code_fence(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text


def _repair_prompt(prompt: str, bad_output: str, error: ValidationError) -> str:
    problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in error.errors()[:10])
    return (
        f"{prompt}\n\n---\nYour previous reply did not match the required JSON schema.\n"
        f"Problems: {problems}\nPrevious reply:\n{bad_output[:4000]}\n\n"
        "Reply again with only a corrected JSON object."
    )


# ─── wiring ───────────────────────────────────────────────────────────


@lru_cache
def get_provider() -> LLMProvider:
    s = get_settings()
    if s.llm_provider == "fake":
        return FakeLLM()
    if s.llm_provider == "gemini":
        if not s.gemini_api_key:
            raise LLMNotConfiguredError("LLM provider 'gemini' is not configured: set GEMINI_API_KEY in .env")
        return GeminiProvider(s.gemini_api_key, s.llm_timeout_s)
    if not s.openai_api_key:
        raise LLMNotConfiguredError("LLM provider 'openai' is not configured: set OPENAI_API_KEY in .env")
    return OpenAIProvider(s.openai_api_key, s.llm_timeout_s)


@asynccontextmanager
async def _api_session() -> AsyncIterator[AsyncSession]:
    # agent_runs are written on their own session, so they persist even if the caller rolls back
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        yield session


def get_llm() -> LLMService:
    """FastAPI dependency. Workers build their own with `LLMService(get_provider(), settings, worker_session)`."""
    return LLMService(get_provider(), get_settings(), _api_session)
