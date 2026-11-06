"""Agent 主循环。

输入 session + user message，输出 SSE 事件流：
``message.delta`` → ``tool.call`` → ``tool.result`` → ... → ``done``。

``max_steps`` / ``max_tokens`` 是硬限制，循环条件强制 break，不依赖模型自己停止。
``RecentToolCallTracker`` 是软检测：连续 window 次相同工具 + 相同入参时注入提示消息。
"""

import asyncio
import contextlib
import copy
import difflib
import hashlib
import json
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.core.config import Settings, get_settings
from app.core.events import (
    BaseSSEEvent,
    ChangeSetProposedEvent,
    CommandProposedEvent,
    DoneEvent,
    FileChangedEvent,
    MessageDeltaEvent,
    PatchProposedEvent,
    PlanUpdateEvent,
    RunOutputEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.agent.context import truncate_messages
from app.services.agent.mock_provider import MockProvider
from app.services.agent.pending import (
    PendingChangeSetRegistry,
    PendingCommandRegistry,
    PendingPatchRegistry,
)
from app.services.agent.plan import PlanState, apply_update, plan_from_json
from app.services.agent.provider import LLMProvider, ToolCallChunk
from app.services.agent.tools import EDIT_TOOL_NAMES, ToolRegistry, ToolResult
from app.services.commands import truncate_output
from app.services.permissions import normalize_permission_mode
from app.services.repo_index import RepoIndex
from app.services.sandbox import SandboxService

MAX_STEPS_DEFAULT = 25
MAX_TOKENS_DEFAULT = 200000
PATCH_APPROVAL_TIMEOUT_SECONDS = 120.0

REJECTED_PATCH_SUMMARY = "user rejected the patch"
REJECTED_CHANGE_SET_SUMMARY = "user rejected the change set"
REJECTED_COMMAND_SUMMARY = "user rejected the command"


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
- 新建文件或整文件重写用 write_file；删除文件用 delete_file；修改多个已有文件用 apply_patch（单批次 1–10 个文件）
- write_patch 仅用于单个文件的精确替换；多文件任务不要拆成多次 write_patch
- 删除操作永远需要用户确认；不要重复提交被拒绝的变更
- 使用中文回复用户

验证规则：
- 编辑类任务完成后，优先运行项目里已有的验证入口：package.json 里的 npm run build / npm test、pytest、python -m compileall 等；项目里没有验证入口时，运行用户指定的命令
- 调用 run_command 时用 reason 说明这次验证要确认什么
- 命令失败时先读输出里的报错，修改文件后重跑；最多自动修复 2 轮，之后停止重试并向用户说明失败原因、已尝试的修复和建议的下一步
- 只读命令（ls / dir / pwd / git status / git diff / cat / type / head / tail）可直接执行，其余命令需要用户审批；被拒绝的命令绝不重复提交
- 不要运行会安装依赖、修改系统配置或需要交互输入的命令
"""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        tools: ToolRegistry | None = None,
        sandbox: SandboxService | None = None,
        history: list[dict] | None = None,
        pending_patches: PendingPatchRegistry | None = None,
        pending_change_sets: PendingChangeSetRegistry | None = None,
        pending_commands: PendingCommandRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.sandbox = sandbox
        self.history = history or []
        self.pending_patches = pending_patches
        self.pending_change_sets = pending_change_sets
        self.pending_commands = pending_commands
        self.settings = settings or get_settings()

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
        max_commands = self.settings.max_commands_per_run
        max_fix_rounds = self.settings.max_auto_fix_rounds
        commands_executed = 0
        fix_rounds = 0
        fix_limit_reached = False
        rejected_commands: set[str] = set()

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
                elif tool_name == "run_command":
                    prepared_result = await tools.call(
                        "run_command",
                        arguments,
                        sandbox,
                        session_workspace_path,
                        permission_mode=permission_mode,
                        repo_index=repo_index,
                    )
                    extra = prepared_result.extra or {}
                    command = str(extra.get("command") or "")
                    timeout_seconds = float(
                        extra.get("timeout") or self.settings.command_timeout_default
                    )
                    reason = str(extra.get("reason") or "")

                    if not prepared_result.ok:
                        result = prepared_result
                    elif fix_limit_reached:
                        result = ToolResult(
                            ok=False,
                            summary="自动修复已达上限，本次 run 不再执行命令",
                            details="请向用户说明失败原因、已尝试的修复与建议的下一步。",
                        )
                    elif command in rejected_commands:
                        result = ToolResult(
                            ok=False,
                            summary="该命令已被拒绝，不要重复提交",
                            details="请换一种方式验证，或先向用户说明必要性。",
                        )
                    else:
                        accepted = True
                        if extra.get("needs_approval") and self.pending_commands is not None:
                            command_id = str(uuid4())
                            self.pending_commands.register(
                                command_id,
                                session_id=session_id,
                                command=command,
                                cwd=session_workspace_path,
                                timeout=timeout_seconds,
                                reason=reason,
                            )
                            yield CommandProposedEvent(
                                command_id=command_id,
                                command=command,
                                cwd=session_workspace_path,
                                timeout_seconds=int(timeout_seconds),
                                reason=reason,
                                call_id=call_id,
                            )
                            try:
                                accepted = await self.pending_commands.wait(
                                    command_id, timeout=patch_approval_timeout
                                )
                            finally:
                                self.pending_commands.discard(command_id)

                            if not accepted:
                                rejected_commands.add(command)

                        if not accepted:
                            result = ToolResult(
                                ok=False,
                                summary=REJECTED_COMMAND_SUMMARY,
                                details="用户拒绝了该命令，请勿重复提交；如确有必要，先向用户说明原因。",
                                extra={"command": command, "rejected": True},
                            )
                        elif commands_executed >= max_commands:
                            result = ToolResult(
                                ok=False,
                                summary=f"本次 run 的命令数已达上限（{max_commands}）",
                                details="请先总结当前进展，等待用户确认后再继续。",
                                extra={"command": command, "limit_reached": True},
                            )
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        f"本次 run 的命令执行次数已达上限（{max_commands}），"
                                        "请停止执行新命令，向用户总结当前进展。"
                                    ),
                                }
                            )
                        else:
                            chunks: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

                            async def _on_output(stream_name: str, chunk: str) -> None:
                                await chunks.put((stream_name, chunk))

                            exec_started = time.perf_counter()
                            exec_task = asyncio.create_task(
                                sandbox.exec_stream(
                                    session_workspace_path,
                                    command,
                                    timeout_seconds,
                                    on_output=_on_output,
                                )
                            )
                            try:
                                while True:
                                    try:
                                        _stream, chunk = await asyncio.wait_for(
                                            chunks.get(), 0.05
                                        )
                                    except asyncio.TimeoutError:
                                        if exec_task.done():
                                            break
                                        continue
                                    yield RunOutputEvent(
                                        stream="stdout", chunk=chunk, call_id=call_id
                                    )
                                exit_code, output = await exec_task
                            except asyncio.CancelledError:
                                exec_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError, Exception):
                                    await exec_task
                                raise

                            commands_executed += 1
                            duration_ms = int((time.perf_counter() - exec_started) * 1000)
                            timed_out = exit_code == 124
                            details, truncated = truncate_output(output)
                            result = ToolResult(
                                ok=exit_code == 0,
                                summary=(
                                    f"命令执行{'超时' if timed_out else '完成'}："
                                    f"exit_code={exit_code}"
                                ),
                                details=details or None,
                                duration_ms=duration_ms,
                                extra={
                                    "command": command,
                                    "reason": reason,
                                    "exit_code": exit_code,
                                    "timed_out": timed_out,
                                    "truncated": truncated,
                                    "duration_ms": duration_ms,
                                },
                            )
                elif tool_name in EDIT_TOOL_NAMES:
                    result_holder: list[ToolResult] = []
                    async for change_event in self._edit_tool_events(
                        tool_name=tool_name,
                        arguments=arguments,
                        tools=tools,
                        sandbox=sandbox,
                        workspace_path=session_workspace_path,
                        permission_mode=permission_mode,
                        repo_index=repo_index,
                        session_id=session_id,
                        timeout=patch_approval_timeout,
                        holder=result_holder,
                    ):
                        yield change_event
                    result = (
                        result_holder[0]
                        if result_holder
                        else ToolResult(ok=False, summary="edit tool produced no result")
                    )
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

                command_failed = (
                    tool_name == "run_command"
                    and isinstance(result.extra, dict)
                    and "exit_code" in result.extra
                )
                fix_limit_hit = False
                if command_failed:
                    if result.ok:
                        fix_rounds = 0
                    else:
                        fix_rounds += 1
                        if fix_rounds <= max_fix_rounds:
                            tool_content += (
                                f"\n验证失败，请修复后重跑"
                                f"（自动修复第 {fix_rounds}/{max_fix_rounds} 轮）。"
                            )
                        else:
                            fix_limit_reached = True
                            fix_limit_hit = True

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tool_content,
                    }
                )

                if fix_limit_hit:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                f"自动修复已达上限（{max_fix_rounds} 轮），停止自动重试。"
                                "请用中文向用户说明：失败原因、已尝试的修复、建议的下一步。"
                            ),
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

    async def _edit_tool_events(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tools: ToolRegistry,
        sandbox: SandboxService,
        workspace_path: str,
        permission_mode: str,
        repo_index: RepoIndex | None,
        session_id: str,
        timeout: float,
        holder: list[ToolResult],
    ) -> AsyncIterator[BaseSSEEvent]:
        """编辑类工具的完整生命周期：预览 → 提案/自动 → 决议 → 原子落盘。"""
        preview = await tools.call(
            tool_name,
            arguments,
            sandbox,
            workspace_path,
            dry_run=True,
            permission_mode=permission_mode,
            repo_index=repo_index,
        )
        files = (preview.extra or {}).get("files") if preview.ok and preview.extra else None
        if not preview.ok or not isinstance(files, list) or not files:
            holder.append(preview)
            return

        change_set_id = str(uuid4())
        mode = normalize_permission_mode(permission_mode)
        is_delete = tool_name == "delete_file"

        # auto：非删除操作直接落盘，并发出只读摘要卡片。
        if mode == "auto" and not is_delete:
            result = await tools.call(
                tool_name,
                _with_expected(arguments, files),
                sandbox,
                workspace_path,
                permission_mode=permission_mode,
                repo_index=repo_index,
            )
            yield ChangeSetProposedEvent(
                change_set_id=change_set_id,
                files=files,
                auto=True,
                auto_applied=result.ok,
            )
            if result.ok:
                for entry in files:
                    yield FileChangedEvent(
                        path=str(entry.get("path") or ""),
                        operation=_event_operation(entry),
                    )
            holder.append(result)
            return

        if self.pending_change_sets is None:
            holder.append(preview)
            return

        # approval（删除在 auto 下同样走审批）。
        self.pending_change_sets.register(
            change_set_id, session_id=session_id, files=files
        )
        yield ChangeSetProposedEvent(
            change_set_id=change_set_id, files=files, auto_applied=False
        )
        decisions = await self.pending_change_sets.wait(change_set_id, timeout=timeout)

        accepted = [
            entry for entry in files if decisions.get(str(entry.get("path")), False)
        ]
        rejected = [
            entry for entry in files if not decisions.get(str(entry.get("path")), False)
        ]

        if not accepted:
            result = ToolResult(
                ok=False,
                summary=REJECTED_CHANGE_SET_SUMMARY,
                details="用户拒绝了该批次，请重新规划或说明原因。",
                extra={
                    "change_set_id": change_set_id,
                    "rejected": True,
                    "changes": [_rejected_change(entry) for entry in files],
                },
            )
            self.pending_change_sets.complete(
                change_set_id,
                {
                    "applied": [],
                    "rejected": [str(entry.get("path")) for entry in files],
                    "failed": [],
                },
            )
            self.pending_change_sets.discard(change_set_id)
            holder.append(result)
            return

        apply_args = _filtered_edit_args(tool_name, arguments, files, accepted)
        result = await tools.call(
            tool_name,
            _with_expected(apply_args, accepted),
            sandbox,
            workspace_path,
            permission_mode=permission_mode,
            repo_index=repo_index,
        )

        extra = result.extra if result.extra is not None else {}
        changes = list(extra.get("changes") or [])
        if rejected:
            changes.extend(_rejected_change(entry) for entry in rejected)
        extra["changes"] = changes
        extra["change_set_id"] = change_set_id
        result.extra = extra
        if rejected:
            rejected_paths = "、".join(str(entry.get("path")) for entry in rejected)
            result.summary = f"{result.summary}；已拒绝：{rejected_paths}"

        applied_paths = [str(entry.get("path")) for entry in accepted] if result.ok else []
        failed_paths = [] if result.ok else [str(entry.get("path")) for entry in accepted]
        if result.ok:
            for entry in accepted:
                yield FileChangedEvent(
                    path=str(entry.get("path") or ""),
                    operation=_event_operation(entry),
                )

        self.pending_change_sets.complete(
            change_set_id,
            {
                "applied": applied_paths,
                "rejected": [str(entry.get("path")) for entry in rejected],
                "failed": failed_paths,
            },
        )
        self.pending_change_sets.discard(change_set_id)
        holder.append(result)

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


def _with_expected(arguments: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """把预览阶段的 sha/exists 附加为工具内部参数，用于落盘前冲突检测。"""
    data = dict(arguments)
    data["_expected"] = {
        str(entry.get("path")): {
            "sha": entry.get("sha"),
            "exists": bool(entry.get("exists")),
        }
        for entry in entries
    }
    return data


def _filtered_edit_args(
    tool_name: str,
    arguments: dict[str, Any],
    files: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
) -> dict[str, Any]:
    """apply_patch 只把被接受的子集传给工具；单文件工具原样返回。"""
    if tool_name != "apply_patch":
        return dict(arguments)
    raw_files = arguments.get("files")
    if not isinstance(raw_files, list):
        return dict(arguments)
    selected: list[Any] = []
    for entry in accepted:
        index = entry.get("index")
        if isinstance(index, int) and 0 <= index < len(raw_files):
            selected.append(raw_files[index])
            continue
        for item in raw_files:
            if isinstance(item, dict) and str(item.get("path") or "") == str(entry.get("path")):
                selected.append(item)
                break
    return {"files": selected}


def _rejected_change(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(entry.get("path") or ""),
        "operation": str(entry.get("operation") or "update"),
        "added": 0,
        "deleted": 0,
        "status": "rejected",
    }


def _event_operation(entry: dict[str, Any]) -> str:
    return "delete" if entry.get("operation") == "delete" else "write"


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
