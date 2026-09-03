"""旧配置导入路径的兼容模块。

配置已经迁移到 app.config 包；本文件仅保留原有导入路径，避免旧业务代码失效。
"""

from app.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
