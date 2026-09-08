"""基础设施客户端的兼容导出入口，并按需加载重量级数据库依赖。"""

from __future__ import annotations

from importlib import import_module

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "MilvusClient": ("app.infrastructure.milvus_client", "MilvusClient"),
    "get_milvus_client": ("app.infrastructure.milvus_client", "get_milvus_client"),
    "MinioClient": ("app.infrastructure.minio_client", "MinioClient"),
    "get_minio_client": ("app.infrastructure.minio_client", "get_minio_client"),
    "Neo4jClient": ("app.infrastructure.neo4j_client", "Neo4jClient"),
    "get_neo4j_client": ("app.infrastructure.neo4j_client", "get_neo4j_client"),
    "PostgresClient": ("app.infrastructure.postgres_client", "PostgresClient"),
    "get_postgres_client": ("app.infrastructure.postgres_client", "get_postgres_client"),
    "RedisClient": ("app.infrastructure.redis_client", "RedisClient"),
    "get_redis_client": ("app.infrastructure.redis_client", "get_redis_client"),
    "close_infrastructure": ("app.infrastructure.clients", "close_infrastructure"),
    "connect_infrastructure": ("app.infrastructure.clients", "connect_infrastructure"),
    "healthcheck_infrastructure": ("app.infrastructure.clients", "healthcheck_infrastructure"),
    "infrastructure_lifespan": ("app.infrastructure.clients", "infrastructure_lifespan"),
}


def __getattr__(name: str) -> object:
    """按访问名称延迟导入基础设施客户端，避免无关依赖提前占用内存。"""

    import_path = _LAZY_IMPORTS.get(name)
    if import_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(import_path[0])
    value = getattr(module, import_path[1])
    globals()[name] = value
    return value

__all__ = list(_LAZY_IMPORTS)
