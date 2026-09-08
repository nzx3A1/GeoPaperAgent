"""数据库数据模型共用的类型、向量类型与基础配置。"""

from typing import Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, JsonValue
from typing_extensions import Annotated

JsonArray = list[JsonValue]
JsonObject = dict[str, JsonValue]


def _parse_embedding(value: Any) -> list[float] | None:
    """把 asyncpg 返回的 pgvector 字符串转换为浮点数列表。"""

    if value is None:
        return None
    if isinstance(value, str):
        content = value.strip()
        if content.startswith("[") and content.endswith("]"):
            content = content[1:-1].strip()
        if not content:
            return []
        return [float(item.strip()) for item in content.split(",")]
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    raise TypeError(f"不支持的 embedding 类型：{type(value).__name__}")


EmbeddingVector = Annotated[list[float] | None, BeforeValidator(_parse_embedding)]


class SchemaModel(BaseModel):
    """数据模型基类：禁止未知字段，并支持从对象属性读取数据。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)
