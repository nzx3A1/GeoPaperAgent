"""
[Bubblewrap](https://github.com/containers/bubblewrap) 是一个 Linux 上底层的非特权沙箱工具，
它可以在隔离的环境中运行应用程序，限制其对操作系统和用户数据的访问。
该环境使用 bubblewrap 在沙箱环境中执行命令。

!!! warning
    该环境为实验性功能。

!!! warning
    该环境在 Windows 上不受支持。
"""

import logging
import os
import platform
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from minisweagent.exceptions import Submitted
from minisweagent.utils.serialize import recursive_merge


class BubblewrapEnvironmentConfig(BaseModel):
    cwd: str = ""
    """沙箱的工作目录。"""
    env: dict[str, str] = {}
    """要在沙箱内设置的环境变量字典。"""
    timeout: int = 30
    """命令的超时时间（秒）。"""
    executable: str = os.getenv("MSWEA_BUBBLEWRAP_EXECUTABLE", "bwrap")
    """bubblewrap 可执行文件的路径。"""
    wrapper_args: list[str] = [
        "--unshare-user-try",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/etc",
        "/etc",
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--new-session",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/sbin:/usr/bin:/bin",
    ]
    """传递给 bubblewrap 可执行文件的参数。"""


class BubblewrapEnvironment:
    def __init__(
        self, *, config_class: type = BubblewrapEnvironmentConfig, logger: logging.Logger | None = None, **kwargs
    ):
        """该类在 bubblewrap 环境和每个环境的独立工作目录中执行 bash 命令。
        关键字参数请参见 `BubblewrapEnvironmentConfig`。
        """
        self.logger = logger or logging.getLogger("minisweagent.environment")
        self.config = config_class(**kwargs)
        self.working_dir = Path(tempfile.gettempdir()) / f"minisweagent-{uuid.uuid4().hex[:8]}"
        self.working_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """在 bubblewrap 环境中执行命令并以字典形式返回结果。"""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd or str(self.working_dir)

        cmd = [self.config.executable] + self.config.wrapper_args + ["--bind", cwd, cwd, "--chdir", cwd]

        # 添加环境变量
        for key, value in self.config.env.items():
            cmd.extend(["--setenv", key, value])

        cmd.extend(["bash", "-c", command])

        try:
            result = subprocess.run(
                cmd,
                text=True,
                timeout=timeout or self.config.timeout,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = {"output": result.stdout, "returncode": result.returncode, "exception_info": ""}
        except Exception as e:
            raw_output = getattr(e, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict):
        """如果输出指示任务完成，则抛出 Submitted。"""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and output["returncode"] == 0:
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )

    def cleanup(self):
        if self.working_dir.exists():
            shutil.rmtree(self.working_dir)

    def __del__(self):
        """对象销毁时清理 working_dir。"""
        self.cleanup()

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return recursive_merge(self.config.model_dump(), platform.uname()._asdict(), kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }
