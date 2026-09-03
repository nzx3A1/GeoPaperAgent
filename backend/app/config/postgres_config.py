"""PostgreSQL 数据库配置。

本文件独立管理 PostgreSQL 连接地址和 asyncpg 连接池参数。
"""

from .base_config import BaseConfigSettings


class PostgresSettings(BaseConfigSettings):
    """定义 PostgreSQL 连接及连接池配置。"""

    database_url: str = "postgresql://postgresql:123456789@127.0.0.1:5532/geopaperagent"
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 10

    @property
    def async_database_url(self) -> str:
        """将 SQLAlchemy 风格驱动前缀转换为 asyncpg 可接受的连接地址。"""

        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )


# 保留与既有配置脚本一致的 Config 命名方式。
PostgresConfig = PostgresSettings
