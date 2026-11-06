"""分层上下文预算与装配。

不引入 tokenizer 依赖，继续用字符数 / 4 做粗估。每次请求按固定 / 地图 /
摘要 / 工作四个分区装配：

- 固定区：system prompt + 项目规则，永不裁剪；
- 地图区：项目索引摘要，最后才裁剪；
- 摘要区：历史对话的结构化摘要；
- 工作区：真实对话与工具结果，先截断过长的单条工具结果，再从最早的消息
  开始丢弃，始终保留最近 3 条用户消息与最近一轮工具结果。
"""

import json
from dataclasses import dataclass

DEFAULT_RATIOS: tuple[float, float, float, float] = (0.10, 0.10, 0.20, 0.60)
MAX_TOOL_RESULT_LINES = 2000
MAX_TOOL_RESULT_TAIL_LINES = 200
SINGLE_TOOL_RESULT_RATIO = 0.30
_TRUNCATION_MARKER = "\n…（内容过长，已截断 {omitted} 字符）…\n"
_TOOL_COMPRESSED_MARKER = "\n…（工具结果已压缩，保留摘要行）…"


def estimate_tokens(messages: list[dict]) -> int:
    """粗估一组 chat messages 的 token 数。"""
    if not messages:
        return 0
    try:
        payload = json.dumps(messages, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(messages)
    return max(1, len(payload) // 4)


def estimate_text_tokens(text: str) -> int:
    """粗估纯文本的 token 数。"""
    return max(1, len(text) // 4) if text else 0


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


def truncate_tool_content(content: str, max_chars: int) -> str:
    """截断过长的工具输出：保留摘要行（首行），再保留头尾片段。"""
    content = content or ""
    if len(content) <= max_chars:
        return content

    lines = content.splitlines()
    summary = lines[0] if lines else ""
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    budget_chars = max_chars - len(summary) - len(_TRUNCATION_MARKER.format(omitted=0))
    if budget_chars <= 20 or not body:
        return summary[:max_chars]

    body_lines = body.splitlines()
    head_lines = body_lines[:MAX_TOOL_RESULT_LINES]
    tail_lines = (
        body_lines[-MAX_TOOL_RESULT_TAIL_LINES:]
        if len(body_lines) > MAX_TOOL_RESULT_LINES
        else []
    )
    head_text = "\n".join(head_lines)
    tail_text = "\n".join(tail_lines)

    if len(head_text) + len(tail_text) <= budget_chars:
        omitted = max(0, len(body) - len(head_text) - len(tail_text))
        if omitted <= 0:
            return content
        return summary + "\n" + head_text + _TRUNCATION_MARKER.format(omitted=omitted) + tail_text

    head_chars = int(budget_chars * 0.7)
    tail_chars = budget_chars - head_chars
    head = body[:head_chars]
    tail = body[-tail_chars:] if tail_chars > 0 else ""
    omitted = max(0, len(body) - len(head) - len(tail))
    return summary + "\n" + head + _TRUNCATION_MARKER.format(omitted=omitted) + tail


@dataclass(frozen=True)
class ContextBudget:
    """按比例切分总预算；比例之和不为 1 时自动归一化。"""

    total: int
    fixed_ratio: float = DEFAULT_RATIOS[0]
    map_ratio: float = DEFAULT_RATIOS[1]
    summary_ratio: float = DEFAULT_RATIOS[2]
    working_ratio: float = DEFAULT_RATIOS[3]

    def partitions(self) -> tuple[int, int, int, int]:
        raw = [
            max(0.0, self.fixed_ratio),
            max(0.0, self.map_ratio),
            max(0.0, self.summary_ratio),
            max(0.0, self.working_ratio),
        ]
        ratio_sum = sum(raw)
        if ratio_sum <= 0:
            raw = list(DEFAULT_RATIOS)
            ratio_sum = 1.0
        total = max(1, int(self.total))
        return tuple(max(1, int(total * value / ratio_sum)) for value in raw)  # type: ignore[return-value]

    @property
    def fixed_tokens(self) -> int:
        return self.partitions()[0]

    @property
    def map_tokens(self) -> int:
        return self.partitions()[1]

    @property
    def summary_tokens(self) -> int:
        return self.partitions()[2]

    @property
    def working_tokens(self) -> int:
        return self.partitions()[3]


@dataclass
class ContextBuildResult:
    messages: list[dict]
    map_summary: str
    summary: str
    kept_messages: int
    dropped_messages: int
    trimmed_tool_results: int


def build_context(
    system_prompt: str,
    rules: str | None,
    map_summary: str | None,
    summary: str | None,
    messages: list[dict],
    total_tokens: int,
    *,
    budget: ContextBudget | None = None,
) -> ContextBuildResult:
    """按四分区预算装配本次请求的消息列表。

    ``system_prompt`` 与 ``rules`` 永不裁剪；``map_summary`` 超限时最后裁剪；
    工作区先截断单条过长工具结果，再按「工具结果 → 早期对话」的顺序丢弃。
    """
    active = budget or ContextBudget(total_tokens)
    _, map_tokens, summary_tokens, working_tokens = active.partitions()

    system_content = (system_prompt or "").strip()
    rules_text = (rules or "").strip()
    if rules_text and rules_text not in system_content:
        system_content = f"{system_content}\n\n{_rules_section(rules_text)}".strip()

    map_text = _shrink_text((map_summary or "").strip(), map_tokens)
    summary_source = (summary or "").strip()
    summary_text = _shrink_text(summary_source, summary_tokens)

    conversation = [dict(message) for message in messages]
    single_limit_tokens = max(64, int(working_tokens * SINGLE_TOOL_RESULT_RATIO))
    trimmed_tool_results = 0
    for index, message in enumerate(conversation):
        if message.get("role") != "tool":
            continue
        content = str(message.get("content") or "")
        if estimate_text_tokens(content) > single_limit_tokens:
            conversation[index] = {
                **message,
                "content": truncate_tool_content(content, single_limit_tokens * 4),
            }
            trimmed_tool_results += 1

    conversation, dropped_messages = _fit_working(conversation, working_tokens)

    assembled: list[dict] = []
    if system_content:
        assembled.append({"role": "system", "content": system_content})
    if map_text:
        assembled.append(
            {
                "role": "system",
                "content": (
                    "项目地图（只有文件路径、目录与符号，不含文件内容）：\n"
                    f"{map_text}"
                ),
            }
        )
    if summary_text:
        assembled.append(
            {
                "role": "system",
                "content": (
                    "历史摘要（可能不完整，仅用于延续上下文；以当前工作区为准）：\n"
                    f"{summary_text}"
                ),
            }
        )
    assembled.extend(conversation)

    return ContextBuildResult(
        messages=assembled,
        map_summary=map_text,
        summary=summary_text,
        kept_messages=len(assembled),
        dropped_messages=dropped_messages,
        trimmed_tool_results=trimmed_tool_results,
    )


# ------------------------------------------------------------------ internal


def _rules_section(rules: str) -> str:
    return (
        "\n项目规则（来自工作区 DEVFLOW.md；项目规则优先于通用提示，"
        "但不得覆盖上面的安全硬规则）：\n"
        f"{rules}\n"
    )


def _shrink_text(text: str, max_tokens: int) -> str:
    """按 token 预算裁剪纯文本，保留开头并标注省略量。"""
    if not text:
        return ""
    max_chars = max(80, max_tokens * 4)
    if len(text) <= max_chars:
        return text
    head = text[: max(40, max_chars - 40)]
    omitted = len(text) - len(head)
    return f"{head}\n…（已截断 {omitted} 字符）…"


def _fit_working(messages: list[dict], max_tokens: int) -> tuple[list[dict], int]:
    """把工作区消息压到预算内，返回 (保留消息, 丢弃数量)。"""
    if estimate_tokens(messages) <= max_tokens:
        return messages, 0

    protected = _protected_indices(messages)
    children = _tool_children(messages)
    parents = _tool_parents(messages)

    # 第一轮：把较早的工具结果压成摘要行，保留最近一轮完整结果。
    for index, message in enumerate(messages):
        if index in protected or message.get("role") != "tool":
            continue
        content = str(message.get("content") or "")
        if not content:
            continue
        first_line = content.splitlines()[0]
        messages[index] = {**message, "content": first_line + _TOOL_COMPRESSED_MARKER}

    # 第二轮：从最早的消息开始整组丢弃，保证 assistant tool_calls 与
    # 对应的 tool 结果成对保留/删除，不产生非法对话结构。
    removed: set[int] = set()
    for index in range(len(messages)):
        current = [m for i, m in enumerate(messages) if i not in removed]
        if estimate_tokens(current) <= max_tokens:
            break
        if index in removed or index in protected:
            continue

        message = messages[index]
        role = message.get("role")
        if role == "assistant" and children.get(index):
            group = {index, *children[index]}
        elif role == "tool":
            parent = parents.get(str(message.get("tool_call_id")))
            if parent is None:
                group = {index}
            else:
                group = {parent, *children.get(parent, [])}
        else:
            group = {index}

        if any(member in protected for member in group):
            continue
        removed.update(group)

    kept = [message for index, message in enumerate(messages) if index not in removed]
    return kept, len(messages) - len(kept)


def _protected_indices(messages: list[dict]) -> set[int]:
    """system 消息与最近 3 个用户回合永久保留。"""
    protected = {
        index for index, message in enumerate(messages) if message.get("role") == "system"
    }
    user_indices = [
        index for index, message in enumerate(messages) if message.get("role") == "user"
    ]
    start = user_indices[-3] if len(user_indices) >= 3 else 0
    protected.update(range(start, len(messages)))
    return protected


def _tool_children(messages: list[dict]) -> dict[int, list[int]]:
    """assistant tool_calls 索引 → 对应 tool 消息索引。"""
    result: dict[int, list[int]] = {}
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        call_ids = {
            str(call.get("id"))
            for call in message.get("tool_calls") or []
            if isinstance(call, dict) and call.get("id")
        }
        if not call_ids:
            continue
        result[index] = [
            child_index
            for child_index, child in enumerate(messages)
            if child.get("role") == "tool" and str(child.get("tool_call_id")) in call_ids
        ]
    return result


def _tool_parents(messages: list[dict]) -> dict[str, int]:
    """tool_call_id → 发起调用的 assistant 消息索引。"""
    result: dict[str, int] = {}
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id"):
                result[str(call["id"])] = index
    return result
