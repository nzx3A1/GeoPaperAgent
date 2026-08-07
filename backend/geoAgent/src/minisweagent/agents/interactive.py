"""对默认代理的一个小扩展，让用户加入循环。

共有三种模式：
- human：用户发出的命令立即执行
- confirm：语言模型发出但不在白名单中的命令需要用户确认
- yolo：语言模型发出的命令无需确认即可立即执行
"""

import re
import sys
from typing import Literal, NoReturn

from rich.console import Console
from rich.rule import Rule

from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.agents.utils.prompt_user import _ensure_prompt_session, _multiline_prompt, _has_real_tty
from minisweagent.exceptions import LimitsExceeded, Submitted, TimeExceeded, UserInterruption
from minisweagent.models.utils.content_string import get_content_string

console = Console(highlight=False, force_terminal=False, legacy_windows=False)


class InteractiveAgentConfig(AgentConfig):
    mode: Literal["human", "confirm", "yolo"] = "confirm"
    """是否确认动作。"""
    whitelist_actions: list[str] = []
    """永远不要确认匹配这些正则表达式的动作。"""
    confirm_exit: bool = True
    """如果代理想要结束，是否向用户请求确认？"""


class InteractiveAgent(DefaultAgent):
    _MODE_COMMANDS_MAPPING = {"/u": "human", "/c": "confirm", "/y": "yolo"}

    def __init__(self, *args, config_class=InteractiveAgentConfig, **kwargs):
        super().__init__(*args, config_class=config_class, **kwargs)
        self.cost_last_confirmed = 0.0

    def _interrupt(self, content: str, *, itype: str = "UserInterruption") -> NoReturn:
        raise UserInterruption({"role": "user", "content": content, "extra": {"interrupt_type": itype}})

    def add_messages(self, *messages: dict) -> list[dict]:
        # 扩展父方法以打印消息
        for msg in messages:
            role, content = msg.get("role") or msg.get("type", "unknown"), get_content_string(msg)
            if role == "assistant":
                console.print(
                    f"\n[red][bold]mini-swe-agent[/bold] (step [bold]{self.n_calls}[/bold], [bold]${self.cost:.2f}[/bold]):[/red]\n",
                    end="",
                    highlight=False,
                )
            else:
                console.print(f"\n[bold green]{role.capitalize()}[/bold green]:\n", end="", highlight=False)
            console.print(content, highlight=False, markup=False)
        return super().add_messages(*messages)

    def query(self) -> dict:
        # 扩展父方法以处理 human 模式
        if self.config.mode == "human":
            match command := self._prompt_and_handle_slash_commands("[bold yellow]>[/bold yellow] "):
                case "/y" | "/c":
                    pass
                case _:
                    msg = {
                        "role": "user",
                        "content": f"User command: \n```bash\n{command}\n```",
                        "extra": {"actions": [{"command": command}]},
                    }
                    self.add_messages(msg)
                    return msg
        try:
            with console.status("Waiting for the LM to respond..."):
                return super().query()
        except TimeExceeded:
            # 挂钟时间限制不能通过提高步数/成本限制来解除
            # （下一次查询会再次检查时钟并再次抛出），
            # 所以继续提示会无限循环。请始终干净地停止。
            raise
        except LimitsExceeded:
            if not _has_real_tty():
                # 没有终端可用于提示新的限制 -- 例如无人值守的
                # `--yolo` 运行，或者任何 stdin 重定向自 /dev/null
                # （CI、沙箱）的运行。干净地停止，使轨迹以
                # LimitsExceeded 退出状态保存，而不是在读取输入时
                # 因 EOFError 而崩溃。
                raise
            console.print(
                f"Limits exceeded. Limits: {self.config.step_limit} steps, ${self.config.cost_limit}.\n"
                f"Current spend: {self.n_calls} steps, ${self.cost:.2f}."
            )
            self.config.step_limit = int(input("New step limit: "))
            self.config.cost_limit = float(input("New cost limit: "))
            return super().query()

    @staticmethod
    def _stdin_is_interactive() -> bool:
        """是否有可用于提示用户的交互式终端。

        对于无人值守的运行（例如 CI 中的 `--yolo`，或 stdin
        重定向自 /dev/null 的沙箱），返回 False；在那些情况下调用
        ``input()`` 会引发 ``EOFError`` 并使运行崩溃。
        """
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except (ValueError, OSError):
            return False

    def step(self) -> list[dict]:
        # 重写 step 方法以处理用户中断
        try:
            console.print(Rule())
            return super().step()
        except KeyboardInterrupt:
            interruption_message = self._prompt_and_handle_slash_commands(
                "\n\n[bold yellow]Interrupted.[/bold yellow] "
                "[green]Type a comment/command[/green] (/h for available commands)"
                "\n[bold yellow]>[/bold yellow] "
            ).strip()
            if not interruption_message or interruption_message in self._MODE_COMMANDS_MAPPING:
                interruption_message = "Temporary interruption caught."
            self._interrupt(f"Interrupted by user: {interruption_message}")

    def execute_actions(self, message: dict) -> list[dict]:
        # 重写以处理用户确认和 confirm_exit，并使用 try/finally 保留部分输出
        actions = message.get("extra", {}).get("actions", [])
        commands = [action["command"] for action in actions]
        outputs = []
        try:
            self._ask_confirmation_or_interrupt(commands)
            for action in actions:
                outputs.append(self.env.execute(action))
        except Submitted as e:
            self._check_for_new_task_or_submit(e)
        finally:
            result = self.add_messages(
                *self.model.format_observation_messages(message, outputs, self.get_template_vars())
            )
        return result

    def _add_observation_messages(self, message: dict, outputs: list[dict]) -> list[dict]:
        return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))

    def _check_for_new_task_or_submit(self, e: Submitted) -> NoReturn:
        """检查用户是否要添加新任务或提交。"""
        if self.config.confirm_exit:
            message = (
                "[bold yellow]Agent wants to finish.[/bold yellow] "
                "[bold green]Type new task[/bold green] or [bold]Enter[/bold] to quit "
                "([bold]/h[/bold] for commands)\n"
                "[bold yellow]>[/bold yellow] "
            )
            user_input = self._prompt_and_handle_slash_commands(message).strip()
            if user_input == "/u":  # 直接继续
                self._interrupt("Switched to human mode.")
            elif user_input in self._MODE_COMMANDS_MAPPING:  # 再次询问
                return self._check_for_new_task_or_submit(e)
            elif user_input:
                self._interrupt(f"The user added a new task: {user_input}", itype="UserNewTask")
        raise e

    def _should_ask_confirmation(self, action: str) -> bool:
        return self.config.mode == "confirm" and not any(re.match(r, action) for r in self.config.whitelist_actions)

    def _ask_confirmation_or_interrupt(self, commands: list[str]) -> None:
        if not any(self._should_ask_confirmation(c) for c in commands):
            return
        prompt = (
            f"[bold yellow]Execute {len(commands)} action(s)?[/] [green][bold]Enter[/] to confirm[/], "
            "[red]type [bold]comment[/] to reject[/], or [blue][bold]/h[/] to show available commands[/]\n"
            "[bold yellow]>[/bold yellow] "
        )
        match user_input := self._prompt_and_handle_slash_commands(prompt).strip():
            case "" | "/y":
                pass  # 已确认，无需操作
            case "/u":  # 跳过执行动作并返回查询
                self._interrupt("Commands not executed. Switching to human mode", itype="UserRejection")
            case _:
                self._interrupt(
                    f"Commands not executed. The user rejected your commands with the following message: {user_input}",
                    itype="UserRejection",
                )

    def _prompt_and_handle_slash_commands(self, prompt: str, *, _multiline: bool = False) -> str:
        """提示用户，处理 /h（之后再次查询）并设置模式。返回用户输入。"""
        console.print(prompt, end="")
        try:
            if _multiline:
                session_input = _multiline_prompt()
            else:
                session_input = _ensure_prompt_session().prompt("")
        except Exception as e:  # noqa: BLE001
            # 没有真正的 TTY 或 prompt_toolkit 在当前终端上无法工作
            # （在 Git Bash 等无 Win32 console 的环境中常见）。抛出
            # UserInterruption 以干净地结束循环。
            self._interrupt(f"No interactive terminal available: {e}", itype="UserInterruption")
            return ""  # 不会执行到
        user_input = session_input
        if user_input == "/m":
            return self._prompt_and_handle_slash_commands(prompt, _multiline=True)
        if user_input == "/h":
            console.print(
                f"Current mode: [bold green]{self.config.mode}[/bold green]\n"
                f"[bold green]/y[/bold green] to switch to [bold yellow]yolo[/bold yellow] mode (execute LM commands without confirmation)\n"
                f"[bold green]/c[/bold green] to switch to [bold yellow]confirmation[/bold yellow] mode (ask for confirmation before executing LM commands)\n"
                f"[bold green]/u[/bold green] to switch to [bold yellow]human[/bold yellow] mode (execute commands issued by the user)\n"
                f"[bold green]/m[/bold green] to enter multiline comment",
            )
            return self._prompt_and_handle_slash_commands(prompt)
        if user_input in self._MODE_COMMANDS_MAPPING:
            if self.config.mode == self._MODE_COMMANDS_MAPPING[user_input]:
                return self._prompt_and_handle_slash_commands(
                    f"[bold red]Already in {self.config.mode} mode.[/bold red]\n{prompt}"
                )
            self.config.mode = self._MODE_COMMANDS_MAPPING[user_input]
            console.print(f"Switched to [bold green]{self.config.mode}[/bold green] mode.")
            return user_input
        return user_input
