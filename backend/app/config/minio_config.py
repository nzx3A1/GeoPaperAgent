"""MinIO 对象存储配置。

本文件独立管理 MinIO 地址、访问凭据、传输协议和区域参数。
"""

from .base_config import BaseConfigSettings


class MinioSettings(BaseConfigSettings):
    """定义 MinIO 对象存储连接配置。"""

    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "geopaperagent"
    minio_secret_key: str = "123456789"
    minio_secure: bool = False
    minio_region: str = "us-east-1"
    minio_paper_bucket: str = "papers"


# 保留与既有配置脚本一致的 Config 命名方式。
MinioConfig = MinioSettings
