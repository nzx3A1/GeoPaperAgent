"""不使用工具调用解析动作并格式化观察结果。
这是 mini-swe-agent v1.0 和原始 SWE-agent 使用的方法。
从 mini-swe-agent v2.0 开始，我们强烈建议改用工具调用。
"""

import re
import time

from jinja2 import StrictUndefined, Template

from minisweagent.exceptions import FormatError
from minisweagent.models.utils.openai_multimodal import expand_multimodal_content


def parse_regex_actions(
    content: str, *, action_regex: str, format_error_template: str, template_kwargs: dict | None = None
) -> list[dict]:
    """使用正则表达式从文本内容中解析动作。如果不是恰好一个动作，则抛出 FormatError。

    ``template_kwargs`` 是暴露给 ``format_error_template`` 的额外变量（例如
    ``{"finish_reason": ...}``，以便模板可以报告 ``max_tokens`` 截断 --
    这在此处表现为零个已解析动作 -- 而不是一般的格式错误）。
    """
    actions = [a.strip() for a in re.findall(action_regex, content, re.DOTALL)]
    if len(actions) != 1:
        error_msg = f"Expected exactly 1 action, found {len(actions)}."
        raise FormatError(
            {
                "role": "user",
                "content": Template(format_error_template, undefined=StrictUndefined).render(
                    actions=actions, error=error_msg, **(template_kwargs or {})
                ),
                "extra": {
                    "interrupt_type": "FormatError",
                    "n_actions": len(actions),
                    "model_response": content,
                },
            }
        )
    return [{"command": action} for action in actions]


def format_observation_messages(
    outputs: list[dict],
    *,
    observation_template: str,
    template_vars: dict | None = None,
    multimodal_regex: str = "",
) -> list[dict]:
    """将执行输出格式化为用户观察消息。"""
    results = []
    for output in outputs:
        content = Template(observation_template, undefined=StrictUndefined).render(
            output=output, **(template_vars or {})
        )
        msg: dict = {
            "role": "user",
            "content": content,
            "extra": {
                "raw_output": output.get("output", ""),
                "returncode": output.get("returncode"),
                "timestamp": time.time(),
                "exception_info": output.get("exception_info"),
                **output.get("extra", {}),
            },
        }
        if multimodal_regex:
            msg = expand_multimodal_content(msg, pattern=multimodal_regex)
        results.append(msg)
    return results
