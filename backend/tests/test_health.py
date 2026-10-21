"""/api/v1/health with every dependency probe mocked (no real services needed)."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from app.main import app
from app.services import health


async def _ok(settings: Settings) -> str | None:
    return None


async def _fail(settings: Settings) -> str | None:
    raise ConnectionError("connection refused")


async def _hang(settings: Settings) -> str | None:
    await asyncio.sleep(10)
    return None


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> dict[str, health.Probe]:
    fakes: dict[str, health.Probe] = {name: _ok for name in health.PROBES}
    monkeypatch.setattr(health, "PROBES", fakes)
    return fakes


async def test_health_all_ok(client: AsyncClient, probes: dict[str, health.Probe]) -> None:
    resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["dependencies"]) == {"database", "redis", "minio", "neo4j", "grobid"}
    assert all(d["status"] == "ok" for d in body["dependencies"].values())


async def test_health_degraded_when_one_dependency_fails(client: AsyncClient, probes: dict[str, health.Probe]) -> None:
    probes["redis"] = _fail

    resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["redis"]["status"] == "error"
    assert "connection refused" in body["dependencies"]["redis"]["detail"]
    assert body["dependencies"]["database"]["status"] == "ok"


async def test_health_times_out_slow_dependency(client: AsyncClient, probes: dict[str, health.Probe]) -> None:
    probes["grobid"] = _hang
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, health_check_timeout_s=0.05)
    try:
        resp = await client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    grobid = resp.json()["dependencies"]["grobid"]
    assert grobid["status"] == "error"
    assert "timed out" in grobid["detail"]


async def test_response_has_request_id(client: AsyncClient, probes: dict[str, health.Probe]) -> None:
    resp = await client.get("/api/v1/health", headers={"X-Request-ID": "abc123"})

    assert resp.headers["X-Request-ID"] == "abc123"


async def test_cors_allows_frontend_origin(client: AsyncClient, probes: dict[str, health.Probe]) -> None:
    resp = await client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )

    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_unexpected_errors_are_json_500s_with_cors_headers(api_client: Any) -> None:
    """A crash must reach the browser as a readable 500, not as an opaque CORS failure."""
    from app.main import app

    async def boom() -> None:
        raise RuntimeError("bug")

    app.add_api_route("/api/v1/__boom", boom)
    try:
        resp = await api_client.get("/api/v1/__boom", headers={"Origin": "http://localhost:3000"})
    finally:
        app.router.routes.pop()
    assert resp.status_code == 500
    assert resp.json()["detail"].startswith("Something went wrong") and resp.json()["request_id"]
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
