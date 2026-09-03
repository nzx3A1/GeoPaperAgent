"""文本块表（``text_chunk``）对应的 Pydantic 数据模型。"""

from datetime import datetime

from pydantic import Field

from app.schemas.base import JsonObject, SchemaModel


class TextChunkBase(SchemaModel):
    """论文文本块的基础字段。"""

    chunk_uid: str = Field(min_length=1, max_length=100, description="系统内部的文本块唯一标识")
    paper_id: int = Field(description="所属论文的数据库主键")
    section_id: int = Field(description="所属章节的数据库主键")
    chunk_index: int = Field(description="文本块在当前章节中的顺序编号")
    content: str = Field(description="文本块的正文内容")
    char_count: int | None = Field(default=None, description="文本块字符数量")
    previous_chunk_id: int | None = Field(default=None, description="上一个文本块的数据库主键")
    next_chunk_id: int | None = Field(default=None, description="下一个文本块的数据库主键")
    metadata: JsonObject | None = Field(default_factory=dict, description="文本块的其他扩展信息")


class TextChunkCreate(TextChunkBase):
    """创建文本块时使用的数据。"""


class TextChunkUpdate(SchemaModel):
    """更新文本块时使用的数据；只修改调用方明确传入的字段。"""

    chunk_uid: str | None = Field(default=None, min_length=1, max_length=100, description="文本块唯一标识")
    paper_id: int | None = Field(default=None, description="所属论文的数据库主键")
    section_id: int | None = Field(default=None, description="所属章节的数据库主键")
    chunk_index: int | None = Field(default=None, description="文本块顺序编号")
    content: str | None = Field(default=None, description="文本块正文内容")
    char_count: int | None = Field(default=None, description="文本块字符数量")
    previous_chunk_id: int | None = Field(default=None, description="上一个文本块的数据库主键")
    next_chunk_id: int | None = Field(default=None, description="下一个文本块的数据库主键")
    metadata: JsonObject | None = Field(default=None, description="文本块扩展信息")


class TextChunk(TextChunkBase):
    """从文本块表读取的完整数据。"""

    id: int = Field(description="数据库自增主键")
    created_at: datetime = Field(description="记录创建时间")
