"""Background jobs: API, status lifecycle (pending → running → success/failed), Celery eager mode."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, pool, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Job, ProjectMember, User
from app.db.models.enums import JobStatus, MemberRole
from app.main import app
from app.services.jobs import execute_job
from app.workers import tasks
from app.workers.celery_app import celery_app
from app.workers.dispatch import get_dispatcher
from app.workers.handlers import HANDLERS
from tests.conftest import TEST_DATABASE_URL
from tests.helpers import Flow, RecordingDispatcher

PASSWORD = "s3cret-passw0rd"


async def auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email})
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def alice(api_client: AsyncClient) -> dict[str, str]:
    return await auth_headers(api_client, "alice@example.com")


def recording_factory(session: AsyncSession, history: list[tuple[str, int, str | None]], job_id: uuid.UUID) -> Any:
    """Session factory for the job runner that snapshots the job row after every write."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session
        job = await session.scalar(select(Job).where(Job.id == job_id).execution_options(populate_existing=True))
        assert job is not None
        history.append((job.status.value, job.progress, job.message))

    return factory


async def test_demo_job_lifecycle(
    api_client: AsyncClient, alice: dict[str, str], dispatcher: RecordingDispatcher, db_session: AsyncSession
) -> None:
    resp = await api_client.post("/api/v1/jobs/demo", json={"steps": 3, "delay_s": 0}, headers=alice)

    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert (job["type"], job["status"], job["progress"]) == ("demo", "pending", 0)
    job_id = uuid.UUID(job["id"])
    assert dispatcher.queued == [(job_id, "demo", {"steps": 3, "delay_s": 0.0, "fail_at": None})]

    # What the worker does, in-process
    history: list[tuple[str, int, str | None]] = []
    final = await execute_job(
        job_id, HANDLERS["demo"], {"steps": 3, "delay_s": 0}, recording_factory(db_session, history, job_id)
    )

    assert final == JobStatus.SUCCESS
    assert history == [
        ("running", 0, "Started"),
        ("running", 33, "Step 1 of 3"),
        ("running", 67, "Step 2 of 3"),
        ("running", 100, "Step 3 of 3"),
        ("success", 100, "Done"),
    ]
    polled = (await api_client.get(f"/api/v1/jobs/{job_id}", headers=alice)).json()
    assert polled["status"] == "success"
    assert polled["result"] == {"steps": 3, "message": "Demo job completed"}
    assert polled["error"] is None


async def test_failing_job_records_error(
    api_client: AsyncClient, alice: dict[str, str], dispatcher: RecordingDispatcher, db_session: AsyncSession
) -> None:
    body = {"steps": 3, "delay_s": 0, "fail_at": 2}
    job_id = uuid.UUID((await api_client.post("/api/v1/jobs/demo", json=body, headers=alice)).json()["id"])
    history: list[tuple[str, int, str | None]] = []

    final = await execute_job(
        job_id,
        HANDLERS["demo"],
        {"steps": 3, "delay_s": 0, "fail_at": 2},
        recording_factory(db_session, history, job_id),
    )

    assert final == JobStatus.FAILED
    assert history[-1] == ("failed", 33, "Failed")
    polled = (await api_client.get(f"/api/v1/jobs/{job_id}", headers=alice)).json()
    assert polled["status"] == "failed"
    assert polled["error"] == "RuntimeError: Demo failure at step 2"


async def test_enqueue_failure_marks_job_failed(
    api_client: AsyncClient, alice: dict[str, str], db_session: AsyncSession
) -> None:
    app.dependency_overrides[get_dispatcher] = lambda: RecordingDispatcher(fail=True)
    try:
        resp = await api_client.post("/api/v1/jobs/demo", json={}, headers=alice)
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)

    assert resp.status_code == 503
    jobs = (await api_client.get("/api/v1/jobs", headers=alice)).json()
    assert [(j["status"], j["error"]) for j in jobs] == [("failed", "Could not reach the task queue")]


async def test_validation(api_client: AsyncClient, alice: dict[str, str], dispatcher: RecordingDispatcher) -> None:
    for body in ({"steps": 0}, {"steps": 51}, {"delay_s": -1}, {"delay_s": 11}):
        assert (await api_client.post("/api/v1/jobs/demo", json=body, headers=alice)).status_code == 422, body
    assert dispatcher.queued == []


async def test_job_visibility(
    api_client: AsyncClient, alice: dict[str, str], dispatcher: RecordingDispatcher, db_session: AsyncSession
) -> None:
    bob = await auth_headers(api_client, "bob@example.com")
    project = (await api_client.post("/api/v1/projects", json={"title": "P"}, headers=alice)).json()
    personal = (await api_client.post("/api/v1/jobs/demo", json={}, headers=alice)).json()
    project_job = (await api_client.post("/api/v1/jobs/demo", json={"project_id": project["id"]}, headers=alice)).json()

    # Bob sees neither
    assert (await api_client.get(f"/api/v1/jobs/{personal['id']}", headers=bob)).status_code == 404
    assert (await api_client.get(f"/api/v1/jobs/{project_job['id']}", headers=bob)).status_code == 404
    assert (await api_client.get("/api/v1/jobs", headers=bob)).json() == []
    # ...nor can he start jobs on Alice's project
    resp = await api_client.post("/api/v1/jobs/demo", json={"project_id": project["id"]}, headers=bob)
    assert resp.status_code == 404

    # As a viewer he can see the project's job (not Alice's personal one), but not start jobs there
    bob_id = await db_session.scalar(select(User.id).where(User.email == "bob@example.com"))
    db_session.add(ProjectMember(project_id=uuid.UUID(project["id"]), user_id=bob_id, role=MemberRole.VIEWER))
    await db_session.flush()
    assert (await api_client.get(f"/api/v1/jobs/{project_job['id']}", headers=bob)).status_code == 200
    assert [j["id"] for j in (await api_client.get("/api/v1/jobs", headers=bob)).json()] == [project_job["id"]]
    resp = await api_client.post("/api/v1/jobs/demo", json={"project_id": project["id"]}, headers=bob)
    assert resp.status_code == 403

    # Alice's list can be filtered by project
    filtered = (await api_client.get(f"/api/v1/jobs?project_id={project['id']}", headers=alice)).json()
    assert [j["id"] for j in filtered] == [project_job["id"]]


async def test_requires_auth(api_client: AsyncClient) -> None:
    assert (await api_client.post("/api/v1/jobs/demo", json={})).status_code == 401
    assert (await api_client.get(f"/api/v1/jobs/{uuid.uuid4()}")).status_code == 401


# ─── The real Celery task, run in eager mode against the test database ──────


async def _with_test_db(fn: Any) -> Any:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=pool.NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await fn(session)
    finally:
        await engine.dispose()


@asynccontextmanager
async def _test_db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=pool.NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


def test_celery_task_eager(test_database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_job` executed by Celery (eagerly, no broker) drives the row to success."""
    monkeypatch.setattr(tasks, "session_factory", _test_db_session)
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    job_id = uuid.uuid4()

    async def create(session: AsyncSession) -> None:
        session.add(Job(id=job_id, type="demo", celery_task_id=str(job_id)))
        await session.commit()

    async def read(session: AsyncSession) -> Job | None:
        return await session.get(Job, job_id)

    async def cleanup(session: AsyncSession) -> None:
        await session.execute(delete(Job).where(Job.id == job_id))
        await session.commit()

    asyncio.run(_with_test_db(create))
    try:
        result = tasks.run_job.delay(str(job_id), "demo", {"steps": 2, "delay_s": 0})
        assert result.get() == "success"
        job = asyncio.run(_with_test_db(read))
        assert job is not None
        assert (job.status, job.progress, job.message) == (JobStatus.SUCCESS, 100, "Done")
        assert job.result_ref == {"steps": 2, "message": "Demo job completed"}
    finally:
        asyncio.run(_with_test_db(cleanup))


def test_celery_task_rejects_unknown_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    with pytest.raises(ValueError, match="Unknown job type"):
        tasks.run_job.apply(args=[str(uuid.uuid4()), "nope", {}]).get()


# ─── Recovery after a worker restart ──────────────────────────────────


async def test_interrupted_jobs_are_failed_and_stages_repaired(
    flow: Flow, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.models import Stage
    from app.db.models.enums import StageName, StageStatus
    from app.workers.recovery import INTERRUPTED, recover_interrupted_jobs
    from tests.helpers import factory_for

    # Stage 1 awaiting review in the graph; then a regenerate job starts and the worker dies mid-run
    await flow.run("idea")
    started = await flow.post("/stages/idea/regenerate", {"feedback": "shorter"})
    job_id = uuid.UUID(started.json()["job"]["id"])
    flow.dispatcher.queued.clear()
    await db_session.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.RUNNING))
    alive = Job(id=uuid.uuid4(), type="demo", status=JobStatus.RUNNING, celery_task_id="still-running")
    db_session.add(alive)
    await db_session.flush()

    recovered = await recover_interrupted_jobs(factory_for(db_session), active_task_ids={"still-running"})

    assert recovered == [job_id]
    job = await db_session.get(Job, job_id, populate_existing=True)
    assert job is not None and job.status == JobStatus.FAILED and job.error == INTERRUPTED
    assert (await db_session.get(Job, alive.id, populate_existing=True)).status == JobStatus.RUNNING  # type: ignore[union-attr]
    stage = await db_session.scalar(
        select(Stage)
        .where(Stage.project_id == flow.pid, Stage.name == StageName.IDEA)
        .execution_options(populate_existing=True)
    )
    # The graph never resumed (the job didn't run), so the review is still pending
    assert stage is not None and stage.status == StageStatus.AWAITING_REVIEW


async def test_stage_without_surviving_review_can_be_run_again(flow: Flow, db_session: AsyncSession) -> None:
    from app.db.models import Stage
    from app.db.models.enums import StageName, StageStatus
    from app.workers.recovery import recover_interrupted_jobs
    from tests.helpers import factory_for

    started = await flow.post("/stages/idea/run")  # queued, never executed: no graph state at all
    job_id = uuid.UUID(started.json()["job"]["id"])
    flow.dispatcher.queued.clear()
    await db_session.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.RUNNING))

    await recover_interrupted_jobs(factory_for(db_session), active_task_ids=set())

    stage = await db_session.scalar(
        select(Stage)
        .where(Stage.project_id == flow.pid, Stage.name == StageName.IDEA)
        .execution_options(populate_existing=True)
    )
    assert stage is not None and stage.status == StageStatus.REJECTED
    assert next(s for s in (await flow.state())["stages"] if s["name"] == "idea")["can_run"] is True
