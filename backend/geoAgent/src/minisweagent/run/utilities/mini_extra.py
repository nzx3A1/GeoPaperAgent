#!/usr/bin/env python3

"""这是 mini-extra 脚本的中央入口点。使用子命令
调用其他命令行实用工具，如在基准上运行、编辑配置、
检查轨迹等。
"""

import sys
from importlib import import_module

from rich.console import Console

subcommands = [
    ("minisweagent.run.utilities.config", ["config"], "管理全局配置文件"),
    ("minisweagent.run.utilities.inspector", ["inspect", "i", "inspector"], "运行检查器（浏览轨迹）"),
    ("minisweagent.run.benchmarks.swebench", ["swebench"], "在 SWE-bench 上评估（批处理模式）"),
    ("minisweagent.run.benchmarks.swebench_single", ["swebench-single"], "在 SWE-bench 上评估（单个实例）"),
    ("minisweagent.run.benchmarks.programbench", ["programbench"], "在 ProgramBench 上运行（批处理模式）"),
]


def get_docstring() -> str:
    lines = [
        "这是来自 mini-swe-agent 的 [yellow]所有额外命令的中央入口点[/yellow]。",
        "",
        "可用的子命令：",
        "",
    ]
    for _, aliases, description in subcommands:
        alias_text = " 或 ".join(f"[bold green]{alias}[/bold green]" for alias in aliases)
        lines.append(f"  {alias_text}: {description}")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if len(args) == 0 or len(args) == 1 and args[0] in ["-h", "--help"]:
        return Console().print(get_docstring())

    for module_path, aliases, _ in subcommands:
        if args[0] in aliases:
            return import_module(module_path).app(args[1:], prog_name=f"mini-extra {aliases[0]}")

    return Console().print(get_docstring())


if __name__ == "__main__":
    main()
