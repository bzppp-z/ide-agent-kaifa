"""Agent 主循环。

输入 session + user message，输出 SSE 事件流：
``message.delta`` → ``tool.call`` → ``tool.result`` → ... → ``done``。

``max_steps`` / ``max_tokens`` 是硬限制，循环条件强制 break，不依赖模型自己停止。
``RecentToolCallTracker`` 是软检测：连续 window 次相同工具 + 相同入参时注入提示消息。
"""

import copy
import difflib
import hashlib
import json
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.core.events import (
    BaseSSEEvent,
    DoneEvent,
    MessageDeltaEvent,
    PatchProposedEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.agent.context import truncate_messages
from app.services.agent.pending import PendingPatchRegistry
from app.services.agent.provider import LLMProvider, ToolCallChunk
from app.services.agent.tools import ToolRegistry, ToolResult
from app.services.sandbox import SandboxService

MAX_STEPS_DEFAULT = 25
MAX_TOKENS_DEFAULT = 200000
PATCH_APPROVAL_TIMEOUT_SECONDS = 120.0

REJECTED_PATCH_SUMMARY = "user rejected the patch"


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
        pending_patches: PendingPatchRegistry | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.sandbox = sandbox
        self.history = history or []
        self.pending_patches = pending_patches

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
        run_id: str | None = None,
        patch_approval_timeout: float = PATCH_APPROVAL_TIMEOUT_SECONDS,
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

        run_id = run_id or f"{session_id}:{uuid4().hex[:12]}"
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
                started_at = time.perf_counter()

                if tool_name == "write_patch" and self.pending_patches is not None:
                    preview = await tools.call(
                        "write_patch",
                        arguments,
                        sandbox,
                        session_workspace_path,
                        dry_run=True,
                    )
                    if preview.ok and preview.extra:
                        patch_id, proposed = self._prepare_patch(
                            session_id=session_id,
                            arguments=arguments,
                            preview=preview,
                        )
                        yield proposed
                        try:
                            accepted = await self.pending_patches.wait(
                                patch_id, timeout=patch_approval_timeout
                            )
                        finally:
                            self.pending_patches.discard(patch_id)

                        if accepted:
                            result = await tools.call(
                                "write_patch", arguments, sandbox, session_workspace_path
                            )
                        else:
                            preview.duration_ms = int(
                                (time.perf_counter() - started_at) * 1000
                            )
                            result = ToolResult(
                                ok=False,
                                summary=REJECTED_PATCH_SUMMARY,
                                details="用户拒绝了该修改，请重新规划。",
                                extra={
                                    "patch_id": patch_id,
                                    "rejected": True,
                                    "duration_ms": preview.duration_ms,
                                },
                            )
                    else:
                        result = preview
                else:
                    result = await tools.call(
                        tool_name, arguments, sandbox, session_workspace_path
                    )

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

    def _prepare_patch(
        self,
        *,
        session_id: str,
        arguments: dict[str, Any],
        preview: ToolResult,
    ) -> tuple[str, PatchProposedEvent]:
        """把 dry_run 结果转换成待审批条目并注册，返回 (patch_id, 事件)。

        调用方必须先 yield 事件再 await registry.wait，否则前端永远看不到审批卡片。
        """
        assert self.pending_patches is not None
        patch_id = str(uuid4())
        path = str(preview.extra.get("path") or arguments.get("path") or "")
        before = str(preview.extra.get("before") or "")
        after = str(preview.extra.get("after") or "")
        event = PatchProposedEvent(
            patch_id=patch_id,
            path=path,
            search=str(arguments.get("search") or ""),
            replace=str(arguments.get("replace") or ""),
            before=before,
            after=after,
            diff=_render_patch_diff(path, before, after),
        )
        self.pending_patches.register(
            patch_id,
            session_id=session_id,
            path=path,
            search=event.search,
            replace=event.replace,
            before=before,
            after=after,
            diff=event.diff,
        )
        return patch_id, event

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


def _render_patch_diff(path: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(lines)


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
