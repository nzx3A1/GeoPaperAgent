from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(Path(__file__).parents[2] / ".env", ".env"), extra="ignore")

    app_name: str = "GeoPaperAgent API"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:5432/geopaperAgent"
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
