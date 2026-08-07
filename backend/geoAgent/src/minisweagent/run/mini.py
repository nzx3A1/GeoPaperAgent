#!/usr/bin/env python3
# coding=utf-8

"""在本地环境中运行 mini-SWE-agent。这是默认可执行文件 `mini`。"""
# 请先阅读：https://mini-swe-agent.com/latest/usage/mini/  （用法）

import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from minisweagent import global_config_dir
from minisweagent.agents import get_agent
from minisweagent.agents.utils.prompt_user import _multiline_prompt
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.utilities.config import configure_if_first_time
from minisweagent.utils.serialize import UNSET, recursive_merge

DEFAULT_CONFIG_FILE = Path(os.getenv("MSWEA_MINI_CONFIG_PATH", builtin_config_dir / "mini.yaml"))
DEFAULT_OUTPUT_FILE = global_config_dir / "last_mini_run.traj.json"


_HELP_TEXT = """在本地环境中运行 mini-SWE-agent。

[not dim]
有关用法的更多信息：[bold green]https://mini-swe-agent.com/latest/usage/mini/[/bold green]
[/not dim]
"""

_CONFIG_SPEC_HELP_TEXT = """配置文件路径、文件名或键值对。

[bold red]重要：[/bold red] [red]如果设置了此选项，将不会使用默认配置文件。[/red]
因此你需要显式设置它，例如使用 [bold green]-c mini.yaml <other options>[/bold green]

多个配置将被递归合并。

示例：

[bold red]-c model.model_kwargs.temperature=0[/bold red] [red]你忘了添加默认配置文件！请参见上文。[/red]

[bold green]-c mini.yaml -c model.model_kwargs.temperature=0.5[/bold green]

[bold green]-c swebench.yaml agent.mode=yolo[/bold green]
"""

console = Console(highlight=False, force_terminal=False, legacy_windows=False)
app = typer.Typer(rich_markup_mode="rich")


# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    model_name: str | None = typer.Option(None, "-m", "--model", help="要使用的模型",),
    model_class: str | None = typer.Option(None, "--model-class", help="要使用的模型类（例如 'litellm' 或 'minisweagent.models.litellm_model.LitellmModel'）", rich_help_panel="高级"),
    agent_class: str | None = typer.Option(None, "--agent-class", help="要使用的代理类（例如 'interactive' 或 'minisweagent.agents.interactive.InteractiveAgent'）", rich_help_panel="高级"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="要使用的环境类（例如 'local' 或 'minisweagent.environments.local.LocalEnvironment'）", rich_help_panel="高级"),
    task: str | None = typer.Option(None, "-t", "--task", help="任务/问题陈述", show_default=False),
    yolo: bool = typer.Option(False, "-y", "--yolo", help="无确认运行"),
    cost_limit: float | None = typer.Option(None, "-l", "--cost-limit", help="成本限制。设置为 0 以禁用。"),
    config_spec: list[str] = typer.Option([str(DEFAULT_CONFIG_FILE)], "-c", "--config", help=_CONFIG_SPEC_HELP_TEXT),
    output: Path | None = typer.Option(DEFAULT_OUTPUT_FILE, "-o", "--output", help="输出轨迹文件"),
    exit_immediately: bool = typer.Option(False, "--exit-immediately", help="当代理想要完成时立即退出，而不是进行提示", rich_help_panel="高级"),
) -> Any:
    # fmt: on
    configure_if_first_time()

    # 从命令行参数构建配置
    console.print(f"Building agent config from specs: [bold green]{config_spec}[/bold green]")
    configs = [get_config_from_spec(spec) for spec in config_spec]
    configs.append({
        "run": {
            "task": task or UNSET,
        },
        "agent": {
            "agent_class": agent_class or UNSET,
            "mode": "yolo" if yolo else UNSET,
            "cost_limit": cost_limit if cost_limit is not None else UNSET,
            "confirm_exit": False if exit_immediately else UNSET,
            "output_path": output or UNSET,
        },
        "model": {
            "model_class": model_class or UNSET,
            "model_name": model_name or UNSET,
        },
        "environment": {
            "environment_class": environment_class or UNSET,
        },
    })
    config = recursive_merge(*configs)

    if (run_task := config.get("run", {}).get("task", UNSET)) is UNSET:
        console.print("[bold yellow]What do you want to do?")
        run_task = _multiline_prompt()
        console.print("[bold green]Got that, thanks![/bold green]")

    model = get_model(config=config.get("model", {}))
    env = get_environment(config.get("environment", {}), default_type="local")
    agent = get_agent(model, env, config.get("agent", {}), default_type="interactive")
    agent.run(run_task)
    if (output_path := config.get("agent", {}).get("output_path")):
        console.print(f"Saved trajectory to [bold green]'{output_path}'[/bold green]")
    return agent


if __name__ == "__main__":
    app()
