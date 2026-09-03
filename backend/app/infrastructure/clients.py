from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.infrastructure.milvus_client import get_milvus_client
from app.infrastructure.minio_client import get_minio_client
from app.infrastructure.neo4j_client import get_neo4j_client
from app.infrastructure.postgres_client import get_postgres_client
from app.infrastructure.redis_client import get_redis_client


def _clients() -> dict[str, object]:
    return {
        "postgres": get_postgres_client(),
        "redis": get_redis_client(),
        "minio": get_minio_client(),
        "milvus": get_milvus_client(),
        "neo4j": get_neo4j_client(),
    }


async def connect_infrastructure() -> None:
    """Connect every business datastore, failing startup as one combined error."""
    clients = _clients()
    results = await asyncio.gather(
        *(client.connect() for client in clients.values()),  # type: ignore[attr-defined]
        return_exceptions=True,
    )
    failures = {
        name: result
        for (name, _), result in zip(clients.items(), results, strict=True)
        if isinstance(result, BaseException)
    }
    if failures:
        await close_infrastructure()
        details = "; ".join(f"{name}: {type(error).__name__}: {error}" for name, error in failures.items())
        raise RuntimeError(f"Infrastructure connection failed: {details}")


async def close_infrastructure() -> None:
    """Release every pool/driver; safe to call more than once."""
    await asyncio.gather(
        *(client.close() for client in reversed(tuple(_clients().values()))),  # type: ignore[attr-defined]
        return_exceptions=True,
    )


async def healthcheck_infrastructure() -> dict[str, dict[str, str]]:
    """Return per-service readiness without allowing one failure to hide the rest."""
    clients = _clients()
    results = await asyncio.gather(
        *(client.healthcheck() for client in clients.values()),  # type: ignore[attr-defined]
        return_exceptions=True,
    )
    health: dict[str, dict[str, str]] = {}
    for (name, _), result in zip(clients.items(), results, strict=True):
        if isinstance(result, BaseException):
            health[name] = {"status": "error", "detail": f"{type(result).__name__}: {result}"}
        else:
            health[name] = {"status": "ok" if result else "error"}
    return health


@asynccontextmanager
async def infrastructure_lifespan(_: object | None = None) -> AsyncIterator[None]:
    """FastAPI-compatible lifecycle context for all infrastructure clients."""
    await connect_infrastructure()
    try:
        yield
    finally:
        await close_infrastructure()
