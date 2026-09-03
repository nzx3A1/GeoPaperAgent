from __future__ import annotations

import asyncio
from functools import lru_cache

from pymilvus import MilvusClient as PyMilvusClient

from app.core.config import get_settings


class MilvusClient:
    """Owns the PyMilvus client and bridges its blocking calls to worker threads."""

    def __init__(self, uri: str, token: str = "", database: str = "default", timeout: float = 10.0) -> None:
        self._uri = uri
        self._token = token
        self._database = database
        self._timeout = timeout
        self._client: PyMilvusClient | None = None
        self._lock = asyncio.Lock()

    @property
    def client(self) -> PyMilvusClient:
        if self._client is None:
            raise RuntimeError("Milvus client is not connected")
        return self._client

    async def connect(self) -> PyMilvusClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                kwargs: dict[str, str | float] = {
                    "uri": self._uri,
                    "db_name": self._database,
                    "timeout": self._timeout,
                }
                if self._token:
                    kwargs["token"] = self._token
                client = await asyncio.to_thread(PyMilvusClient, **kwargs)
                try:
                    await asyncio.to_thread(client.list_collections)
                except Exception:
                    await asyncio.to_thread(client.close)
                    raise
                self._client = client
        return self._client

    async def close(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
            if client is not None:
                await asyncio.to_thread(client.close)

    async def healthcheck(self) -> bool:
        client = await self.connect()
        await asyncio.to_thread(client.list_collections)
        return True


@lru_cache(maxsize=1)
def get_milvus_client() -> MilvusClient:
    settings = get_settings()
    return MilvusClient(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        database=settings.milvus_database,
        timeout=settings.infrastructure_connect_timeout,
    )
