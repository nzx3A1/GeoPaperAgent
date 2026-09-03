"""文本块表数据持久层。"""

from app.infrastructure.postgres_client import PostgresClient
from app.repositories.base import BaseRepository
from app.schemas import TextChunk, TextChunkCreate, TextChunkUpdate

TEXT_CHUNK_COLUMNS = (
    "chunk_uid", "paper_id", "section_id", "chunk_index", "content", "char_count", "previous_chunk_id",
    "next_chunk_id", "metadata",
)


class TextChunkRepository(BaseRepository[TextChunkCreate, TextChunkUpdate, TextChunk]):
    """提供论文文本块数据的基础增删改查操作。"""

    def __init__(self, client: PostgresClient | None = None) -> None:
        super().__init__(
            table="text_chunk",
            row_model=TextChunk,
            create_columns=TEXT_CHUNK_COLUMNS,
            update_columns=TEXT_CHUNK_COLUMNS,
            json_columns=frozenset({"metadata"}),
            non_nullable_columns=frozenset({"chunk_uid", "paper_id", "section_id", "chunk_index", "content"}),
            uid_column="chunk_uid",
            order_by=("section_id ASC", "chunk_index ASC", "id ASC"),
            client=client,
        )

    async def list_by_paper(self, paper_id: int, *, limit: int = 100, offset: int = 0) -> list[TextChunk]:
        """分页查询一篇论文的文本块。"""
        return await self._list_by("paper_id", paper_id, limit=limit, offset=offset)

    async def list_by_section(self, section_id: int, *, limit: int = 100, offset: int = 0) -> list[TextChunk]:
        """按顺序分页查询一个章节的文本块。"""
        return await self._list_by("section_id", section_id, limit=limit, offset=offset)
