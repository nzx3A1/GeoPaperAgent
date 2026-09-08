"""表格块表（``table_chunk``）对应的 Pydantic 数据模型。"""

from datetime import datetime

from pydantic import Field, JsonValue

from app.schemas.base import EmbeddingVector, JsonObject, SchemaModel


class TableChunkBase(SchemaModel):
    """论文表格块的基础字段。"""

    chunk_uid: str = Field(min_length=1, max_length=100, description="系统内部的表格块唯一标识")
    paper_id: int = Field(description="所属论文的数据库主键")
    section_id: int = Field(description="所属章节的数据库主键")
    table_index: int | None = Field(default=None, description="表格在当前论文或章节中的顺序编号")
    table_number: str | None = Field(default=None, max_length=50, description="论文中的表号，如 Table 2、表2")
    caption: str | None = Field(default=None, description="表格标题或表注")
    page_number: int | None = Field(default=None, description="表格所在的 PDF 页码")
    bbox: JsonValue = Field(default=None, description="表格在 PDF 页面中的边界框坐标")
    table_html: str | None = Field(default=None, description="表格的 HTML 内容")
    structured_data: JsonObject | None = Field(default_factory=dict, description="表格的结构化数据")
    description: str | None = Field(default=None, description="表格内容的自然语言描述")
    metadata: JsonObject | None = Field(default_factory=dict, description="表格块的其他扩展信息")


class TableChunkCreate(TableChunkBase):
    """创建表格块时使用的数据。"""


class TableChunkUpdate(SchemaModel):
    """更新表格块时使用的数据；只修改调用方明确传入的字段。"""

    chunk_uid: str | None = Field(default=None, min_length=1, max_length=100, description="表格块唯一标识")
    paper_id: int | None = Field(default=None, description="所属论文的数据库主键")
    section_id: int | None = Field(default=None, description="所属章节的数据库主键")
    table_index: int | None = Field(default=None, description="表格顺序编号")
    table_number: str | None = Field(default=None, max_length=50, description="论文中的表号")
    caption: str | None = Field(default=None, description="表格标题或表注")
    page_number: int | None = Field(default=None, description="表格所在的 PDF 页码")
    bbox: JsonValue = Field(default=None, description="表格边界框坐标")
    table_html: str | None = Field(default=None, description="表格 HTML 内容")
    structured_data: JsonObject | None = Field(default=None, description="表格结构化数据")
    description: str | None = Field(default=None, description="表格内容描述")
    metadata: JsonObject | None = Field(default=None, description="表格块扩展信息")


class TableChunk(TableChunkBase):
    """从表格块表读取的完整数据。"""

    id: int = Field(description="数据库自增主键")
    embedding: EmbeddingVector = Field(default=None, description="qwen3-embedding:0.6b 生成的 1024 维表格描述向量")
    created_at: datetime = Field(description="记录创建时间")
