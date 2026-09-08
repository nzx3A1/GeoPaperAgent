"""章节表（``section``）对应的 Pydantic 数据模型。"""

from datetime import datetime

from pydantic import Field

from app.schemas.base import EmbeddingVector, JsonArray, SchemaModel


class SectionBase(SchemaModel):
    """论文章节的基础字段。"""

    section_uid: str = Field(min_length=1, max_length=100, description="系统内部的章节唯一标识")
    paper_id: int = Field(description="所属论文的数据库主键")
    parent_id: int | None = Field(default=None, description="父章节主键；一级章节为空")
    section_number: str | None = Field(default=None, max_length=50, description="章节编号，如 3、3.2、3.2.1")
    title: str = Field(min_length=1, description="章节标题")
    level: int = Field(description="章节层级；一级标题为 1，二级标题为 2")
    sort_order: int = Field(description="章节在整篇论文中的排序序号")
    section_path: JsonArray | None = Field(default_factory=list, description="从根章节到当前章节的层级路径")
    raw_text: str | None = Field(default=None, description="章节完整原始文本")
    summary: str | None = Field(default=None, description="章节摘要")


class SectionCreate(SectionBase):
    """创建章节时使用的数据。"""


class SectionUpdate(SchemaModel):
    """更新章节时使用的数据；只修改调用方明确传入的字段。"""

    section_uid: str | None = Field(default=None, min_length=1, max_length=100, description="章节唯一标识")
    paper_id: int | None = Field(default=None, description="所属论文的数据库主键")
    parent_id: int | None = Field(default=None, description="父章节主键；一级章节为空")
    section_number: str | None = Field(default=None, max_length=50, description="章节编号")
    title: str | None = Field(default=None, min_length=1, description="章节标题")
    level: int | None = Field(default=None, description="章节层级")
    sort_order: int | None = Field(default=None, description="章节排序序号")
    section_path: JsonArray | None = Field(default=None, description="章节层级路径")
    raw_text: str | None = Field(default=None, description="章节完整原始文本")
    summary: str | None = Field(default=None, description="章节摘要")


class Section(SectionBase):
    """从章节表读取的完整数据。"""

    id: int = Field(description="数据库自增主键")
    embedding: EmbeddingVector = Field(default=None, description="qwen3-embedding:0.6b 生成的 1024 维章节向量")
    created_at: datetime = Field(description="记录创建时间")
