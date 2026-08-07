import logging
import time
from typing import Any

from pydantic import BaseModel

from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.models.utils.actions_text import format_observation_messages
from minisweagent.models.utils.actions_toolcall import format_toolcall_observation_messages
from minisweagent.models.utils.actions_toolcall_response import (
    format_toolcall_observation_messages as format_response_api_observation_messages,
)
from minisweagent.models.utils.openai_multimodal import expand_multimodal_content


def make_output(content: str, actions: list[dict], cost: float = 1.0) -> dict:
    """用于为 DeterministicModel 创建输出字典的辅助函数。

    参数：
        content：响应内容字符串
        actions：动作字典列表，例如 [{"command": "echo hello"}]
        cost：此输出要报告的成本（默认 1.0）
    """
    return {
        "role": "assistant",
        "content": content,
        "extra": {"actions": actions, "cost": cost, "timestamp": time.time()},
    }


def make_toolcall_output(content: str | None, tool_calls: list[dict], actions: list[dict]) -> dict:
    """用于为 DeterministicToolcallModel 创建工具调用输出字典的辅助函数。

    参数：
        content：可选的文本内容（对于仅含工具调用的响应可以为 None）
        tool_calls：OpenAI 格式的工具调用字典列表
        actions：解析后的动作字典列表，例如 [{"command": "echo hello", "tool_call_id": "call_123"}]
    """
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
        "extra": {"actions": actions, "cost": 1.0, "timestamp": time.time()},
    }


def make_response_api_output(content: str | None, actions: list[dict]) -> dict:
    """用于为 DeterministicResponseAPIToolcallModel 创建输出字典的辅助函数。

    参数：
        content：可选的文本内容（对于仅含工具调用的响应可以为 None）
        actions：包含 'command' 和 'tool_call_id' 键的动作字典列表
    """
    output_items = []
    if content:
        output_items.append(
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": content}]}
        )
    for action in actions:
        output_items.append(
            {
                "type": "function_call",
                "call_id": action["tool_call_id"],
                "name": "bash",
                "arguments": f'{{"command": "{action["command"]}"}}',
            }
        )
    return {
        "object": "response",
        "output": output_items,
        "extra": {"actions": actions, "cost": 1.0, "timestamp": time.time()},
    }


def _process_test_actions(actions: list[dict]) -> bool:
    """处理特殊的测试动作。如果查询应该重试，则返回 True。"""
    for action in actions:
        if "raise" in action:
            raise action["raise"]
        cmd = action.get("command", "")
        if cmd.startswith("/sleep "):
            time.sleep(float(cmd.split("/sleep ")[1]))
            return True
        if cmd.startswith("/warning"):
            logging.warning(cmd.split("/warning")[1])
            return True
    return False


class DeterministicModelConfig(BaseModel):
    outputs: list[dict]
    """按顺序返回的精确输出消息列表。每个字典应包含 'role'、'content' 和 'extra'（包含 'actions'）。"""
    model_name: str = "deterministic"
    cost_per_call: float = 1.0
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    """用于在执行动作后渲染观察结果的模板。"""
    multimodal_regex: str = ""
    """用于提取多模态内容的正则表达式。空字符串禁用多模态处理。"""


class DeterministicModel:
    def __init__(self, **kwargs):
        """使用按顺序返回的输出消息列表进行初始化。"""
        self.config = DeterministicModelConfig(**kwargs)
        self.current_index = -1

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        self.current_index += 1
        output = self.config.outputs[self.current_index]
        if _process_test_actions(output.get("extra", {}).get("actions", [])):
            return self.query(messages, **kwargs)
        GLOBAL_MODEL_STATS.add(self.config.cost_per_call)
        return output

    def format_message(self, **kwargs) -> dict:
        return expand_multimodal_content(kwargs, pattern=self.config.multimodal_regex)

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """将执行输出格式化为观察消息。"""
        return format_observation_messages(
            outputs,
            observation_template=self.config.observation_template,
            template_vars=template_vars,
            multimodal_regex=self.config.multimodal_regex,
        )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.config.model_dump()

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }


class DeterministicToolcallModelConfig(BaseModel):
    outputs: list[dict]
    """按顺序返回的精确工具调用输出消息列表。"""
    model_name: str = "deterministic_toolcall"
    cost_per_call: float = 1.0
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    """用于在执行动作后渲染观察结果的模板。"""
    multimodal_regex: str = ""
    """用于提取多模态内容的正则表达式。空字符串禁用多模态处理。"""


class DeterministicToolcallModel:
    def __init__(self, **kwargs):
        """使用按顺序返回的工具调用输出消息列表进行初始化。"""
        self.config = DeterministicToolcallModelConfig(**kwargs)
        self.current_index = -1

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        self.current_index += 1
        output = self.config.outputs[self.current_index]
        if _process_test_actions(output.get("extra", {}).get("actions", [])):
            return self.query(messages, **kwargs)
        GLOBAL_MODEL_STATS.add(self.config.cost_per_call)
        return output

    def format_message(self, **kwargs) -> dict:
        return expand_multimodal_content(kwargs, pattern=self.config.multimodal_regex)

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """将执行输出格式化为工具结果消息。"""
        actions = message.get("extra", {}).get("actions", [])
        return format_toolcall_observation_messages(
            actions=actions,
            outputs=outputs,
            observation_template=self.config.observation_template,
            template_vars=template_vars,
            multimodal_regex=self.config.multimodal_regex,
        )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.config.model_dump()

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }


class DeterministicResponseAPIToolcallModelConfig(BaseModel):
    outputs: list[dict]
    """按顺序返回的精确 Response API 输出消息列表。"""
    model_name: str = "deterministic_response_api_toolcall"
    cost_per_call: float = 1.0
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    """用于在执行动作后渲染观察结果的模板。"""
    multimodal_regex: str = ""
    """用于提取多模态内容的正则表达式。空字符串禁用多模态处理。"""


class DeterministicResponseAPIToolcallModel:
    """使用 OpenAI Responses API 格式的确定性测试模型。"""

    def __init__(self, **kwargs):
        """使用按顺序返回的 Response API 输出消息列表进行初始化。"""
        self.config = DeterministicResponseAPIToolcallModelConfig(**kwargs)
        self.current_index = -1

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        self.current_index += 1
        output = self.config.outputs[self.current_index]
        if _process_test_actions(output.get("extra", {}).get("actions", [])):
            return self.query(messages, **kwargs)
        GLOBAL_MODEL_STATS.add(self.config.cost_per_call)
        return output

    def format_message(self, **kwargs) -> dict:
        """以 Responses API 格式格式化消息。"""
        role = kwargs.get("role", "user")
        content = kwargs.get("content", "")
        extra = kwargs.get("extra")
        content_items = [{"type": "input_text", "text": content}] if isinstance(content, str) else content
        msg: dict = {"type": "message", "role": role, "content": content_items}
        if extra:
            msg["extra"] = extra
        return msg

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """将执行输出格式化为 function_call_output 消息。"""
        actions = message.get("extra", {}).get("actions", [])
        return format_response_api_observation_messages(
            actions=actions,
            outputs=outputs,
            observation_template=self.config.observation_template,
            template_vars=template_vars,
            multimodal_regex=self.config.multimodal_regex,
        )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.config.model_dump()

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }
