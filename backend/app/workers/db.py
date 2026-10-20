"""Database sessions for worker processes.

Each Celery task runs its coroutine on a fresh event loop, so pooled connections
(bound to the loop that opened them) can't be reused: use NullPool.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import get_settings


@lru_cache
def _engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=pool.NullPool)


@asynccontextmanager
async def worker_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_engine(), expire_on_commit=False) as session:
        yield session
