"""数据库数据模型共用的类型与基础配置。"""

from pydantic import BaseModel, ConfigDict, JsonValue

JsonArray = list[JsonValue]
JsonObject = dict[str, JsonValue]


class SchemaModel(BaseModel):
    """数据模型基类：禁止未知字段，并支持从对象属性读取数据。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)
