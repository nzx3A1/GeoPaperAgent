import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

import litellm
from pydantic import BaseModel

from minisweagent.exceptions import FormatError
from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.models.utils.actions_toolcall import (
    BASH_TOOL,
    format_toolcall_observation_messages,
    parse_toolcall_actions,
)
from minisweagent.models.utils.anthropic_utils import _reorder_anthropic_thinking_blocks
from minisweagent.models.utils.cache_control import set_cache_control
from minisweagent.models.utils.openai_multimodal import expand_multimodal_content
from minisweagent.models.utils.retry import retry

logger = logging.getLogger("portkey_model")

try:
    from portkey_ai import Portkey
except ImportError:
    raise ImportError(
        "The portkey-ai package is required to use PortkeyModel. Please install it with: pip install portkey-ai"
    )


class PortkeyModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    provider: str = ""
    """要使用的 LLM 提供者（例如 'openai'、'anthropic'、'google'）。
    如果未指定，将从 model_name 自动检测。
    在不使用虚拟密钥时，Portkey 需要此参数。
    """
    litellm_model_registry: Path | str | None = os.getenv("LITELLM_MODEL_REGISTRY_PATH")
    """我们目前使用 litellm 来计算成本。你可以在这里向 litellm 的模型注册表注册额外的模型。
    请注意，如果我们获得对 Portkey 更好的支持并改变成本计算方式，这可能会改变。
    """
    litellm_model_name_override: str = ""
    """我们目前使用 litellm 来计算成本。如果 litellm 模型名称与
    Portkey 模型名称不匹配，你可以在这里覆盖用于 litellm 的模型名称。
    请注意，如果我们获得对 Portkey 更好的支持并改变成本计算方式，这可能会改变。
    """
    set_cache_control: Literal["default_end"] | None = None
    """设置显式的缓存控制标记，例如用于 Anthropic 模型。"""
    cost_tracking: Literal["default", "ignore_errors"] = os.getenv("MSWEA_COST_TRACKING", "default")
    """此模型的成本跟踪模式。可以是 "default" 或 "ignore_errors"（忽略错误/缺失的成本信息）。"""
    format_error_template: str = "{{ error }}"
    """当语言模型输出不符合预期格式时使用的模板。"""
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    """用于在执行动作后渲染观察结果的模板。"""
    multimodal_regex: str = ""
    """用于提取多模态内容的正则表达式。空字符串禁用多模态处理。"""


class PortkeyModel:
    abort_exceptions: list[type[Exception]] = [KeyboardInterrupt, TypeError, ValueError]

    def __init__(self, *, config_class: type = PortkeyModelConfig, **kwargs):
        self.config = config_class(**kwargs)
        if self.config.litellm_model_registry and Path(self.config.litellm_model_registry).is_file():
            litellm.utils.register_model(json.loads(Path(self.config.litellm_model_registry).read_text()))

        self._api_key = os.getenv("PORTKEY_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Portkey API key is required. Set it via the "
                "PORTKEY_API_KEY environment variable. You can permanently set it with "
                "`mini-extra config set PORTKEY_API_KEY YOUR_KEY`."
            )

        virtual_key = os.getenv("PORTKEY_VIRTUAL_KEY")
        client_kwargs = {"api_key": self._api_key}
        if virtual_key:
            client_kwargs["virtual_key"] = virtual_key
        elif self.config.provider:
            # 如果没有虚拟密钥但指定了提供者，则传递它
            client_kwargs["provider"] = self.config.provider

        self.client = Portkey(**client_kwargs)

    def _query(self, messages: list[dict[str, str]], **kwargs):
        return self.client.chat.completions.create(
            model=self.config.model_name,
            messages=messages,
            tools=[BASH_TOOL],
            **(self.config.model_kwargs | kwargs),
        )

    def _prepare_messages_for_api(self, messages: list[dict]) -> list[dict]:
        prepared = [{k: v for k, v in msg.items() if k != "extra"} for msg in messages]
        prepared = _reorder_anthropic_thinking_blocks(prepared)
        return set_cache_control(prepared, mode=self.config.set_cache_control)

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        for attempt in retry(logger=logger, abort_exceptions=self.abort_exceptions):
            with attempt:
                response = self._query(self._prepare_messages_for_api(messages), **kwargs)
        cost_output = self._calculate_cost(response)
        GLOBAL_MODEL_STATS.add(cost_output["cost"])
        try:
            actions = self._parse_actions(response)
        except FormatError as e:
            e.messages[0]["extra"].update(cost_output)
            try:
                e.messages[0]["extra"]["response"] = response.model_dump(mode="json")
            except Exception:
                # model_dump 失败（例如无法序列化的对象）；退回到 repr
                # 以保证规范契约（"必须持久化 response"）始终成立。
                e.messages[0]["extra"]["response"] = repr(response)
            raise
        message = response.choices[0].message.model_dump()
        message["extra"] = {
            "actions": actions,
            "response": response.model_dump(),
            **cost_output,
            "timestamp": time.time(),
        }
        return message

    def _parse_actions(self, response) -> list[dict]:
        """从响应中解析工具调用。如果是未知工具，则抛出 FormatError。"""
        tool_calls = response.choices[0].message.tool_calls or []
        return parse_toolcall_actions(
            tool_calls,
            format_error_template=self.config.format_error_template,
            template_kwargs={"finish_reason": response.choices[0].finish_reason},
        )

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

    def _calculate_cost(self, response) -> dict[str, float]:
        response_for_cost_calc = response.model_copy()
        if self.config.litellm_model_name_override:
            if response_for_cost_calc.model:
                response_for_cost_calc.model = self.config.litellm_model_name_override
        prompt_tokens = response_for_cost_calc.usage.prompt_tokens
        if prompt_tokens is None:
            logger.warning(
                f"Prompt tokens are None for model {self.config.model_name}. Setting to 0. Full response: {response_for_cost_calc.model_dump()}"
            )
            prompt_tokens = 0
        total_tokens = response_for_cost_calc.usage.total_tokens
        completion_tokens = response_for_cost_calc.usage.completion_tokens
        if completion_tokens is None:
            logger.warning(
                f"Completion tokens are None for model {self.config.model_name}. Setting to 0. Full response: {response_for_cost_calc.model_dump()}"
            )
            completion_tokens = 0
        if total_tokens - prompt_tokens - completion_tokens != 0:
            # 这很可能与 portkey 如何处理缓存令牌有关：它不将它们计入 prompt tokens（？）
            logger.warning(
                f"WARNING: Total tokens - prompt tokens - completion tokens != 0: {response_for_cost_calc.model_dump()}."
                " This is probably a portkey bug or incompatibility with litellm cost tracking. "
                "Setting prompt tokens based on total tokens and completion tokens. You might want to double check your costs. "
                f"Full response: {response_for_cost_calc.model_dump()}"
            )
            response_for_cost_calc.usage.prompt_tokens = total_tokens - completion_tokens
        try:
            cost = litellm.cost_calculator.completion_cost(
                response_for_cost_calc, model=self.config.litellm_model_name_override or None
            )
            assert cost >= 0.0, f"Cost is negative: {cost}"
        except Exception as e:
            cost = 0.0
            if self.config.cost_tracking != "ignore_errors":
                msg = (
                    f"Error calculating cost for model {self.config.model_name} based on {response_for_cost_calc.model_dump()}: {e}. "
                    "You can ignore this issue from your config file with cost_tracking: 'ignore_errors' or "
                    "globally with export MSWEA_COST_TRACKING='ignore_errors' to ignore this error. "
                    "Alternatively check the 'Cost tracking' section in the documentation at "
                    "https://klieret.short.gy/mini-local-models. "
                    "Still stuck? Please open a github issue at https://github.com/SWE-agent/mini-swe-agent/issues/new/choose!"
                )
                logger.critical(msg)
                raise RuntimeError(msg) from e
        return {"cost": cost}
