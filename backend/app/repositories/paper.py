"""论文表数据持久层。"""

from app.infrastructure.postgres_client import PostgresClient
from app.repositories.base import BaseRepository
from app.schemas import Paper, PaperCreate, PaperUpdate

PAPER_COLUMNS = (
    "paper_uid",
    "title",
    "title_en",
    "abstract",
    "authors",
    "keywords",
    "affiliations",
    "doi",
    "journal",
    "volume",
    "issue",
    "publish_year",
    "publish_date",
    "language",
    "source_url",
    "pdf_path",
    "markdown_path",
    "document_json_path",
    "ingestion_status",
    "error_message",
    "page_count",
    "file_metadata",
)


class PaperRepository(BaseRepository[PaperCreate, PaperUpdate, Paper]):
    """提供论文数据的基础增删改查操作。"""

    def __init__(self, client: PostgresClient | None = None) -> None:
        super().__init__(
            table="paper",
            row_model=Paper,
            create_columns=PAPER_COLUMNS,
            update_columns=PAPER_COLUMNS,
            json_columns=frozenset({"authors", "keywords", "affiliations", "file_metadata"}),
            non_nullable_columns=frozenset({"paper_uid", "title", "ingestion_status"}),
            uid_column="paper_uid",
            order_by=("created_at DESC", "id DESC"),
            touch_updated_at=True,
            client=client,
        )

    async def get_by_doi(self, doi: str) -> Paper | None:
        """按 DOI 查询论文。"""
        rows = await self._list_by("doi", doi, limit=1)
        return rows[0] if rows else None
