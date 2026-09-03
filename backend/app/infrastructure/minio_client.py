from __future__ import annotations

import asyncio
import mimetypes
from functools import lru_cache
from pathlib import Path, PurePosixPath

from minio import Minio
from minio.error import S3Error
from urllib3 import PoolManager, Timeout

from app.config import get_settings


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
        """检查 MinIO 服务是否可访问。"""

        client = await self.connect()
        await asyncio.to_thread(client.list_buckets)
        return True

    async def ensure_bucket(self, bucket_name: str) -> None:
        """确保业务桶存在；不存在时创建该桶。"""

        name = _validate_bucket_name(bucket_name)
        client = await self.connect()
        exists = await asyncio.to_thread(client.bucket_exists, name)
        if not exists:
            try:
                await asyncio.to_thread(client.make_bucket, name, location=self._region)
            except S3Error as exc:
                # 多个任务并发建桶时，另一个任务可能已经先完成创建。
                if exc.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    raise

    async def upload_file(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str | Path,
        *,
        content_type: str | None = None,
    ) -> str:
        """上传单个本地文件，并返回可持久化到数据库的 ``minio://`` 地址。"""

        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"待上传文件不存在：{source}")
        bucket = _validate_bucket_name(bucket_name)
        object_key = _normalise_object_name(object_name)
        await self.ensure_bucket(bucket)
        client = await self.connect()
        guessed_type = content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        await asyncio.to_thread(
            client.fput_object,
            bucket,
            object_key,
            str(source),
            content_type=guessed_type,
        )
        return self.object_uri(bucket, object_key)

    async def upload_directory(
        self,
        bucket_name: str,
        object_prefix: str,
        local_directory: str | Path,
    ) -> dict[Path, str]:
        """递归上传目录内全部文件，并返回“本地绝对路径到 MinIO 地址”的映射。"""

        directory = Path(local_directory).expanduser().resolve()
        if not directory.is_dir():
            raise NotADirectoryError(f"待上传目录不存在：{directory}")
        prefix = _normalise_object_name(object_prefix)
        uploaded: dict[Path, str] = {}
        for source in sorted((path for path in directory.rglob("*") if path.is_file()), key=str):
            relative = source.relative_to(directory).as_posix()
            object_name = f"{prefix}/{relative}"
            uploaded[source.resolve()] = await self.upload_file(bucket_name, object_name, source)
        return uploaded

    @staticmethod
    def object_uri(bucket_name: str, object_name: str) -> str:
        """把桶名和对象键转换为统一的 ``minio://bucket/key`` 地址。"""

        return f"minio://{_validate_bucket_name(bucket_name)}/{_normalise_object_name(object_name)}"


def _validate_bucket_name(bucket_name: str) -> str:
    """校验业务桶名，避免空桶名或路径字符进入 SDK 调用。"""

    name = bucket_name.strip()
    if not name or "/" in name or "\\" in name:
        raise ValueError(f"非法 MinIO 桶名：{bucket_name!r}")
    return name


def _normalise_object_name(object_name: str) -> str:
    """规范并校验 MinIO 对象键，禁止绝对路径和上级目录片段。"""

    raw_name = object_name.strip().replace("\\", "/")
    path = PurePosixPath(raw_name)
    if not raw_name or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"非法 MinIO 对象键：{object_name!r}")
    return path.as_posix()


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
