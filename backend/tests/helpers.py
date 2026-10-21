"""Shared test helpers (fixtures that use them live in conftest.py)."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback, Job
from app.db.models.enums import JobStatus
from app.services.jobs import execute_job
from app.workers.handlers import HANDLERS

PASSWORD = "s3cret-passw0rd"
IDEA = "Use graph neural networks to rank drug candidates"


async def auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email})
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def factory_for(session: AsyncSession) -> Any:
    """A session factory that hands out the (rolled-back) test session."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


class RecordingDispatcher:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.queued: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    async def enqueue(self, job: Job, params: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.queued.append((job.id, job.type, params))


class Flow:
    """Drives a project through the API, running queued jobs in-process like the worker would."""

    def __init__(
        self, client: AsyncClient, headers: dict[str, str], pid: uuid.UUID, db: AsyncSession, d: RecordingDispatcher
    ):
        self.client, self.headers, self.pid, self.db, self.dispatcher = client, headers, pid, db, d

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    async def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return await self.client.post(self.url(path), json=json or {}, headers=self.headers)

    async def finish_jobs(self) -> None:
        while self.dispatcher.queued:
            job_id, job_type, params = self.dispatcher.queued.pop(0)
            assert await execute_job(job_id, HANDLERS[job_type], params, factory_for(self.db)) == JobStatus.SUCCESS

    async def run(self, stage: str) -> None:
        resp = await self.post(f"/stages/{stage}/run")
        assert resp.status_code == 202, resp.text
        await self.finish_jobs()

    async def act(self, stage: str, action: str, body: dict[str, Any] | None = None, expect: int = 200) -> Any:
        resp = await self.post(f"/stages/{stage}/{action}", body)
        assert resp.status_code == expect, resp.text
        await self.finish_jobs()
        return resp.json()

    async def state(self) -> dict[str, Any]:
        return (await self.client.get(self.url("/state"), headers=self.headers)).json()  # type: ignore[no-any-return]

    async def statuses(self) -> dict[str, str]:
        return {s["name"]: s["status"] for s in (await self.state())["stages"]}

    async def feedback(self) -> list[tuple[str, str, str | None]]:
        rows = await self.db.scalars(
            select(Feedback).where(Feedback.project_id == self.pid).order_by(Feedback.created_at)
        )
        return [(f.stage.value, f.action.value, f.comment) for f in rows]
