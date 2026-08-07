import asyncio
from typing import Any

from pydantic import BaseModel
from swerex.deployment.modal import ModalDeployment
from swerex.runtime.abstract import Command as RexCommand

from minisweagent.exceptions import Submitted
from minisweagent.utils.serialize import recursive_merge


class SwerexModalEnvironmentConfig(BaseModel):
    image: str
    """用于部署的镜像。可以是：
    - Dockerhub 镜像名（例如 `python:3.11-slim`）
    - ECR 镜像名（例如 `123456789012.dkr.ecr.us-east-1.amazonaws.com/my-image:tag`）
    - Dockerfile 的路径
    """
    cwd: str = "/"
    """在其中执行命令的工作目录。"""
    timeout: int = 30
    """在容器中执行命令的超时时间。"""
    env: dict[str, str] = {}
    """执行命令时要设置的环境变量。"""
    startup_timeout: float = 60.0
    """等待 runtime 启动的时间。"""
    runtime_timeout: float = 3600.0
    """runtime 的超时时间（Modal 沙箱可以保持活动多久）。"""
    deployment_timeout: float = 3600.0
    """部署的超时时间。"""
    install_pipx: bool = True
    """是否在容器内安装 pipx（swe-rex runtime 需要）。"""
    modal_sandbox_kwargs: dict[str, Any] = {}
    """传递给 `modal.Sandbox.create` 的额外参数。"""


class SwerexModalEnvironment:
    def __init__(self, **kwargs):
        """该类使用 SWE-ReX 在 Modal 沙箱中远程执行 bash 命令。

        Modal（https://modal.com）提供可用于运行沙箱环境的无服务器云计算。
        该环境类使用 SWE-ReX 的 ModalDeployment 来创建和管理用于命令执行的 Modal 沙箱。

        适用于以下场景：
        - 使用远程执行大规模训练编码代理
        - 在隔离的云环境中运行评估
        - 在多个实例间并行执行

        有关关键字参数，请参见 `SwerexModalEnvironmentConfig`。
        """
        self.config = SwerexModalEnvironmentConfig(**kwargs)
        self.deployment = ModalDeployment(
            image=self.config.image,
            startup_timeout=self.config.startup_timeout,
            runtime_timeout=self.config.runtime_timeout,
            deployment_timeout=self.config.deployment_timeout,
            install_pipx=self.config.install_pipx,
            modal_sandbox_kwargs=self.config.modal_sandbox_kwargs,
        )
        asyncio.run(self.deployment.start())

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """在环境中执行命令并返回原始输出。"""
        command = action.get("command", "") if isinstance(action, dict) else action
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
                        env=self.config.env if self.config.env else None,
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

    def stop(self):
        async def _stop():
            await asyncio.wait_for(self.deployment.stop(), timeout=10)

        try:
            asyncio.run(_stop())
        except Exception:
            pass
