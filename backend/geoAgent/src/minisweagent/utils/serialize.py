from typing import Any

UNSET = object()


def recursive_merge(*dictionaries: dict | None) -> dict:
    """递归地合并多个字典。

    后面的字典优先于前面的字典。
    嵌套字典会被递归合并。
    UNSET 值会被跳过。
    """
    if not dictionaries:
        return {}
    result: dict[str, Any] = {}
    for d in dictionaries:
        if d is None:
            continue
        for key, value in d.items():
            if value is UNSET:
                continue
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = recursive_merge(result[key], value)
            elif isinstance(value, dict):
                # 递归合并字典值以过滤掉嵌套的 UNSET 值
                result[key] = recursive_merge(value)
            else:
                result[key] = value
    return result
