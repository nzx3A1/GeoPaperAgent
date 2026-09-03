from __future__ import annotations

import asyncio
from functools import lru_cache

from redis.asyncio import Redis

from app.config import get_settings


class RedisClient:
    """Owns one redis-py asyncio client and its connection pool."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout
        self._client: Redis | None = None
        self._lock = asyncio.Lock()

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("Redis client is not connected")
        return self._client

    async def connect(self) -> Redis:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                client = Redis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_connect_timeout=self._timeout,
                    socket_timeout=self._timeout,
                    health_check_interval=30,
                )
                try:
                    await client.ping()
                except Exception:
                    await client.aclose()
                    raise
                self._client = client
        return self._client

    async def close(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
            if client is not None:
                await client.aclose()

    async def healthcheck(self) -> bool:
        client = await self.connect()
        return bool(await client.ping())


@lru_cache(maxsize=1)
def get_redis_client() -> RedisClient:
    settings = get_settings()
    return RedisClient(settings.redis_url, settings.infrastructure_connect_timeout)
