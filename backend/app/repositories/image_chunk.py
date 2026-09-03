"""图片块表数据持久层。"""

from app.infrastructure.postgres_client import PostgresClient
from app.repositories.base import BaseRepository
from app.schemas import ImageChunk, ImageChunkCreate, ImageChunkUpdate

IMAGE_CHUNK_COLUMNS = (
    "chunk_uid", "paper_id", "section_id", "image_index", "figure_number", "image_path", "caption", "image_type",
    "description", "structured_data", "metadata",
)


class ImageChunkRepository(BaseRepository[ImageChunkCreate, ImageChunkUpdate, ImageChunk]):
    """提供论文图片块数据的基础增删改查操作。"""

    def __init__(self, client: PostgresClient | None = None) -> None:
        super().__init__(
            table="image_chunk",
            row_model=ImageChunk,
            create_columns=IMAGE_CHUNK_COLUMNS,
            update_columns=IMAGE_CHUNK_COLUMNS,
            json_columns=frozenset({"structured_data", "metadata"}),
            non_nullable_columns=frozenset({"chunk_uid", "paper_id", "section_id", "image_path"}),
            uid_column="chunk_uid",
            order_by=("image_index ASC NULLS LAST", "id ASC"),
            client=client,
        )

    async def list_by_paper(self, paper_id: int, *, limit: int = 100, offset: int = 0) -> list[ImageChunk]:
        """分页查询一篇论文的图片块。"""
        return await self._list_by("paper_id", paper_id, limit=limit, offset=offset)

    async def list_by_section(self, section_id: int, *, limit: int = 100, offset: int = 0) -> list[ImageChunk]:
        """分页查询一个章节的图片块。"""
        return await self._list_by("section_id", section_id, limit=limit, offset=offset)
