"""Milvus 向量数据库配置。

本文件独立管理 Milvus 服务地址、令牌和目标数据库名称。
"""

from .base_config import BaseConfigSettings


class MilvusSettings(BaseConfigSettings):
    """定义 Milvus 向量数据库连接配置。"""

    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: str = ""
    milvus_database: str = "default"


# 保留与既有配置脚本一致的 Config 命名方式。
MilvusConfig = MilvusSettings
