"""表格块表数据持久层。"""

from app.infrastructure.postgres_client import PostgresClient
from app.repositories.base import BaseRepository
from app.schemas import TableChunk, TableChunkCreate, TableChunkUpdate

TABLE_CHUNK_COLUMNS = (
    "chunk_uid", "paper_id", "section_id", "table_index", "table_number", "caption", "page_number", "bbox",
    "table_html", "structured_data", "description", "metadata",
)


class TableChunkRepository(BaseRepository[TableChunkCreate, TableChunkUpdate, TableChunk]):
    """提供论文表格块数据的基础增删改查操作。"""

    def __init__(self, client: PostgresClient | None = None) -> None:
        super().__init__(
            table="table_chunk",
            row_model=TableChunk,
            create_columns=TABLE_CHUNK_COLUMNS,
            update_columns=TABLE_CHUNK_COLUMNS,
            json_columns=frozenset({"bbox", "structured_data", "metadata"}),
            non_nullable_columns=frozenset({"chunk_uid", "paper_id", "section_id"}),
            uid_column="chunk_uid",
            order_by=("table_index ASC NULLS LAST", "id ASC"),
            client=client,
        )

    async def list_by_paper(self, paper_id: int, *, limit: int = 100, offset: int = 0) -> list[TableChunk]:
        """分页查询一篇论文的表格块。"""
        return await self._list_by("paper_id", paper_id, limit=limit, offset=offset)

    async def list_by_section(self, section_id: int, *, limit: int = 100, offset: int = 0) -> list[TableChunk]:
        """分页查询一个章节的表格块。"""
        return await self._list_by("section_id", section_id, limit=limit, offset=offset)
