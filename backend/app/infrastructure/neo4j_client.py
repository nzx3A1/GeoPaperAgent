from __future__ import annotations

import asyncio
from functools import lru_cache

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.config import get_settings


class Neo4jClient:
    """Owns the application's asynchronous Neo4j driver."""

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str = "neo4j",
        timeout: float = 10.0,
    ) -> None:
        self._uri = uri
        self._username = username
        self._password = password
        self._database = database
        self._timeout = timeout
        self._driver: AsyncDriver | None = None
        self._lock = asyncio.Lock()

    @property
    def driver(self) -> AsyncDriver:
        if self._driver is None:
            raise RuntimeError("Neo4j client is not connected")
        return self._driver

    async def connect(self) -> AsyncDriver:
        if self._driver is not None:
            return self._driver
        async with self._lock:
            if self._driver is None:
                driver = AsyncGraphDatabase.driver(
                    self._uri,
                    auth=(self._username, self._password),
                    connection_timeout=self._timeout,
                )
                try:
                    await driver.verify_connectivity()
                except Exception:
                    await driver.close()
                    raise
                self._driver = driver
        return self._driver

    async def close(self) -> None:
        async with self._lock:
            driver, self._driver = self._driver, None
            if driver is not None:
                await driver.close()

    async def healthcheck(self) -> bool:
        driver = await self.connect()
        records, _, _ = await driver.execute_query(
            "RETURN 1 AS ok",
            database_=self._database,
        )
        return bool(records and records[0]["ok"] == 1)


@lru_cache(maxsize=1)
def get_neo4j_client() -> Neo4jClient:
    settings = get_settings()
    return Neo4jClient(
        uri=settings.neo4j_uri,
        username=settings.neo4j_username,
        password=settings.neo4j_password,
        database=settings.neo4j_database,
        timeout=settings.infrastructure_connect_timeout,
    )
