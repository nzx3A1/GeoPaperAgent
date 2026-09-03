"""统一封装全部 PostgreSQL 基础数据服务。"""

from functools import lru_cache

import asyncpg

from app.infrastructure.postgres_client import PostgresClient, get_postgres_client
from app.repositories.image_chunk import ImageChunkRepository
from app.repositories.paper import PaperRepository
from app.repositories.section import SectionRepository
from app.repositories.table_chunk import TableChunkRepository
from app.repositories.text_chunk import TextChunkRepository


class DatabaseService:
    """集中管理五张业务表的 Repository，并复用同一个连接池。"""

    def __init__(self, client: PostgresClient | None = None) -> None:
        self.client = client or get_postgres_client()
        self.papers = PaperRepository(self.client)
        self.sections = SectionRepository(self.client)
        self.text_chunks = TextChunkRepository(self.client)
        self.image_chunks = ImageChunkRepository(self.client)
        self.table_chunks = TableChunkRepository(self.client)

    async def connect(self) -> asyncpg.Pool:
        """初始化 PostgreSQL 连接池。"""
        return await self.client.connect()

    async def close(self) -> None:
        """关闭 PostgreSQL 连接池。"""
        await self.client.close()

    async def healthcheck(self) -> bool:
        """检查 PostgreSQL 是否可用。"""
        return await self.client.healthcheck()


@lru_cache(maxsize=1)
def get_database_service() -> DatabaseService:
    """返回应用级复用的数据库服务实例。"""
    return DatabaseService()
