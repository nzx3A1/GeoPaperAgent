"""缓存控制实用工具主要用于 Anthropic 模型。
它们用于显式设置缓存控制点。
"""

import copy
import warnings
from typing import Literal


def _get_content_text(entry: dict) -> str | None:
    if entry["content"] is None:
        return None
    if isinstance(entry["content"], str):
        return entry["content"]
    assert len(entry["content"]) == 1, "Expected single message in content"
    return entry["content"][0]["text"]


def _clear_cache_control(entry: dict) -> None:
    if isinstance(entry["content"], list):
        assert len(entry["content"]) == 1, "Expected single message in content"
        entry["content"][0].pop("cache_control", None)
    # 注意：对于仅含 tool_use 的助手消息，entry["content"] 可能为 None
    entry.pop("cache_control", None)


def _set_cache_control(entry: dict) -> None:
    # 处理 None content（例如仅含 tool_use 的助手消息）
    if entry["content"] is None:
        entry["cache_control"] = {"type": "ephemeral"}
        return

    if not isinstance(entry["content"], list):
        entry["content"] = [  # type: ignore
            {
                "type": "text",
                "text": _get_content_text(entry),
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        entry["content"][0]["cache_control"] = {"type": "ephemeral"}
    if entry["role"] == "tool":
        # 绕过奇怪的 bug
        entry["content"][0].pop("cache_control", None)
        entry["cache_control"] = {"type": "ephemeral"}


def set_cache_control(
    messages: list[dict], *, mode: Literal["default_end"] | None = "default_end", last_n_messages_offset: int = 0
) -> list[dict]:
    """这个消息处理器向消息添加手动缓存控制标记。"""
    if mode is None:
        return messages
    if mode != "default_end":
        raise ValueError(f"Invalid mode: {mode}")
    if last_n_messages_offset:
        warnings.warn("last_n_messages_offset is deprecated and will be removed in the future. It has no effect.")

    messages = copy.deepcopy(messages)
    new_messages = []
    for i_entry, entry in enumerate(reversed(messages)):
        _clear_cache_control(entry)
        if i_entry == 0:
            _set_cache_control(entry)
        new_messages.append(entry)
    return list(reversed(new_messages))
