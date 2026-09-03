"""MinerU 文档解析服务配置。

本文件管理 MinerU API 鉴权、服务地址、模型版本、轮询和超时参数。
"""

from .base_config import BaseConfigSettings


class MinerUSettings(BaseConfigSettings):
    """定义 MinerU 云端文档解析服务配置。"""

    mineru_api_key: str = "sk-zdeqRrVvzMnlLAQq7NMBSj61QxDo8FBFbyTMHHhky36EYje4"
    mineru_base_url: str = "https://mineru.net"
    mineru_model_version: str = "vlm"
    mineru_poll_interval: float = 3.0
    mineru_timeout: float = 120.0
    mineru_max_wait_seconds: int = 1800


# 保留旧类名，避免仍使用 MinerUConfig 的调用方需要同步修改。
MinerUConfig = MinerUSettings

__all__ = ["MinerUConfig", "MinerUSettings"]
