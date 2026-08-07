import logging
import os
import platform
import shlex
import subprocess
import uuid
from typing import Any

from pydantic import BaseModel

from minisweagent.exceptions import Submitted
from minisweagent.utils.serialize import recursive_merge


class DockerEnvironmentConfig(BaseModel):
    image: str
    cwd: str = "/"
    """在其中执行命令的工作目录。"""
    env: dict[str, str] = {}
    """要在容器内设置的环境变量。"""
    forward_env: list[str] = []
    """要转发到容器的环境变量。
    只有在主机环境中设置了这些变量时才会被转发。
    如果与 `env` 冲突，`env` 中的变量优先。
    """
    timeout: int = 30
    """在容器中执行命令的超时时间。"""
    executable: str = os.getenv("MSWEA_DOCKER_EXECUTABLE", "docker")
    """docker/container 可执行文件的路径。"""
    run_args: list[str] = ["--rm"]
    """传递给 docker/container 可执行文件的额外参数。
    默认值为 ["--rm"]，即在容器退出后将其删除。
    """
    container_timeout: str = "2h"
    """容器保持运行的最长时间。使用与 sleep 命令相同的格式。"""
    pull_timeout: int = 120
    """拉取镜像的超时时间（秒）。"""
    interpreter: list[str] = ["bash", "-lc"]
    """用于执行命令的解释器。默认值为 ["bash", "-lc"]。
    实际命令将作为参数附加到它后面。可以重写此项以修改 shell 标志
    （例如去掉 `-l` 标志以禁用登录 shell），或使用 python 而不是 bash 来解释命令。
    """


class DockerEnvironment:
    def __init__(
        self,
        *,
        config_class: type = DockerEnvironmentConfig,
        logger: logging.Logger | None = None,
        **kwargs,
    ):
        """该类使用直接的 docker 命令在 Docker 容器中执行 bash 命令。
        有关关键字参数，请参见 `DockerEnvironmentConfig`。
        """
        self.logger = logger or logging.getLogger("minisweagent.environment")
        self.container_id: str | None = None
        self.config = config_class(**kwargs)
        self._start_container()

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

    def _start_container(self):
        """启动 Docker 容器并返回容器 ID。"""
        container_name = f"minisweagent-{uuid.uuid4().hex[:8]}"
        cmd = [
            self.config.executable,
            "run",
            "-d",
            "--name",
            container_name,
            "-w",
            self.config.cwd,
            *self.config.run_args,
            self.config.image,
            "sleep",
            self.config.container_timeout,
        ]
        self.logger.debug(f"Starting container with command: {shlex.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.pull_timeout,  # docker pull 可能需要一些时间
            check=True,
        )
        self.logger.info(f"Started container {container_name} with ID {result.stdout.strip()}")
        self.container_id = result.stdout.strip()

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """在 Docker 容器中执行命令并以字典形式返回结果。"""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd
        assert self.container_id, "Container not started"

        cmd = [self.config.executable, "exec", "-w", cwd]
        for key in self.config.forward_env:
            if (value := os.getenv(key)) is not None:
                cmd.extend(["-e", f"{key}={value}"])
        for key, value in self.config.env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([self.container_id, *self.config.interpreter, command])

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
        """停止并删除 Docker 容器。"""
        if getattr(self, "container_id", None) is not None:  # 如果 init 早期失败，container_id 可能尚未设置
            cmd = f"(timeout 60 {self.config.executable} stop {self.container_id} || {self.config.executable} rm -f {self.container_id}) >/dev/null 2>&1 &"
            subprocess.Popen(cmd, shell=True)

    def __del__(self):
        """对象销毁时清理容器。"""
        self.cleanup()
