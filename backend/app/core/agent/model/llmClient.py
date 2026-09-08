"""基于 PydanticAI 的文本大模型调用封装。"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from pydantic_ai import Agent, AgentRunResult, ModelMessage, ModelSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from app.config.model_config import OpenAICompatibleConfig, load_model_settings


def _get_config(config: OpenAICompatibleConfig | None) -> OpenAICompatibleConfig:
    """使用显式配置，未提供时读取项目统一的 ``ModelSettings.llm``。"""

    resolved = config or load_model_settings().llm
    if not resolved.base_url.strip():
        raise ValueError("LLM base_url 不能为空")
    if not resolved.model.strip():
        raise ValueError("LLM model 不能为空")
    return resolved


def create_llm_model(config: OpenAICompatibleConfig | None = None) -> OpenAIChatModel:
    """根据项目 LLM 配置创建 OpenAI Chat Completions 兼容模型。"""

    llm = _get_config(config)
    provider = OpenAIProvider(
        base_url=llm.base_url.rstrip("/"),
        # PydanticAI/OpenAI SDK 要求提供非空 key；无鉴权的本地服务可使用占位值。
        api_key=llm.api_key or "not-required",
    )
    profile = OpenAIModelProfile(
        # vLLM/Qwen 兼容接口普遍使用 max_tokens，并可能只接受一个开头的 system 消息。
        openai_chat_supports_max_completion_tokens=False,
        openai_chat_supports_multiple_system_messages=False,
        openai_supports_strict_tool_definition=False,
    )
    return OpenAIChatModel(llm.model, provider=provider, profile=profile)


def create_llm_model_settings(config: OpenAICompatibleConfig | None = None) -> ModelSettings:
    """把项目配置转换为 PydanticAI 每次请求使用的模型参数。"""

    llm = _get_config(config)
    return ModelSettings(
        temperature=llm.temperature,
        max_tokens=llm.max_tokens,
        timeout=llm.timeout_secs,
        # Qwen/vLLM 通过 chat_template_kwargs 控制是否生成思考内容。
        extra_body={"chat_template_kwargs": {"enable_thinking": llm.enable_thinking}},
    )


def create_llm_agent(
    *,
    instructions: str | None = None,
    output_type: Any = str,
    config: OpenAICompatibleConfig | None = None,
    retries: int = 1,
) -> Agent[None, Any]:
    """创建可继续注册工具或指定结构化输出的 PydanticAI Agent。"""

    llm = _get_config(config)
    return Agent(
        model=create_llm_model(llm),
        output_type=output_type,
        instructions=instructions,
        model_settings=create_llm_model_settings(llm),
        retries=retries,
    )


class LLMClient:
    """项目默认文本 LLM 客户端，同时提供异步和同步调用方式。"""

    def __init__(
        self,
        config: OpenAICompatibleConfig | None = None,
        *,
        instructions: str | None = None,
        retries: int = 1,
    ) -> None:
        self.config = _get_config(config)
        self.agent: Agent[None, str] = create_llm_agent(
            config=self.config,
            instructions=instructions,
            output_type=str,
            retries=retries,
        )

    async def run(
        self,
        prompt: str,
        *,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> AgentRunResult[str]:
        """异步调用 LLM，并保留 PydanticAI 的消息、用量等完整结果。"""

        return await self.agent.run(prompt, message_history=message_history)

    def run_sync(
        self,
        prompt: str,
        *,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> AgentRunResult[str]:
        """在非异步调用方中同步调用 LLM。"""

        return self.agent.run_sync(prompt, message_history=message_history)

    async def generate(
        self,
        prompt: str,
        *,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> str:
        """异步调用并仅返回模型生成的文本。"""

        return (await self.run(prompt, message_history=message_history)).output

    def generate_sync(
        self,
        prompt: str,
        *,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> str:
        """同步调用并仅返回模型生成的文本。"""

        return self.run_sync(prompt, message_history=message_history).output


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """返回复用项目默认配置的 LLM 客户。"""

    return LLMClient()


async def ask_llm(prompt: str, *, message_history: Sequence[ModelMessage] | None = None) -> str:
    """使用项目默认 LLM 异步生成文本。"""

    return await get_llm_client().generate(prompt, message_history=message_history)


def ask_llm_sync(prompt: str, *, message_history: Sequence[ModelMessage] | None = None) -> str:
    """使用项目默认 LLM 同步生成文本。"""

    return get_llm_client().generate_sync(prompt, message_history=message_history)

if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        result = await ask_llm("你好，世界！")
        print(result)

    asyncio.run(main())