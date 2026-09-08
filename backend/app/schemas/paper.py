"""论文表（``paper``）对应的 Pydantic 数据模型。"""

from datetime import date, datetime

from pydantic import Field

from app.schemas.base import EmbeddingVector, JsonArray, JsonObject, SchemaModel


class PaperBase(SchemaModel):
    """论文的基础字段。"""

    paper_uid: str = Field(min_length=1, max_length=64, description="系统内部的论文唯一标识")
    title: str = Field(min_length=1, description="论文原始标题")
    title_en: str | None = Field(default=None, description="论文英文标题")
    abstract: str | None = Field(default=None, description="论文摘要")
    authors: JsonArray | None = Field(default_factory=list, description="作者信息列表")
    keywords: JsonArray | None = Field(default_factory=list, description="论文关键词列表")
    affiliations: JsonArray | None = Field(default_factory=list, description="作者所属单位列表")
    doi: str | None = Field(default=None, max_length=255, description="数字对象唯一标识符（DOI）")
    journal: str | None = Field(default=None, max_length=255, description="发表期刊名称")
    volume: str | None = Field(default=None, max_length=50, description="期刊卷号")
    issue: str | None = Field(default=None, max_length=50, description="期刊期号")
    publish_year: int | None = Field(default=None, description="发表年份")
    publish_date: date | None = Field(default=None, description="发表日期")
    language: str | None = Field(default=None, max_length=20, description="论文语言代码，如 zh、en")
    source_url: str | None = Field(default=None, description="论文来源网页或下载地址")
    pdf_path: str | None = Field(default=None, description="原始 PDF 文件的存储路径")
    markdown_path: str | None = Field(default=None, description="MinerU 解析生成的 Markdown 文件路径")
    document_json_path: str | None = Field(default=None, description="章节树和多模态 Chunk JSON 的存储路径")
    ingestion_status: str = Field(default="pending", max_length=32, description="论文解析入库流程的当前状态")
    error_message: str | None = Field(default=None, description="最近一次解析入库失败的错误信息")
    page_count: int | None = Field(default=None, description="论文 PDF 的总页数")
    file_metadata: JsonObject | None = Field(default_factory=dict, description="文件大小、哈希值等扩展信息")


class PaperCreate(PaperBase):
    """创建论文时使用的数据；主键和时间由 PostgreSQL 自动生成。"""


class PaperUpdate(SchemaModel):
    """更新论文时使用的数据；只修改调用方明确传入的字段。"""

    paper_uid: str | None = Field(default=None, min_length=1, max_length=64, description="系统内部的论文唯一标识")
    title: str | None = Field(default=None, min_length=1, description="论文原始标题")
    title_en: str | None = Field(default=None, description="论文英文标题")
    abstract: str | None = Field(default=None, description="论文摘要")
    authors: JsonArray | None = Field(default=None, description="作者信息列表")
    keywords: JsonArray | None = Field(default=None, description="论文关键词列表")
    affiliations: JsonArray | None = Field(default=None, description="作者所属单位列表")
    doi: str | None = Field(default=None, max_length=255, description="数字对象唯一标识符（DOI）")
    journal: str | None = Field(default=None, max_length=255, description="发表期刊名称")
    volume: str | None = Field(default=None, max_length=50, description="期刊卷号")
    issue: str | None = Field(default=None, max_length=50, description="期刊期号")
    publish_year: int | None = Field(default=None, description="发表年份")
    publish_date: date | None = Field(default=None, description="发表日期")
    language: str | None = Field(default=None, max_length=20, description="论文语言代码，如 zh、en")
    source_url: str | None = Field(default=None, description="论文来源网页或下载地址")
    pdf_path: str | None = Field(default=None, description="原始 PDF 文件的存储路径")
    markdown_path: str | None = Field(default=None, description="MinerU 解析生成的 Markdown 文件路径")
    document_json_path: str | None = Field(default=None, description="章节树和多模态 Chunk JSON 的存储路径")
    ingestion_status: str | None = Field(default=None, max_length=32, description="论文解析入库流程的当前状态")
    error_message: str | None = Field(default=None, description="最近一次解析入库失败的错误信息")
    page_count: int | None = Field(default=None, description="论文 PDF 的总页数")
    file_metadata: JsonObject | None = Field(default=None, description="文件大小、哈希值等扩展信息")


class Paper(PaperBase):
    """从论文表读取的完整数据。"""

    id: int = Field(description="数据库自增主键")
    embedding: EmbeddingVector = Field(default=None, description="qwen3-embedding:0.6b 生成的 1024 维论文向量")
    created_at: datetime = Field(description="记录创建时间")
    updated_at: datetime = Field(description="记录最后更新时间")
