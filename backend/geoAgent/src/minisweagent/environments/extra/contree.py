import logging
import os
import platform
import shlex
from dataclasses import asdict, is_dataclass
from numbers import Number
from typing import Any, NotRequired, TypedDict

from contree_sdk import ContreeSync
from contree_sdk.config import ContreeConfig
from contree_sdk.sdk.objects.image import ContreeImageSync
from pydantic import BaseModel

from minisweagent import Environment
from minisweagent.exceptions import Submitted
from minisweagent.utils.serialize import recursive_merge

logger = logging.getLogger(__name__)


class ContreeEnvironmentConfig(BaseModel):
    contree_config: ContreeConfig | dict[str, Any]

    image: str
    image_tag: str = None
    """如果设置了，则按 tag 拉取镜像。如果失败，则按 `image` 导入，并将 `image_tag` 设为镜像的 tag。"""
    cwd: str = "/"
    """在其中执行命令的工作目录。"""
    cwd_auto_create: bool = True
    """在运行任何命令之前创建 cwd。"""
    env: dict[str, str] = {}
    """要在容器内设置的环境变量。"""
    forward_env: list[str] = []
    """要转发到容器的环境变量。
    只有在主机环境中设置了这些变量时才会被转发。
    如果与 `env` 冲突，`env` 中的变量优先。
    """
    interpreter: list[str] = ["bash", "-c"]
    """用于执行命令的解释器。"""
    timeout: int = 100
    """在容器中执行命令的超时时间。"""
    import_username: str | None = None
    """当镜像需要被导入时使用的用户名。"""
    import_password: str | None = None
    """当镜像需要被导入时使用的密码。"""


class ExecutionResult(TypedDict):
    output: str
    returncode: int
    exception_info: str
    extra: NotRequired[dict[str, Any]]


class ContreeEnvironment(Environment):
    def __init__(self, *, config_class: type[ContreeEnvironmentConfig] = ContreeEnvironmentConfig, **kwargs):
        """该类在 [ConTree](https://contree.dev) 容器中使用
        [contree-sdk](https://github.com/nebius/contree-sdk) 执行 bash 命令。"""

        self.config: ContreeEnvironmentConfig = config_class(**kwargs)
        self.logger = logging.getLogger("minisweagent.environment")

        if isinstance(self.config.contree_config, dict):
            self.config = self.config.model_copy(update={"contree_config": ContreeConfig(**self.config.contree_config)})

        self.client = ContreeSync(config=self.config.contree_config)
        self.session = self._pull_image().session()
        if self.config.cwd_auto_create:
            self.execute(
                action={"command": f"mkdir -p {self.config.cwd}"},
                cwd="/",
            )

    def _pull_image(self) -> ContreeImageSync:
        return self.client.images.oci(
            self.config.image,
            tag=self.config.image_tag,
            username=self.config.import_username,
            password=self.config.import_password,
        )

    def _shell_command(self, command: str) -> str:
        shell_cmd = " ".join(self.config.interpreter)
        return f"{shell_cmd} {shlex.quote(command)}"

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> ExecutionResult:
        """在环境中执行命令并返回原始输出。"""
        command = action.get("command")

        env = {k: v for k in self.config.forward_env if (v := os.getenv(k)) is not None}
        env.update(self.config.env)

        try:
            self.session.run(
                shell=self._shell_command(command),
                cwd=cwd or self.config.cwd,
                timeout=timeout or self.config.timeout,
                disposable=False,
                env=env or None,
            ).wait()
            output = {
                "output": self.session.stdout + self.session.stderr,
                "returncode": self.session.exit_code,
                "exception_info": "",
            }
        except Exception as e:
            raw_output = getattr(e, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            )
            extras = {}
            if is_dataclass(e):
                extras = {k: str(v) if not isinstance(v, Number) else v for k, v in asdict(e).items()}

            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e), **extras},
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
