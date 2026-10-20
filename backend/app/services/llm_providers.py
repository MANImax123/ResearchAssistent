"""LLM provider adapters: one raw model call each. Retries, validation and logging live in llm.py.

Only this module imports provider SDKs (A3: no agent calls an SDK directly).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    tokens_in: int
    tokens_out: int


class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None,
        temperature: float | None,
        max_output_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> Completion: ...

    def is_retryable(self, exc: BaseException) -> bool: ...


def is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError | ConnectionError | httpx.TimeoutException | httpx.TransportError)


# ─── Gemini ───────────────────────────────────────────────────────────


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, timeout_s: float) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    async def complete(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None,
        temperature: float | None,
        max_output_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> Completion:
        config = self._types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json" if json_schema else None,
            response_json_schema=json_schema,
        )
        resp = await self._client.aio.models.generate_content(model=model, contents=prompt, config=config)
        usage = resp.usage_metadata
        tokens_in = (usage.prompt_token_count or 0) if usage else 0
        # "Thinking" tokens are billed as output
        tokens_out = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
        return Completion(
            text=resp.text or "", model=resp.model_version or model, tokens_in=tokens_in, tokens_out=tokens_out
        )

    def is_retryable(self, exc: BaseException) -> bool:
        from google.genai import errors

        if isinstance(exc, errors.ServerError):
            return True
        if isinstance(exc, errors.ClientError):
            return exc.code in (408, 429)
        return is_network_error(exc)


# ─── OpenAI ───────────────────────────────────────────────────────────


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, timeout_s: float) -> None:
        import openai

        self._openai = openai
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)

    async def complete(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None,
        temperature: float | None,
        max_output_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> Completion:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            kwargs["max_completion_tokens"] = max_output_tokens
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": str(json_schema.get("title", "Output")),
                    "schema": json_schema,
                    "strict": False,
                },
            }
        resp = await self._client.chat.completions.create(**kwargs)
        usage = resp.usage
        return Completion(
            text=resp.choices[0].message.content or "",
            model=resp.model,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
        )

    def is_retryable(self, exc: BaseException) -> bool:
        o = self._openai
        retryable = (o.APITimeoutError, o.APIConnectionError, o.RateLimitError, o.InternalServerError)
        return isinstance(exc, retryable) or is_network_error(exc)


# ─── Fake (tests, and LLM_PROVIDER=fake for running without an API key) ─────────────


class TransientFakeError(Exception):
    """Raise from a FakeLLM script to simulate a retryable provider error."""


FakeResponse = str | dict[str, Any] | BaseException | Callable[[str], str | dict[str, Any]]


@dataclass
class FakeCall:
    model: str
    prompt: str
    system: str | None
    json_schema: dict[str, Any] | None


@dataclass
class FakeLLM:
    """Deterministic provider. Scripted responses are used in order; when the script runs out,
    text calls return a fixed string and structured calls return a schema-valid placeholder."""

    responses: list[FakeResponse] = field(default_factory=list)
    calls: list[FakeCall] = field(default_factory=list)
    name: str = "fake"

    async def complete(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None,
        temperature: float | None,
        max_output_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> Completion:
        self.calls.append(FakeCall(model=model, prompt=prompt, system=system, json_schema=json_schema))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            if callable(response):
                response = response(prompt)
            text = response if isinstance(response, str) else json.dumps(response)
        elif json_schema is not None:
            text = json.dumps(example_from_schema(json_schema))
        else:
            text = "This is a placeholder response from the fake LLM provider."
        return Completion(
            text=text, model=f"fake/{model}", tokens_in=len(prompt) // 4 + 1, tokens_out=len(text) // 4 + 1
        )

    def is_retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, TransientFakeError)


def example_from_schema(schema: dict[str, Any]) -> Any:
    """Smallest value that satisfies a (ref-free) JSON schema: enough for placeholders and tests."""
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"] or schema[key]
            return example_from_schema(options[0])
    kind = schema.get("type")
    if kind == "object":
        props: dict[str, Any] = schema.get("properties", {})
        return {name: example_from_schema(sub) for name, sub in props.items()}
    if kind == "array":
        count = max(int(schema.get("minItems", 1)), 1)
        return [example_from_schema(schema.get("items", {"type": "string"})) for _ in range(count)]
    if kind in ("integer", "number"):
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    text = "placeholder"
    min_len = int(schema.get("minLength", 0))
    return text if len(text) >= min_len else text.ljust(min_len, "x")
