"""基础设施公共配置。

本文件保存所有数据库和存储客户端共用的连接参数。
"""

from .base_config import BaseConfigSettings


class InfrastructureSettings(BaseConfigSettings):
    """定义基础设施客户端共用的连接配置。"""

    infrastructure_connect_timeout: float = 10.0
