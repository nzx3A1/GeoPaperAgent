"""图片块表（``image_chunk``）对应的 Pydantic 数据模型。"""

from datetime import datetime

from pydantic import Field

from app.schemas.base import EmbeddingVector, JsonObject, SchemaModel


class ImageChunkBase(SchemaModel):
    """论文图片块的基础字段。"""

    chunk_uid: str = Field(min_length=1, max_length=100, description="系统内部的图片块唯一标识")
    paper_id: int = Field(description="所属论文的数据库主键")
    section_id: int = Field(description="所属章节的数据库主键")
    image_index: int | None = Field(default=None, description="图片在当前论文或章节中的顺序编号")
    figure_number: str | None = Field(default=None, max_length=50, description="论文中的图号，如 Fig.5、图5")
    image_path: str = Field(description="图片文件的存储路径")
    caption: str | None = Field(default=None, description="图片标题或图注")
    image_type: str | None = Field(default=None, max_length=100, description="图片类型，如地层柱状图、地质剖面图")
    description: str | None = Field(default=None, description="图片内容的自然语言描述")
    structured_data: JsonObject | None = Field(default_factory=dict, description="图片的结构化解析结果")
    metadata: JsonObject | None = Field(default_factory=dict, description="图片块的其他扩展信息")


class ImageChunkCreate(ImageChunkBase):
    """创建图片块时使用的数据。"""


class ImageChunkUpdate(SchemaModel):
    """更新图片块时使用的数据；只修改调用方明确传入的字段。"""

    chunk_uid: str | None = Field(default=None, min_length=1, max_length=100, description="图片块唯一标识")
    paper_id: int | None = Field(default=None, description="所属论文的数据库主键")
    section_id: int | None = Field(default=None, description="所属章节的数据库主键")
    image_index: int | None = Field(default=None, description="图片顺序编号")
    figure_number: str | None = Field(default=None, max_length=50, description="论文中的图号")
    image_path: str | None = Field(default=None, description="图片文件存储路径")
    caption: str | None = Field(default=None, description="图片标题或图注")
    image_type: str | None = Field(default=None, max_length=100, description="图片类型")
    description: str | None = Field(default=None, description="图片内容描述")
    structured_data: JsonObject | None = Field(default=None, description="图片结构化解析结果")
    metadata: JsonObject | None = Field(default=None, description="图片块扩展信息")


class ImageChunk(ImageChunkBase):
    """从图片块表读取的完整数据。"""

    id: int = Field(description="数据库自增主键")
    embedding: EmbeddingVector = Field(default=None, description="qwen3-embedding:0.6b 生成的 1024 维图片描述向量")
    created_at: datetime = Field(description="记录创建时间")
