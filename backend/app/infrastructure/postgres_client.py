from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

import asyncpg

from app.config import get_settings


class PostgresClient:
    """Owns the application's asyncpg connection pool."""

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10, timeout: float = 10.0) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._timeout = timeout
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgreSQL client is not connected")
        return self._pool

    async def connect(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                    timeout=self._timeout,
                    command_timeout=self._timeout,
                )
        return self._pool

    async def close(self) -> None:
        async with self._lock:
            pool, self._pool = self._pool, None
            if pool is not None:
                await pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        pool = await self.connect()
        async with pool.acquire() as connection:
            yield connection

    async def healthcheck(self) -> bool:
        async with self.connection() as connection:
            return await connection.fetchval("SELECT 1") == 1


@lru_cache(maxsize=1)
def get_postgres_client() -> PostgresClient:
    settings = get_settings()
    return PostgresClient(
        dsn=settings.async_database_url,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
        timeout=settings.infrastructure_connect_timeout,
    )
