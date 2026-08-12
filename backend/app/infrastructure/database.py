from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.core.config import get_settings

_pool: asyncpg.Pool | None = None


async def connect_database() -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_settings().async_database_url, min_size=1, max_size=10)


async def close_database() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    if _pool is None:
        await connect_database()
    assert _pool is not None
    async with _pool.acquire() as conn:
        yield conn
