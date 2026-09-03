"""Redis 数据库配置。

本文件独立管理 Redis 服务的连接地址。
"""

from .base_config import BaseConfigSettings


class RedisSettings(BaseConfigSettings):
    """定义 Redis 连接配置。"""

    redis_url: str = "redis://:123456789@127.0.0.1:6479/0"


# 保留与既有配置脚本一致的 Config 命名方式。
RedisConfig = RedisSettings
