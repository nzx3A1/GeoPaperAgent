"""配置模块的公共基础定义。

本文件统一声明后端环境文件位置和 Pydantic Settings 的通用读取规则，供各类
配置脚本复用。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_FILES = (BACKEND_ROOT / ".env", Path(".env"))


class BaseConfigSettings(BaseSettings):
    """为所有分类配置提供统一的环境变量读取规则。"""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")
