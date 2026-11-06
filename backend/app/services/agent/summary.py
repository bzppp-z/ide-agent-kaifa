"""历史对话的结构化摘要。

摘要请求与正常对话使用同一个 provider，但不带工具定义；整体流式消费包在
``asyncio.wait_for`` 里，超时、异常或空内容都返回 ``None``，由调用方降级为
截断早期消息，绝不把异常抛给主循环。
"""

import asyncio

from app.services.agent.plan import PlanState
from app.services.agent.provider import LLMProvider
from app.services.workspace import redact_secrets

SUMMARY_TIMEOUT_SECONDS = 20.0
SUMMARY_TEMPERATURE = 0.2
MAX_MESSAGE_CHARS = 4000
MAX_INPUT_CHARS = 24000
REQUIRED_SECTION = "未完成事项"
MISSING_SECTION_TEXT = "未提取到"

SUMMARY_SYSTEM_PROMPT = """你是 DevFlow 的上下文压缩器。请把下面的 agent 对话压缩成结构化摘要，供后续对话继续使用。
必须严格输出以下六段（使用 Markdown 二级标题，顺序不变，不得省略任何一段）：
## 目标
## 已改文件
## 关键决策
## 失败与尝试
## 未完成事项
## 最近验证

要求：
- 保留所有文件路径（相对路径）与未完成事项，不要编造
- 不要包含 API key、密钥文件内容或大段代码
- 只输出摘要本身，不要额外解释"""


async def summarize_history(
    provider: LLMProvider | None,
    messages: list[dict],
    previous_summary: str | None = None,
    plan: PlanState | None = None,
) -> str | None:
    """生成六段式摘要；失败返回 ``None``，不抛异常。"""
    if provider is None or not messages:
        return None

    prompt = _render_input(messages, previous_summary, plan)
    if not prompt.strip():
        return None

    text: str | None = None
    for attempt in range(2):
        text = await _request_summary(provider, prompt)
        if text is None:
            return None
        if REQUIRED_SECTION in text:
            return redact_secrets(text).strip()
        if attempt == 0:
            # 缺少「未完成事项」段，重试一次。
            continue

    final = (text or "").strip()
    if not final:
        return None
    return redact_secrets(
        f"{final}\n\n## {REQUIRED_SECTION}\n{MISSING_SECTION_TEXT}"
    ).strip()


# ------------------------------------------------------------------ internal


async def _request_summary(provider: LLMProvider, prompt: str) -> str | None:
    async def consume() -> str:
        parts: list[str] = []
        async for chunk in provider.chat_completion_stream(
            [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            tools=None,
            temperature=SUMMARY_TEMPERATURE,
        ):
            if chunk.content:
                parts.append(chunk.content)
        return "".join(parts).strip()

    try:
        text = await asyncio.wait_for(consume(), timeout=SUMMARY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return None
    except asyncio.CancelledError:
        raise
    except Exception:
        # provider 异常、连接失败等一律视为摘要失败，交给调用方降级。
        return None
    return text or None


def _render_input(
    messages: list[dict],
    previous_summary: str | None,
    plan: PlanState | None,
) -> str:
    chunks: list[str] = []
    if previous_summary and previous_summary.strip():
        chunks.append("## 已有摘要\n" + previous_summary.strip())
    if plan is not None:
        rendered = plan.render()
        if rendered:
            chunks.append("## 当前计划\n" + rendered)

    # 从最新消息往前取，保证有限的摘要输入优先覆盖最近上下文。
    lines: list[str] = []
    total = 0
    for message in reversed(messages):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        tool_calls = message.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
            names = [
                str((call.get("function") or {}).get("name") or "")
                for call in tool_calls
                if isinstance(call, dict)
            ]
            names = [name for name in names if name]
            if names:
                content = f"[调用工具：{', '.join(names)}] {content}".strip()
        if not content:
            continue
        line = f"{role}: {content[:MAX_MESSAGE_CHARS]}"
        if total + len(line) > MAX_INPUT_CHARS:
            break
        lines.append(line)
        total += len(line)

    chunks.append("## 对话记录")
    chunks.extend(reversed(lines))
    return "\n\n".join(chunks)
