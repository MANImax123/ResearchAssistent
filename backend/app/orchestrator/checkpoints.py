"""LangGraph Postgres checkpointer (thread id = project id).

The checkpointer manages its own tables (checkpoints, checkpoint_blobs, checkpoint_writes,
checkpoint_migrations) via `setup()`; Alembic is told to ignore them.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import get_settings

CHECKPOINT_TABLES = frozenset({"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"})

CheckpointerFactory = Callable[[], AbstractAsyncContextManager[AsyncPostgresSaver]]

_ready: set[str] = set()


@asynccontextmanager
async def open_checkpointer(url: str | None = None) -> AsyncIterator[AsyncPostgresSaver]:
    """A checkpointer on its own connection; tables are created on first use per database."""
    conn_url = url or get_settings().psycopg_url
    async with AsyncPostgresSaver.from_conn_string(conn_url) as saver:
        if conn_url not in _ready:
            await saver.setup()
            _ready.add(conn_url)
        yield saver


def thread_config(project_id: Any) -> RunnableConfig:
    return {"configurable": {"thread_id": str(project_id)}}
