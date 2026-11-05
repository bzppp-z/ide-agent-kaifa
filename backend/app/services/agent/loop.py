"""Agent 主循环。

输入 session + user message，输出 SSE 事件流：
``message.delta`` → ``tool.call`` → ``tool.result`` → ... → ``done``。

``max_steps`` / ``max_tokens`` 是硬限制，循环条件强制 break，不依赖模型自己停止。
``RecentToolCallTracker`` 是软检测：连续 window 次相同工具 + 相同入参时注入提示消息。
"""

import copy
import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.core.events import (
    BaseSSEEvent,
    DoneEvent,
    MessageDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.agent.context import truncate_messages
from app.services.agent.provider import LLMProvider, ToolCallChunk
from app.services.agent.tools import ToolRegistry
from app.services.sandbox import SandboxService

MAX_STEPS_DEFAULT = 25
MAX_TOKENS_DEFAULT = 200000


class RecentToolCallTracker:
    """记录最近 window 次工具调用 hash，全部相同则判定为卡住。"""

    def __init__(self, window: int = 3) -> None:
        if window < 1:
            raise ValueError("window must be positive")
        self.window = window
        self._digests: deque[str] = deque(maxlen=window)

    def record(self, tool_name: str, arguments: dict[str, Any] | str) -> str:
        payload = json.dumps(
            {"tool": tool_name, "arguments": arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._digests.append(digest)
        return digest

    def is_stuck(self) -> bool:
        return len(self._digests) == self.window and len(set(self._digests)) == 1


class AgentLoop:
    SYSTEM_PROMPT = """你是 DevFlow 的 AI coding agent。工作目录是一个隔离的 workspace。
你可以使用以下工具：
{tool_descriptions}

规则：
- 所有路径必须是 workspace 下的相对路径，不要使用绝对路径或 ..
- 一次工具调用只处理一个文件的修改
- 如果需要修改多个文件，依次调用工具
- 使用中文回复用户
"""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        tools: ToolRegistry | None = None,
        sandbox: SandboxService | None = None,
        history: list[dict] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.sandbox = sandbox
        self.history = history or []

    async def run_stream(
        self,
        session_id: str,
        user_message: str,
        provider: LLMProvider | None = None,
        tools: ToolRegistry | None = None,
        sandbox: SandboxService | None = None,
        session_workspace_path: str | None = None,
        max_steps: int = MAX_STEPS_DEFAULT,
        max_tokens: int = MAX_TOKENS_DEFAULT,
    ) -> AsyncIterator[BaseSSEEvent]:
        provider = provider or self.provider
        tools = tools or self.tools
        sandbox = sandbox or self.sandbox

        if provider is None:
            raise ValueError("provider is required")
        if tools is None:
            raise ValueError("tools is required")
        if sandbox is None:
            raise ValueError("sandbox is required")
        if not session_workspace_path:
            raise ValueError("session_workspace_path is required")

        run_id = f"{session_id}:{uuid4().hex[:12]}"
        tracker = RecentToolCallTracker(window=3)
        messages = self._build_messages(user_message, tools)
        steps = 0
        tokens_used = 0

        while steps < max_steps and tokens_used < max_tokens:
            messages = truncate_messages(messages, max_tokens)

            tool_calls: list[ToolCallChunk] = []
            content_parts: list[str] = []

            async for chunk in provider.chat_completion_stream(
                messages, tools=tools.to_schema(), temperature=0.3
            ):
                if chunk.content:
                    content_parts.append(chunk.content)
                    yield MessageDeltaEvent(content=chunk.content)
                if chunk.tool_calls:
                    tool_calls.extend(chunk.tool_calls)
                if chunk.usage is not None and chunk.usage.total_tokens:
                    tokens_used += chunk.usage.total_tokens

            if not tool_calls:
                yield DoneEvent(
                    run_id=run_id,
                    total_tokens=tokens_used,
                    total_steps=steps,
                )
                return

            prepared = [
                (tool_call, tool_call.id or f"call_{uuid4().hex[:12]}", _normalize_arguments(tool_call.arguments))
                for tool_call in tool_calls
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(content_parts) or None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name or "",
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                        for tool_call, call_id, arguments in prepared
                    ],
                }
            )

            for tool_call, call_id, arguments in prepared:
                tool_name = tool_call.name or ""
                yield ToolCallEvent(call_id=call_id, tool_name=tool_name, arguments=arguments)

                result = await tools.call(tool_name, arguments, sandbox, session_workspace_path)
                tracker.record(tool_name, arguments)

                yield ToolResultEvent(
                    call_id=call_id,
                    tool_name=tool_name,
                    ok=result.ok,
                    result=result.model_dump(mode="json", exclude_none=True),
                    duration_ms=result.duration_ms,
                )

                tool_content = result.summary
                if result.details:
                    tool_content += "\n" + result.details
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tool_content,
                    }
                )

                if tracker.is_stuck():
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                f"你连续 {tracker.window} 次对 {tool_name} 使用了相同的入参，"
                                "请换一个策略或先说明你的判断。"
                            ),
                        }
                    )

            steps += 1

        reason = "max_steps" if steps >= max_steps else "max_tokens"
        yield DoneEvent(
            run_id=run_id,
            warning=f"stopped: {reason}",
            total_tokens=tokens_used,
            total_steps=steps,
        )

    # --------------------------------------------------------------- internal

    def _build_messages(self, user_message: str, tools: ToolRegistry) -> list[dict]:
        descriptions = []
        for schema in tools.to_schema():
            function = schema.get("function", {})
            properties = (function.get("parameters") or {}).get("properties") or {}
            parameter_names = ", ".join(properties) if properties else ""
            descriptions.append(
                f"- {function.get('name')}({parameter_names}): {function.get('description', '')}"
            )

        messages: list[dict] = [
            {
                "role": "system",
                "content": self.SYSTEM_PROMPT.format(
                    tool_descriptions="\n".join(descriptions)
                ),
            }
        ]
        messages.extend(copy.deepcopy(self.history))
        messages.append({"role": "user", "content": user_message})
        return messages


def _normalize_arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
