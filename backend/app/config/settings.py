"""后端配置统一入口。

本文件聚合各分类配置并提供带缓存的全局读取函数；实际字段定义位于对应的
独立配置脚本中。
"""

from functools import lru_cache

from .api_config import ApiSettings
from .document_config import DocumentSettings
from .infrastructure_config import InfrastructureSettings
from .milvus_config import MilvusSettings
from .mineru_config import MinerUSettings
from .minio_config import MinioSettings
from .neo4j_config import Neo4jSettings
from .postgres_config import PostgresSettings
from .redis_config import RedisSettings


class Settings(
    ApiSettings,
    PostgresSettings,
    RedisSettings,
    MinioSettings,
    MilvusSettings,
    Neo4jSettings,
    InfrastructureSettings,
    MinerUSettings,
    DocumentSettings,
):
    """聚合应用、数据库、外部服务和文档处理配置。"""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取并缓存完整的后端配置。"""

    return Settings()
