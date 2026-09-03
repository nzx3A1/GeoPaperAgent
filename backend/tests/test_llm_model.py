"""PydanticAI 文本模型封装测试。"""

from pydantic_ai import models
from pydantic_ai.models.test import TestModel

from app.config.model_config import OpenAICompatibleConfig
from app.core.agent.model import LLMClient, create_llm_agent, create_llm_model, create_llm_model_settings

models.ALLOW_MODEL_REQUESTS = False


def test_create_llm_model_uses_openai_compatible_config() -> None:
    config = OpenAICompatibleConfig(
        base_url="http://llm.example.test/v1/",
        api_key="test-key",
        model="test-qwen",
    )

    model = create_llm_model(config)

    assert model.model_name == "test-qwen"
    assert str(model.base_url) == "http://llm.example.test/v1/"


def test_create_llm_model_settings_maps_all_generation_options() -> None:
    config = OpenAICompatibleConfig(
        temperature=0.25,
        max_tokens=2048,
        timeout_secs=45,
        enable_thinking=True,
    )

    settings = create_llm_model_settings(config)

    assert settings == {
        "temperature": 0.25,
        "max_tokens": 2048,
        "timeout": 45,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
    }


def test_create_llm_agent_accepts_structured_output_type() -> None:
    agent = create_llm_agent(output_type=dict[str, str], config=OpenAICompatibleConfig())

    assert agent.output_type == dict[str, str]


async def test_llm_client_runs_through_pydantic_ai_without_live_request() -> None:
    client = LLMClient(OpenAICompatibleConfig())

    with client.agent.override(model=TestModel(custom_output_text="测试成功")):
        result = await client.run("请回复测试成功")

    assert result.output == "测试成功"
    assert result.usage.requests == 1


def test_llm_client_sync_text_helper() -> None:
    client = LLMClient(OpenAICompatibleConfig())

    with client.agent.override(model=TestModel(custom_output_text="同步成功")):
        output = client.generate_sync("请回复同步成功")

    assert output == "同步成功"
