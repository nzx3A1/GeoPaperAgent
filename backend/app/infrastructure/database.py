"""Backward-compatible PostgreSQL helpers for existing repositories."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.infrastructure.postgres_client import get_postgres_client


async def connect_database() -> None:
    await get_postgres_client().connect()


async def close_database() -> None:
    await get_postgres_client().close()


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    async with get_postgres_client().connection() as postgres_connection:
        yield postgres_connection
