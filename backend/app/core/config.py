from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(Path(__file__).parents[2] / ".env", ".env"), extra="ignore")

    app_name: str = "GeoPaperAgent API"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Local defaults match backend/compose.yml. Override every secret in production.
    database_url: str = "postgresql://postgresql:123456789@127.0.0.1:5532/geopaperagent"
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 10
    redis_url: str = "redis://:123456789@127.0.0.1:6479/0"
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "geopaperagent"
    minio_secret_key: str = "123456789"
    minio_secure: bool = False
    minio_region: str = "us-east-1"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: str = ""
    milvus_database: str = "default"
    neo4j_uri: str = "bolt://127.0.0.1:7787"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "123456789"
    neo4j_database: str = "neo4j"
    infrastructure_connect_timeout: float = 10.0

    mineru_api_key: str = ""
    mineru_base_url: str = "https://mineru.net"
    mineru_model_version: str = "vlm"
    mineru_poll_interval: float = 3.0
    mineru_timeout: float = 120.0
    mineru_max_wait_seconds: int = 1800
    upload_dir: Path = Path("data/uploads")
    mineru_output_dir: Path = Path("data/mineru")
    chunk_size: int = 1800
    chunk_overlap: int = 200

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
