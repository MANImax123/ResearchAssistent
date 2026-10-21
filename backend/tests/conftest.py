import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.orchestrator import service as orchestrator
from app.orchestrator.checkpoints import open_checkpointer
from app.services.storage import Storage, get_storage
from app.workers.dispatch import get_dispatcher
from tests.helpers import IDEA, Flow, RecordingDispatcher, auth_headers

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ─── Test database ──────────────────────────────────────────────────────
# DB tests run against a separate `<POSTGRES_DB>_test` database on the same server,
# and are skipped when Postgres is unreachable.

TEST_SETTINGS = get_settings().model_copy(
    update={
        "postgres_db": f"{get_settings().postgres_db}_test",
        "minio_bucket": f"{get_settings().minio_bucket}-test",
        # Tests fetch presigned URLs from inside the container, so sign them for the internal host
        "minio_public_endpoint": "",
    }
)
TEST_DATABASE_URL = TEST_SETTINGS.database_url
TEST_STORAGE = Storage.from_settings(TEST_SETTINGS)


async def _ensure_test_database() -> None:
    s = TEST_SETTINGS
    conn = await asyncpg.connect(
        host=s.postgres_host,
        port=s.postgres_port,
        user=s.postgres_user,
        password=s.postgres_password,
        database="postgres",
        timeout=5,
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", s.postgres_db):
            await conn.execute(f'CREATE DATABASE "{s.postgres_db}"')
    finally:
        await conn.close()


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def test_database() -> str:
    """Create the test database if needed and migrate it to head."""
    try:
        asyncio.run(_ensure_test_database())
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    command.upgrade(alembic_config(), "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db_session(test_database: str) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back after each test (commits become savepoints)."""
    engine = create_async_engine(test_database, poolclass=pool.NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest.fixture
async def api_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client whose requests use the rolled-back test session instead of the dev DB."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = lambda: TEST_STORAGE
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
async def storage() -> Storage:
    """The test bucket (created if needed); skips the test when MinIO is unreachable."""
    try:
        await asyncio.wait_for(TEST_STORAGE.ensure_bucket(), timeout=5)
    except Exception as exc:
        pytest.skip(f"MinIO not reachable: {exc}")
    return TEST_STORAGE


# ─── Workflow fixtures (jobs, orchestrator, review actions, sections) ─────────


@pytest.fixture
def dispatcher() -> Iterator[RecordingDispatcher]:
    """Record queued jobs instead of sending them to Celery."""
    d = RecordingDispatcher()
    app.dependency_overrides[get_dispatcher] = lambda: d
    yield d
    app.dependency_overrides.pop(get_dispatcher, None)


def checkpointer_on_test_db() -> Any:
    return open_checkpointer(TEST_SETTINGS.psycopg_url)


@pytest.fixture
async def checkpointer(test_database: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[uuid.UUID]]:
    """Point the orchestrator at the test database; delete the graph threads created by each test."""
    monkeypatch.setattr(orchestrator, "checkpointer_factory", checkpointer_on_test_db)
    threads: list[uuid.UUID] = []
    yield threads
    async with checkpointer_on_test_db() as saver:
        for pid in threads:
            await saver.adelete_thread(str(pid))


@pytest.fixture
async def alice(api_client: AsyncClient) -> dict[str, str]:
    return await auth_headers(api_client, "alice@example.com")


@pytest.fixture
async def project_id(api_client: AsyncClient, alice: dict[str, str], checkpointer: list[uuid.UUID]) -> uuid.UUID:
    resp = await api_client.post(
        "/api/v1/projects", json={"title": "GNN drug discovery", "raw_idea": IDEA}, headers=alice
    )
    pid = uuid.UUID(resp.json()["id"])
    checkpointer.append(pid)
    return pid


@pytest.fixture
async def flow(
    api_client: AsyncClient,
    alice: dict[str, str],
    project_id: uuid.UUID,
    db_session: AsyncSession,
    dispatcher: RecordingDispatcher,
) -> Flow:
    return Flow(api_client, alice, project_id, db_session, dispatcher)


@pytest.fixture(autouse=True)
def stub_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests run stages with the placeholder agents unless they install real ones (never a live LLM)."""
    from app.agents import registry

    monkeypatch.setattr(registry, "agent_factory", lambda session_factory: {})


@pytest.fixture(autouse=True)
def no_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything reaching for the real LLM in a test fails loudly instead of spending quota."""
    from app.agents import registry

    def refuse(session_factory: Any) -> Any:
        def llm() -> Any:
            raise AssertionError("A test tried to use the live LLM; install a FakeLLM")

        return llm

    monkeypatch.setattr(registry, "llm_factory", refuse)
