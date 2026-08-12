from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class IngestionStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    PARSING = "parsing"
    DOWNLOADING = "downloading"
    INGESTING = "ingesting"
    COMPLETED = "completed"
    FAILED = "failed"


class PaperMetadata(BaseModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    doi: str | None = None
    journal: str | None = None
    publish_year: int | None = None
    language: str = "zh"
    source_url: str | None = None
    geological_metadata: dict = Field(default_factory=dict)


class UrlIngestRequest(BaseModel):
    url: HttpUrl
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)
    enable_table: bool = True
    enable_formula: bool = True


class IngestionJobRead(BaseModel):
    id: str
    paper_uid: str
    file_name: str
    status: IngestionStatus
    progress: int
    message: str | None = None
    mineru_batch_id: str | None = None
    paper_id: int | None = None
    error: str | None = None


class PaperRead(BaseModel):
    id: int
    paper_uid: str
    title: str
    abstract: str | None = None
    authors: list = Field(default_factory=list)
    keywords: list = Field(default_factory=list)
    publish_year: int | None = None
    pdf_path: str | None = None
    markdown_path: str | None = None
    page_count: int | None = None
    section_count: int = 0
    text_chunk_count: int = 0
    image_count: int = 0
    table_count: int = 0
