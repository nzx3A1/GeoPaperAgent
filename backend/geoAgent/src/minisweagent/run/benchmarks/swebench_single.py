"""在单个 SWE-Bench 实例上运行。"""

from pathlib import Path

import typer
from datasets import load_dataset

from minisweagent import global_config_dir
from minisweagent.agents import get_agent
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.models import get_model
from minisweagent.run.benchmarks.swebench import (
    DATASET_MAPPING,
    get_sb_environment,
)
from minisweagent.utils.log import logger
from minisweagent.utils.serialize import UNSET, recursive_merge

DEFAULT_OUTPUT_FILE = global_config_dir / "last_swebench_single_run.traj.json"
DEFAULT_CONFIG_FILE = builtin_config_dir / "benchmarks" / "swebench.yaml"

app = typer.Typer(rich_markup_mode="rich", add_completion=False)

_CONFIG_SPEC_HELP_TEXT = """配置文件路径、文件名或键值对。

[bold red]重要：[/bold red] [red]如果设置了此选项，将不会使用默认配置文件。[/red]
因此你需要显式设置它，例如使用 [bold green]-c swebench.yaml <other options>[/bold green]

多个配置将被递归合并。

示例：

[bold red]-c model.model_kwargs.temperature=0[/bold red] [red]你忘了添加默认配置文件！请参见上文。[/red]

[bold green]-c swebench.yaml -c model.model_kwargs.temperature=0.5[/bold green]

[bold green]-c swebench.yaml -c agent.mode=yolo[/bold green]
"""


# fmt: off
@app.command()
def main(
    subset: str = typer.Option("lite", "--subset", help="要使用的 SWEBench 子集或数据集路径", rich_help_panel="数据选择"),
    split: str = typer.Option("dev", "--split", help="数据集划分", rich_help_panel="数据选择"),
    instance_spec: str = typer.Option(0, "-i", "--instance", help="SWE-Bench 实例 ID 或索引", rich_help_panel="数据选择"),
    model_name: str | None = typer.Option(None, "-m", "--model", help="要使用的模型", rich_help_panel="基本"),
    model_class: str | None = typer.Option(None, "--model-class", help="要使用的模型类（例如 'anthropic' 或 'minisweagent.models.anthropic.AnthropicModel'）", rich_help_panel="高级"),
    agent_class: str | None = typer.Option(None, "--agent-class", help="要使用的代理类（例如 'interactive' 或 'minisweagent.agents.interactive.InteractiveAgent'）", rich_help_panel="高级"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="要使用的环境类（例如 'docker' 或 'minisweagent.environments.docker.DockerEnvironment'）", rich_help_panel="高级"),
    yolo: bool = typer.Option(False, "-y", "--yolo", help="无确认运行"),
    cost_limit: float | None = typer.Option(None, "-l", "--cost-limit", help="成本限制。设置为 0 以禁用。"),
    config_spec: list[str] = typer.Option([str(DEFAULT_CONFIG_FILE)], "-c", "--config", help=_CONFIG_SPEC_HELP_TEXT, rich_help_panel="基本"),
    exit_immediately: bool = typer.Option(False, "--exit-immediately", help="当代理想要完成时立即退出，而不是进行提示", rich_help_panel="高级"),
    output: Path | None = typer.Option(DEFAULT_OUTPUT_FILE, "-o", "--output", help="输出轨迹文件", rich_help_panel="基本"),
) -> None:
    # fmt: on
    """在单个 SWE-Bench 实例上运行。"""
    dataset_path = DATASET_MAPPING.get(subset, subset)
    logger.info(f"Loading dataset from {dataset_path}, split {split}...")
    instances = {
        inst["instance_id"]: inst  # type: ignore
        for inst in load_dataset(dataset_path, split=split)
    }
    if instance_spec.isnumeric():
        instance_spec = sorted(instances.keys())[int(instance_spec)]
    instance: dict = instances[instance_spec]  # type: ignore

    logger.info(f"Building agent config from specs: {config_spec}")
    configs = [get_config_from_spec(spec) for spec in config_spec]
    configs.append({
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

    env = get_sb_environment(config, instance)
    agent = get_agent(
        get_model(config=config.get("model", {})),
        env,
        config.get("agent", {}),
        default_type="interactive",
    )
    agent.run(instance["problem_statement"])


if __name__ == "__main__":
    app()
