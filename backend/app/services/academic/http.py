"""Shared HTTP layer for the academic API clients.

Every client gets: async httpx with a timeout, a minimum spacing between its requests (each API's
published rate limit), retries with exponential backoff on 429/5xx/network errors (honouring
Retry-After), and a 24 h response cache in Redis keyed by the request. Clients are created per event
loop, because workers run each job in a fresh `asyncio.run()` loop (see `PerLoop`).
"""

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.logging import get_logger
from app.services.academic.models import SearchSourceName
from app.services.llm_providers import PerLoop

log = get_logger(__name__)

CACHE_TTL_S = 24 * 3600
MAX_RETRY_AFTER_S = 30.0
USER_AGENT = "ResearchFlow/0.1"

Sleep = Callable[[float], Awaitable[None]]


class SourceError(Exception):
    """A source could not answer (after retries). Discovery reports it and carries on with the others."""

    def __init__(self, source: SearchSourceName, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_s: int) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        self.data[key] = value


class RedisCache:
    """Best effort: if Redis is unavailable the request simply isn't cached."""

    def __init__(self, url: str) -> None:
        from redis import asyncio as aioredis

        self._clients = PerLoop(lambda: aioredis.from_url(url, decode_responses=True))  # type: ignore[no-untyped-call]

    async def get(self, key: str) -> str | None:
        try:
            value = await self._clients.get().get(key)
            return value if isinstance(value, str) else None
        except Exception as exc:  # noqa: BLE001 - the cache must never break a search
            log.warning("academic_cache_unavailable", error=str(exc))
            return None

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        try:
            await self._clients.get().set(key, value, ex=ttl_s)
        except Exception as exc:  # noqa: BLE001
            log.warning("academic_cache_unavailable", error=str(exc))


@dataclass
class _LoopState:
    client: httpx.AsyncClient
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_request: float = 0.0


class ApiClient:
    """Base class: subclasses set `source`, `base_url` and `min_interval_s` and call `get_text`."""

    source: SearchSourceName
    base_url: str
    min_interval_s: float = 1.0

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 20.0,
        max_retries: int = 3,
        backoff_s: float = 2.0,
        headers: dict[str, str] | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._cache = cache
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        all_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self._state = PerLoop(
            lambda: _LoopState(
                httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=all_headers,
                    timeout=timeout_s,
                    transport=transport,
                    follow_redirects=True,
                )
            )
        )

    def _cache_key(self, path: str, params: dict[str, Any]) -> str:
        raw = json.dumps([self.source, path, sorted(params.items())], default=str)
        return "academic:" + hashlib.sha256(raw.encode()).hexdigest()

    async def _throttle(self, state: _LoopState) -> None:
        async with state.lock:
            wait = state.last_request + self.min_interval_s - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            state.last_request = time.monotonic()

    async def get_text(self, path: str, params: dict[str, Any] | None = None) -> str | None:
        """Response body, or None for 404. Raises SourceError when the source keeps failing."""
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        key = self._cache_key(path, params)
        if self._cache is not None and (cached := await self._cache.get(key)) is not None:
            return cached

        state = self._state.get()
        problem = "no response"
        retry_after = 0.0
        for attempt in range(self._max_retries + 1):
            if attempt:
                delay = self._backoff_s * 2 ** (attempt - 1)
                await self._sleep(min(max(delay, retry_after), MAX_RETRY_AFTER_S))
            retry_after = 0.0
            await self._throttle(state)
            try:
                response = await state.client.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                problem = f"network error ({type(exc).__name__})"
                continue
            if response.status_code == 404:
                return None
            if response.status_code == 429 or response.status_code >= 500:
                problem = "rate limited" if response.status_code == 429 else f"HTTP {response.status_code}"
                retry_after = _retry_after(response)
                continue
            if response.status_code >= 400:
                raise SourceError(self.source, f"HTTP {response.status_code}: {response.text[:200]}")
            if self._cache is not None:
                await self._cache.set(key, response.text, CACHE_TTL_S)
            return response.text
        log.warning("academic_source_failed", source=self.source, path=path, problem=problem)
        raise SourceError(self.source, problem)

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        text = await self.get_text(path, params)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError(self.source, "invalid JSON response") from exc


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("retry-after", 0))
    except ValueError:
        return 0.0
