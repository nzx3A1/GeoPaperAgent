"""Tests for minisweagent.__init__."""

import os
import subprocess
import sys


def test_startup_banner_survives_non_utf8_stdout(tmp_path):
    """Importing the package must not crash when stdout can't encode the startup banner (e.g. Windows cp1252)."""
    # 模拟 Windows 默认的 cp1252 编码环境,确保启动横幅在非 UTF-8 输出下不会触发 UnicodeEncodeError
    env = {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",  # 强制使用 Windows 旧版编码
        "MSWEA_SILENT_STARTUP": "",  # 关闭静默启动,触发横幅打印
        "MSWEA_GLOBAL_CONFIG_DIR": str(tmp_path),  # 隔离配置目录,避免污染全局
    }
    # 在子进程中仅执行 import,验证包级别的初始化逻辑
    result = subprocess.run([sys.executable, "-c", "import minisweagent"], capture_output=True, text=True, env=env)
    # returncode != 0 通常意味着打印横幅时编码失败抛出异常
    assert result.returncode == 0, result.stderr
