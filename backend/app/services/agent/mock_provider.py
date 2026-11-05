"""本地演示用 MockProvider。

不调用任何外部 API，按固定剧本产生流式 chunk，用于在无 DEEPSEEK_API_KEY
的环境下跑通 SSE 全链路（tool.call → tool.result → message.delta → done）：

1. 历史里没有 tool 消息 → 发起 ``list(path="src")``。
2. 上一条 tool 结果是 "not found" → 退回 ``list(path="")``（列出 workspace 根目录）。
3. 其余情况 → 返回固定中文总结文本并 ``finish_reason="stop"``。
"""

from collections.abc import AsyncIterator

from app.services.agent.provider import (
    ChatCompletionChunk,
    LLMProvider,
    ToolCallChunk,
    UsageDelta,
)

DEFAULT_SUMMARY = "好的，我已经查看了 src 目录。"


class MockProvider(LLMProvider):
    def __init__(self, target_dir: str = "src", summary: str = DEFAULT_SUMMARY) -> None:
        self.target_dir = target_dir
        self.summary = summary

    async def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatCompletionChunk]:
        tool_messages = [message for message in messages if message.get("role") == "tool"]

        if not tool_messages:
            yield ChatCompletionChunk(
                tool_calls=[
                    ToolCallChunk(
                        id="mock-call-1", name="list", arguments={"path": self.target_dir}
                    )
                ],
                finish_reason="tool_calls",
                usage=UsageDelta(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            )
            return

        last_content = str(tool_messages[-1].get("content") or "")
        if len(tool_messages) == 1 and "not found" in last_content:
            yield ChatCompletionChunk(
                tool_calls=[
                    ToolCallChunk(id="mock-call-2", name="list", arguments={"path": ""})
                ],
                finish_reason="tool_calls",
                usage=UsageDelta(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            )
            return

        # 分片输出，便于前端看到逐字流式效果
        piece_size = 6
        for index in range(0, len(self.summary), piece_size):
            yield ChatCompletionChunk(content=self.summary[index : index + piece_size])
        yield ChatCompletionChunk(
            finish_reason="stop",
            usage=UsageDelta(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
