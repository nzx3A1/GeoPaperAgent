"""用于美观地打印内容字符串的辅助函数。"""

import json


def _format_tool_call(args_str: str) -> str:
    """格式化工具调用参数，如果它是 bash 调用则提取 command。"""
    try:
        args = json.loads(args_str) if isinstance(args_str, str) else args_str
        if isinstance(args, dict) and "command" in args:
            return f"```\n{args['command']}\n```"
    except Exception:
        pass
    return f"```\n{args_str}\n```"


def _format_observation(content: str) -> str | None:
    """尝试将观察 JSON 格式化为键值对。"""
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "returncode" in data:
            lines = []
            for key, value in data.items():
                lines.append(f"<{key}>")
                lines.append(str(value))
            return "\n".join(lines)
        return content
    except Exception:
        return content


def get_content_string(message: dict) -> str:
    """从任何消息格式中提取文本内容以供显示。
    应同时支持 OpenAI 和 Anthropic 消息格式。

    处理：
    - 传统聊天：{"content": "text"}
    - 多模态聊天：{"content": [{"type": "text", "text": "..."}]}
    - Anthropic 工具使用：{"content": [{"type": "tool_use", "input": {...}}]}
    - Anthropic 工具结果：{"content": [{"type": "tool_result", "content": "..."}]}
    - 观察消息：{"content": "{\"returncode\": 0, \"output\": \"...\"}"}
    - 传统工具调用：{"tool_calls": [{"function": {"name": "...", "arguments": "..."}}]}
    - Responses API：{"output": [{"type": "message", "content": [...]}]}
    """
    texts = []

    # 提取 content（字符串或多模态列表）
    content = message.get("content")
    if isinstance(content, str):
        texts.append(_format_observation(content))
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                texts.append(_format_tool_call(json.dumps(item.get("input", {}))))
            elif item.get("type") == "tool_result":
                rc = item.get("content", "")
                if isinstance(rc, str):
                    texts.append(_format_observation(rc))
            elif text := item.get("text"):
                texts.append(text)

    # 处理传统的 tool_calls 格式（OpenAI/LiteLLM 风格）
    if tool_calls := message.get("tool_calls"):
        for tc in tool_calls:
            func = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", None)
            if func:
                args = func.get("arguments", "{}") if isinstance(func, dict) else getattr(func, "arguments", "{}")
                texts.append(_format_tool_call(args))

    # 处理 Responses API 格式（output 数组）
    if output := message.get("output"):
        if isinstance(output, str):
            texts.append(_format_observation(output))
        elif isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if isinstance(c, dict) and (text := c.get("text")):
                            texts.append(text)
                elif item.get("type") == "function_call":
                    texts.append(_format_tool_call(item.get("arguments", "{}")))

    return "\n\n".join(t for t in texts if t)
