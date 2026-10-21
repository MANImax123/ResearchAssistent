"""Alembic migrations: full upgrade/downgrade cycle and model/migration drift."""

import asyncio

import asyncpg
from alembic import command

from app.db.base import Base
from app.orchestrator.checkpoints import CHECKPOINT_TABLES
from tests.conftest import TEST_SETTINGS, alembic_config

EXPECTED_TABLES = {
    "users", "projects", "project_members", "research_state", "stages", "sections", "section_versions",
    "files", "papers", "paper_profiles", "paper_chunks", "comparisons", "contradictions", "research_gaps",
    "references", "figures", "tables", "agent_runs", "feedback", "jobs",
}  # fmt: skip


def _public_tables() -> set[str]:
    async def fetch() -> set[str]:
        s = TEST_SETTINGS
        conn = await asyncpg.connect(
            host=s.postgres_host, port=s.postgres_port, user=s.postgres_user,
            password=s.postgres_password, database=s.postgres_db,
        )  # fmt: skip
        try:
            rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        finally:
            await conn.close()
        # Alembic's bookkeeping and LangGraph's checkpoint tables aren't part of our schema
        return {r["tablename"] for r in rows} - {"alembic_version"} - CHECKPOINT_TABLES

    return asyncio.run(fetch())


def test_models_cover_every_b11_table() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_upgrade_downgrade_upgrade(test_database: str) -> None:
    cfg = alembic_config()
    assert _public_tables() == EXPECTED_TABLES

    command.downgrade(cfg, "base")
    assert _public_tables() == set()

    command.upgrade(cfg, "head")
    assert _public_tables() == EXPECTED_TABLES


def test_models_match_migrations(test_database: str) -> None:
    """Fails if a model changed without a new migration (`alembic check`)."""
    command.check(alembic_config())
