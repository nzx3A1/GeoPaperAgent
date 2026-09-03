from __future__ import annotations

import asyncio
from functools import lru_cache

from minio import Minio
from urllib3 import PoolManager, Timeout

from app.core.config import get_settings


class MinioClient:
    """Owns the synchronous MinIO SDK client without blocking the event loop."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = False,
        region: str = "us-east-1",
        timeout: float = 10.0,
    ) -> None:
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._secure = secure
        self._region = region
        self._timeout = timeout
        self._client: Minio | None = None
        self._http_client: PoolManager | None = None
        self._lock = asyncio.Lock()

    @property
    def client(self) -> Minio:
        if self._client is None:
            raise RuntimeError("MinIO client is not connected")
        return self._client

    async def connect(self) -> Minio:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                http_client = PoolManager(
                    timeout=Timeout(connect=self._timeout, read=self._timeout),
                    retries=False,
                )
                client = Minio(
                    endpoint=self._endpoint,
                    access_key=self._access_key,
                    secret_key=self._secret_key,
                    secure=self._secure,
                    region=self._region,
                    http_client=http_client,
                )
                try:
                    await asyncio.to_thread(client.list_buckets)
                except Exception:
                    http_client.clear()
                    raise
                self._http_client = http_client
                self._client = client
        return self._client

    async def close(self) -> None:
        async with self._lock:
            self._client = None
            http_client, self._http_client = self._http_client, None
            if http_client is not None:
                http_client.clear()

    async def healthcheck(self) -> bool:
        client = await self.connect()
        await asyncio.to_thread(client.list_buckets)
        return True


@lru_cache(maxsize=1)
def get_minio_client() -> MinioClient:
    settings = get_settings()
    return MinioClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
        timeout=settings.infrastructure_connect_timeout,
    )
