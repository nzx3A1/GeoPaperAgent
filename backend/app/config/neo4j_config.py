"""Neo4j 数据库连接配置。

配置从环境变量或 .env 读取，默认值仅用于本地开发。写入器可分别使用 schema
数据库和 document 数据库存放概念 schema 与论文抽取结果。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .model_config import PROJECT_ROOT, _load_env_file


_load_env_file(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Neo4jConfig:
    """单个 Neo4j 数据库连接配置。"""

    uri: str = "bolt://localhost:7687"
    username: str = "neo4j"
    password: str = "123456789"
    database: str = "petrommkg-schema"




