"""章节表数据持久层。"""

from app.infrastructure.postgres_client import PostgresClient
from app.repositories.base import BaseRepository
from app.schemas import Section, SectionCreate, SectionUpdate

SECTION_COLUMNS = (
    "section_uid", "paper_id", "parent_id", "section_number", "title", "level", "sort_order", "section_path",
    "raw_text", "summary",
)


class SectionRepository(BaseRepository[SectionCreate, SectionUpdate, Section]):
    """提供论文章节数据的基础增删改查操作。"""

    def __init__(self, client: PostgresClient | None = None) -> None:
        super().__init__(
            table="section",
            row_model=Section,
            create_columns=SECTION_COLUMNS,
            update_columns=SECTION_COLUMNS,
            json_columns=frozenset({"section_path"}),
            non_nullable_columns=frozenset({"section_uid", "paper_id", "title", "level", "sort_order"}),
            uid_column="section_uid",
            order_by=("sort_order ASC", "id ASC"),
            client=client,
        )

    async def list_by_paper(self, paper_id: int, *, limit: int = 100, offset: int = 0) -> list[Section]:
        """按章节顺序分页查询一篇论文的章节。"""
        return await self._list_by("paper_id", paper_id, limit=limit, offset=offset)

    async def list_by_parent(self, parent_id: int, *, limit: int = 100, offset: int = 0) -> list[Section]:
        """分页查询指定父章节下的直接子章节。"""
        return await self._list_by("parent_id", parent_id, limit=limit, offset=offset)
