"""分类配置模块测试。

本文件验证数据库配置可独立读取、统一配置入口可聚合全部字段，并确保旧导入路径
继续可用。
"""

from app.config import Settings, get_settings
from app.config.milvus_config import MilvusSettings
from app.config.minio_config import MinioSettings
from app.config.neo4j_config import Neo4jSettings
from app.config.postgres_config import PostgresSettings
from app.config.redis_config import RedisSettings
from app.core.config import Settings as LegacySettings


def test_database_settings_can_be_loaded_independently(monkeypatch) -> None:
    """验证每种数据库配置均可从自己的脚本独立读取环境变量。"""

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost:5432/papers")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9001")
    monkeypatch.setenv("MILVUS_DATABASE", "papers")
    monkeypatch.setenv("NEO4J_DATABASE", "papers")

    assert PostgresSettings().database_url.endswith("/papers")
    assert RedisSettings().redis_url.endswith("/1")
    assert MinioSettings().minio_endpoint == "localhost:9001"
    assert MilvusSettings().milvus_database == "papers"
    assert Neo4jSettings().neo4j_database == "papers"


def test_aggregated_settings_keep_existing_field_names() -> None:
    """验证统一配置保留原有字段名和 PostgreSQL 地址转换能力。"""

    settings = Settings(database_url="postgresql+asyncpg://user:password@localhost:5432/papers")

    assert settings.async_database_url == "postgresql://user:password@localhost:5432/papers"
    assert settings.redis_url
    assert settings.minio_endpoint
    assert settings.minio_paper_bucket == "papers"
    assert settings.milvus_uri
    assert settings.neo4j_uri
    assert settings.mineru_base_url


def test_legacy_config_import_reuses_new_settings() -> None:
    """验证旧配置模块仅转发新的统一配置对象。"""

    assert LegacySettings is Settings
    get_settings.cache_clear()
    assert isinstance(get_settings(), Settings)
