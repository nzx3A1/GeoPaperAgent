"""Neo4j 图数据库配置。

本文件独立管理 Neo4j 服务地址、登录凭据和目标数据库名称。
"""

from functools import lru_cache

from .base_config import BaseConfigSettings


class Neo4jSettings(BaseConfigSettings):
    """定义 Neo4j 图数据库连接配置。"""

    neo4j_uri: str = "bolt://127.0.0.1:7787"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "123456789"
    neo4j_database: str = "neo4j"

    @property
    def uri(self) -> str:
        """兼容旧代码使用的简写服务地址属性。"""

        return self.neo4j_uri

    @property
    def username(self) -> str:
        """兼容旧代码使用的简写用户名属性。"""

        return self.neo4j_username

    @property
    def password(self) -> str:
        """兼容旧代码使用的简写密码属性。"""

        return self.neo4j_password

    @property
    def database(self) -> str:
        """兼容旧代码使用的简写数据库名属性。"""

        return self.neo4j_database


Neo4jConfig = Neo4jSettings


@lru_cache(maxsize=1)
def load_neo4j_settings() -> Neo4jSettings:
    """读取并缓存 Neo4j 配置。"""

    return Neo4jSettings()
