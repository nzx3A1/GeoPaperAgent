"""用于 Anthropic API 兼容性的实用工具。"""


def _is_anthropic_thinking_block(block) -> bool:
    """检查内容块是否为 thinking 类型的块。"""
    if not isinstance(block, dict):
        return False
    return block.get("type") in ("thinking", "redacted_thinking")


def _reorder_anthropic_thinking_blocks(messages: list[dict]) -> list[dict]:
    """重新排序 thinking 块，使其不作为助手消息的最后一个块。

    这是 Anthropic API 的要求：thinking 块必须出现在其他块之前。
    """
    result = []
    for msg in messages:
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            content = msg["content"]
            thinking_blocks = [b for b in content if _is_anthropic_thinking_block(b)]
            if thinking_blocks:
                other_blocks = [b for b in content if not _is_anthropic_thinking_block(b)]
                if other_blocks:
                    msg = {**msg, "content": thinking_blocks + other_blocks}
                else:
                    msg = {**msg, "content": thinking_blocks + [{"type": "text", "text": ""}]}
        result.append(msg)
    return result
