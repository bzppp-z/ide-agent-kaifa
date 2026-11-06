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
    PlanUpdateEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.agent.context import truncate_messages
from app.services.agent.mock_provider import MockProvider
from app.services.agent.pending import PendingPatchRegistry
from app.services.agent.plan import PlanState, apply_update, plan_from_json
from app.services.agent.provider import LLMProvider, ToolCallChunk
from app.services.agent.tools import ToolRegistry, ToolResult
from app.services.permissions import normalize_permission_mode
from app.services.repo_index import RepoIndex
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


def describe_permission(mode: str | None) -> str:
    """把权限模式写成模型可执行的行为约束。"""
    normalized = normalize_permission_mode(mode)
    if normalized == "readonly":
        return "只读（禁止新建、写入、删除文件和执行命令；只能读取、检索、规划）"
    if normalized == "auto":
        return "自动接受（编辑类操作可直接生效；删除文件与执行命令仍需用户确认）"
    return "需审批（每次写入、删除和命令执行都需要用户确认）"


def describe_model(provider: LLMProvider | None) -> str:
    """返回当前底层模型的展示名，用于注入系统提示词。

    模型本身无法自省自己是谁，只有把 provider 的 model 字段写进提示词，
    它才能准确回答「你是什么模型」。
    """
    if provider is None:
        return ""
    model = getattr(provider, "model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    name = type(provider).__name__
    if isinstance(provider, MockProvider):
        return f"{name}（本地模拟，非真实模型）"
    return name


class AgentLoop:
    SYSTEM_PROMPT = """你是 DevFlow 的 AI coding agent。工作目录是一个隔离的 workspace。
当前底层模型：{model_name}
当前权限模式：{permission_desc}
你可以使用以下工具：
{tool_descriptions}

项目地图（只有文件路径、目录与符号，不含文件内容）：
{project_map}

规划规则：
- 当任务涉及 2 个及以上文件、新建文件、重构、修复缺陷、实现功能或数据迁移时，必须先调用 update_plan 给出 3–8 项计划，再开始执行
- 计划项完成一项就立即调用 update_plan 把它标记为 done
- 某项失败时标记为 failed，并在 note 写明原因
- 同一时刻最多只能有 1 项处于 in_progress
- 每次 update_plan 都提交完整计划快照，并保留已有计划项的 id

规则：
- 所有路径必须是 workspace 下的相对路径，不要使用绝对路径或 ..
- 严格遵守当前权限模式：只读模式下不要尝试写入或执行命令，也不要反复重试被拒绝的调用
- 检索代码优先使用 search_text / find_symbols / search_files；search 是旧版文本检索别名
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
        permission_mode: str = "approval",
        repo_index: RepoIndex | None = None,
        initial_plan: PlanState | dict[str, Any] | None = None,
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
        plan_state = plan_from_json(initial_plan)
        messages = self._build_messages(
            user_message,
            tools,
            describe_model(provider),
            permission_mode,
            repo_index=repo_index,
        )
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
                plan_event: PlanUpdateEvent | None = None

                if tool_name == "update_plan":
                    result, plan_state, plan_event = self._handle_plan_update(
                        arguments, plan_state
                    )
                    result.duration_ms = int((time.perf_counter() - started_at) * 1000)
                elif tool_name == "write_patch" and self.pending_patches is not None:
                    preview = await tools.call(
                        "write_patch",
                        arguments,
                        sandbox,
                        session_workspace_path,
                        dry_run=True,
                        permission_mode=permission_mode,
                        repo_index=repo_index,
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
                                "write_patch",
                                arguments,
                                sandbox,
                                session_workspace_path,
                                permission_mode=permission_mode,
                                repo_index=repo_index,
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
                        tool_name,
                        arguments,
                        sandbox,
                        session_workspace_path,
                        permission_mode=permission_mode,
                        repo_index=repo_index,
                    )

                tracker.record(tool_name, arguments)

                if plan_event is not None:
                    yield plan_event

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

    def _handle_plan_update(
        self, arguments: dict[str, Any], current: PlanState | None
    ) -> tuple[ToolResult, PlanState | None, PlanUpdateEvent | None]:
        """校验整份计划快照；成功时返回新状态与 plan.update 事件。"""
        try:
            state = apply_update(current, arguments.get("items"))
        except ValueError as exc:
            return (
                ToolResult(
                    ok=False,
                    summary=f"计划未更新：{exc}",
                    details="请修正后重新调用 update_plan（items 必须是 3–8 项的完整快照）。",
                ),
                current,
                None,
            )

        result = ToolResult(
            ok=True,
            summary=f"计划已更新：{state.done_count}/{len(state.items)} 完成",
            details=state.render(),
            extra={"plan": state.model_dump(mode="json")},
        )
        return result, state, PlanUpdateEvent(plan=state.model_dump(mode="json"))

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

    def _build_messages(
        self,
        user_message: str,
        tools: ToolRegistry,
        model_name: str = "",
        permission_mode: str = "approval",
        repo_index: RepoIndex | None = None,
    ) -> list[dict]:
        descriptions = []
        for schema in tools.to_schema():
            function = schema.get("function", {})
            properties = (function.get("parameters") or {}).get("properties") or {}
            parameter_names = ", ".join(properties) if properties else ""
            descriptions.append(
                f"- {function.get('name')}({parameter_names}): {function.get('description', '')}"
            )

        project_map = (
            repo_index.summary()
            if repo_index is not None
            else "项目索引不可用；可先用 list 或 search_files 了解目录结构。"
        )

        messages: list[dict] = [
            {
                "role": "system",
                "content": self.SYSTEM_PROMPT.format(
                    tool_descriptions="\n".join(descriptions),
                    model_name=model_name.strip() or "未知",
                    permission_desc=describe_permission(permission_mode),
                    project_map=project_map,
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
