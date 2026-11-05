"""上下文 token 粗估与历史消息截断（MVP 简化版）。

不引入 tokenizer 依赖，用字符数 / 4 做粗估。超限时只截断早期的
``tool`` 消息，始终保留 system prompt 与最近 N 轮消息。
"""

import json


def estimate_tokens(messages: list[dict]) -> int:
    """粗估一组 chat messages 的 token 数。"""
    if not messages:
        return 0
    try:
        payload = json.dumps(messages, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(messages)
    return max(1, len(payload) // 4)


def truncate_messages(messages: list[dict], max_tokens: int, keep_recent: int = 6) -> list[dict]:
    """超过 max_tokens 时丢弃早期的 tool 消息，保留 system 与最近 keep_recent 条消息。"""
    if estimate_tokens(messages) <= max_tokens:
        return list(messages)

    cutoff = max(0, len(messages) - keep_recent)
    kept: list[dict] = []
    for index, message in enumerate(messages):
        if index >= cutoff or message.get("role") == "system":
            kept.append(message)
        elif message.get("role") == "tool":
            continue
        else:
            kept.append(message)
    return kept
