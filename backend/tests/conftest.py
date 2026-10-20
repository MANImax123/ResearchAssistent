import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

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
from app.services.storage import Storage, get_storage

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
