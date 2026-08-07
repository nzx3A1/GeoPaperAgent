import asyncio
from typing import Any

from pydantic import BaseModel
from swerex.deployment.docker import DockerDeployment
from swerex.runtime.abstract import Command as RexCommand

from minisweagent.exceptions import Submitted
from minisweagent.utils.serialize import recursive_merge


class SwerexDockerEnvironmentConfig(BaseModel):
    image: str
    cwd: str = "/"
    """在其中执行命令的工作目录。"""
    timeout: int = 30
    """在容器中执行命令的超时时间。"""
    deployment_extra_kwargs: dict[str, Any] = {}
    """传递给 DockerDeployment 的额外关键字参数。"""


class SwerexDockerEnvironment:
    def __init__(self, **kwargs):
        """该类使用 SWE-ReX 进行沙箱化，在 Docker 容器中执行 bash 命令。"""
        self.config = SwerexDockerEnvironmentConfig(**kwargs)
        self.deployment = DockerDeployment(image=self.config.image, **self.config.deployment_extra_kwargs)
        asyncio.run(self.deployment.start())

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """在环境中执行命令并返回原始输出。"""
        command = action.get("command", "")
        try:
            result = asyncio.run(
                self.deployment.runtime.execute(
                    RexCommand(
                        command=command,
                        shell=True,
                        check=False,
                        cwd=cwd or self.config.cwd,
                        timeout=timeout or self.config.timeout,
                        merge_output_streams=True,
                    )
                )
            )
            output = {"output": result.stdout, "returncode": result.exit_code, "exception_info": ""}
        except Exception as e:
            output = {
                "output": str(e) if str(e) else "",
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

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return recursive_merge(self.config.model_dump(), kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }
