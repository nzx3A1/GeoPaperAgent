"""Web API 应用配置。

本文件保存应用名称、API 路由前缀和跨域来源等与具体数据库无关的配置。
"""

from pydantic import Field

from .base_config import BaseConfigSettings


class ApiSettings(BaseConfigSettings):
    """定义 GeoPaperAgent Web API 的基础运行参数。"""

    app_name: str = "GeoPaperAgent API"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
