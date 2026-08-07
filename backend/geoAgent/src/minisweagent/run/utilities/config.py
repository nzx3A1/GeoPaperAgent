#!/usr/bin/env python3

"""用于管理全局配置文件的实用工具。

你也可以直接编辑配置目录中的 `.env` 文件。

它位于 [bold green]{global_config_file}[/bold green]。
"""

import os
import subprocess

from dotenv import load_dotenv, set_key, unset_key
from rich.console import Console
from rich.rule import Rule
from typer import Argument, Typer

from minisweagent import global_config_file


def _reload_config():
    load_dotenv(dotenv_path=global_config_file, override=True)


app = Typer(
    help=__doc__.format(global_config_file=global_config_file),  # type: ignore
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)
console = Console(highlight=False)


_SETUP_HELP = """要开始使用，我们需要设置你的全局配置文件。

你可以手动编辑它，或使用 [bold green]mini-extra config set[/bold green] 或 [bold green]mini-extra config edit[/bold green] 命令。

此设置将询问你的模型和 API 密钥。

以下是一些流行的模型及所需的 API 密钥：

[bold green]anthropic/claude-opus-4-6-20260205[/bold green] （[bold green]ANTHROPIC_API_KEY[/bold green]）
[bold green]openai/gpt-5.4[/bold green] 或 [bold green]openai/gpt-5.4-mini[/bold green] （[bold green]OPENAI_API_KEY[/bold green]）
[bold green]gemini/gemini-3-pro-preview[/bold green] （[bold green]GEMINI_API_KEY[/bold green]）

[bold]注意：请始终在模型名称中包含提供者（例如 "openai/"）。[/bold]

[bold yellow]你可以将任何设置留空以跳过它。[/bold yellow]

更多信息请参见 https://mini-swe-agent.com/latest/quickstart/
要找到最佳模型，请查看 https://swebench.com/ 上的排行榜
"""


def prompt(*args, **kwargs):
    # 延迟导入以避免缓慢的模块导入
    from prompt_toolkit.shortcuts.prompt import prompt as _prompt

    return _prompt(*args, **kwargs)


def configure_if_first_time():
    if not os.getenv("MSWEA_CONFIGURED"):
        console.print(Rule())
        setup()
        console.print(Rule())


@app.command()
def setup():
    """设置全局配置文件。"""
    console.print(_SETUP_HELP.format(global_config_file=global_config_file))
    default_model = prompt(
        "Enter your default model (e.g., anthropic/claude-opus-4-6-20260205): ",
        default=os.getenv("MSWEA_MODEL_NAME", ""),
    ).strip()
    if default_model:
        set_key(global_config_file, "MSWEA_MODEL_NAME", default_model)
    console.print(
        "[bold yellow]If you already have your API keys set as environment variables, you can ignore the next question.[/bold yellow]"
    )
    key_name = prompt("Enter your API key name (e.g., ANTHROPIC_API_KEY): ").strip()
    key_value = None
    if key_name:
        key_value = prompt("Enter your API key value (e.g., sk-1234567890): ", default=os.getenv(key_name, "")).strip()
        if key_value:
            set_key(global_config_file, key_name, key_value)
    if not key_value:
        console.print(
            "[bold red]API key setup not completed.[/bold red] Totally fine if you have your keys as environment variables."
        )
    set_key(global_config_file, "MSWEA_CONFIGURED", "true")
    _reload_config()
    console.print(
        "\n[bold yellow]Config finished.[/bold yellow] If you want to revisit it, run [bold green]mini-extra config setup[/bold green]."
    )


@app.command()
def set(
    key: str | None = Argument(None, help="要设置的键"),
    value: str | None = Argument(None, help="要设置的值"),
):
    """在全局配置文件中设置一个键。"""
    if key is None:
        key = prompt("Enter the key to set: ")
    if value is None:
        value = prompt(f"Enter the value for {key}: ")
    set_key(global_config_file, key, value)
    _reload_config()


@app.command()
def unset(key: str | None = Argument(None, help="要取消设置的键")):
    """取消设置全局配置文件中的一个键。"""
    if key is None:
        key = prompt("Enter the key to unset: ")
    unset_key(global_config_file, key)
    _reload_config()


@app.command()
def edit():
    """编辑全局配置文件。"""
    editor = os.getenv("EDITOR", "nano")
    subprocess.run([editor, global_config_file])
    _reload_config()


if __name__ == "__main__":
    app()
