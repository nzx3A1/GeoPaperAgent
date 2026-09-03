"""论文文档处理配置。

本文件管理上传与解析输出目录，以及文本切块大小和重叠长度。
"""

from pathlib import Path

from .base_config import BaseConfigSettings


class DocumentSettings(BaseConfigSettings):
    """定义论文文件目录和文本切块参数。"""

    upload_dir: Path = Path("data/uploads")
    mineru_output_dir: Path = Path("data/mineru")
    chunk_size: int = 1800
    chunk_overlap: int = 200
