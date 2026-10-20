"""Dependency probes used by the /health endpoint.

Each probe raises on failure; `check_all` runs them concurrently with a timeout
and turns results into a status map, so one slow service can't stall the endpoint.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from minio import Minio
from neo4j import AsyncGraphDatabase
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import engine

log = get_logger(__name__)


class DependencyStatus(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: float
    detail: str | None = None


async def check_database(settings: Settings) -> str | None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        version = (
            await conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        ).scalar_one_or_none()
    if not version:
        raise RuntimeError("pgvector extension not installed")
    return f"pgvector {version}"


async def check_redis(settings: Settings) -> str | None:
    client = Redis.from_url(settings.redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()
    return None


async def check_minio(settings: Settings) -> str | None:
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    # The MinIO SDK is synchronous
    exists = await asyncio.to_thread(client.bucket_exists, settings.minio_bucket)
    if not exists:
        raise RuntimeError(f"bucket '{settings.minio_bucket}' does not exist")
    return f"bucket '{settings.minio_bucket}'"


async def check_neo4j(settings: Settings) -> str | None:
    async with AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    ) as driver:
        await driver.verify_connectivity()
    return None


async def check_grobid(settings: Settings) -> str | None:
    async with httpx.AsyncClient(timeout=settings.health_check_timeout_s) as client:
        resp = await client.get(f"{settings.grobid_url.rstrip('/')}/api/isalive")
        resp.raise_for_status()
        if resp.text.strip().lower() != "true":
            raise RuntimeError(f"not ready: {resp.text.strip()[:100]}")
    return None


Probe = Callable[[Settings], Awaitable[str | None]]

PROBES: dict[str, Probe] = {
    "database": check_database,
    "redis": check_redis,
    "minio": check_minio,
    "neo4j": check_neo4j,
    "grobid": check_grobid,
}


async def _run_probe(name: str, probe: Probe, settings: Settings) -> DependencyStatus:
    start = time.perf_counter()
    try:
        detail = await asyncio.wait_for(probe(settings), timeout=settings.health_check_timeout_s)
        status: Literal["ok", "error"] = "ok"
    except TimeoutError:
        status, detail = "error", f"timed out after {settings.health_check_timeout_s}s"
    except Exception as exc:
        status, detail = "error", f"{type(exc).__name__}: {exc}"[:300]
    latency = round((time.perf_counter() - start) * 1000, 1)
    if status == "error":
        log.warning("dependency_unhealthy", dependency=name, detail=detail)
    return DependencyStatus(status=status, latency_ms=latency, detail=detail)


async def check_all(settings: Settings) -> dict[str, DependencyStatus]:
    results = await asyncio.gather(*(_run_probe(n, p, settings) for n, p in PROBES.items()))
    return dict(zip(PROBES, results, strict=True))
