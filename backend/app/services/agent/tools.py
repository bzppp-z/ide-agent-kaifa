"""Agent 可调用的工具：list / read / search / write_patch / search_text 等。

**本地模式适配：** 所有文件访问都走 ``SandboxService`` 的抽象方法
（``list_files`` / ``read_file`` / ``write_file``），搜索用 Python 递归遍历实现，
不依赖 ``ls`` / ``rg`` / ``cat`` 等 shell 命令。Docker 模式恢复后工具层零改动。

**安全边界：** 所有路径参数都必须先经过 ``core.security.validate_workspace_path``，
任何 ValueError 都直接抛出，绝不吞掉。``read`` 限制 2000 行、``search`` 最多 100 条
匹配，防止模型用工具产出超大结果占满 token。
"""

import difflib
import hashlib
import re
import time
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.security import validate_workspace_path
from app.services.checkpoints import CheckpointService
from app.services.commands import (
    is_dangerous_command,
    is_interactive_command,
    is_readonly_command,
)
from app.services.permissions import (
    can_delete,
    can_run_command,
    can_write,
    normalize_permission_mode,
)
from app.services.repo_index import (
    MAX_SYMBOL_FILE_BYTES,
    SKIP_DIR_NAMES,
    RepoIndex,
    SearchPage,
)
from app.services.sandbox import SandboxService

MAX_READ_LINES = 2000
MAX_SEARCH_MATCHES = 100
MAX_SEARCH_FILES = 200
MAX_SEARCH_DEPTH = 4
MAX_SEARCH_FILE_BYTES = MAX_SYMBOL_FILE_BYTES
MAX_AGENT_SEARCH_RESULTS = 50

TRUNCATED_MARKER = "[truncated]"

_SETTINGS = get_settings()
MAX_FILES_PER_CHANGE_SET = _SETTINGS.max_files_per_change_set
MAX_AGENT_WRITE_BYTES = _SETTINGS.max_agent_write_bytes

EDIT_TOOL_NAMES = ("write_file", "delete_file", "apply_patch")


class ToolResult(BaseModel):
    ok: bool
    summary: str
    details: str | None = None
    duration_ms: int = 0
    extra: dict[str, Any] | None = None


def _function_schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _function_schema(
        "list",
        "列出 workspace 内指定目录下的文件和子目录。path 为空字符串表示 workspace 根目录。",
        {"path": {"type": "string", "description": "workspace 相对路径；空字符串表示根目录"}},
        ["path"],
    ),
    _function_schema(
        "read",
        "读取 workspace 内单个文件的全部内容；超过 2000 行会被截断。",
        {"path": {"type": "string", "description": "workspace 相对文件路径"}},
        ["path"],
    ),
    _function_schema(
        "search",
        "旧版文本检索别名（深度 ≤ 4 层）；新任务优先使用 search_text。",
        {
            "query": {"type": "string", "description": "要搜索的文本或正则表达式"},
            "path": {"type": "string", "description": "workspace 相对目录路径；空字符串表示根目录"},
        },
        ["query", "path"],
    ),
    _function_schema(
        "search_files",
        "按路径片段检索文件（不读取文件内容），返回匹配的相对路径，可用 offset 翻页。",
        {
            "fragment": {"type": "string", "description": "路径中要匹配的片段（不区分大小写）"},
            "limit": {"type": "integer", "description": "本页最多返回条数，默认 50，上限 50"},
            "offset": {"type": "integer", "description": "分页偏移，默认 0"},
        },
        ["fragment"],
    ),
    _function_schema(
        "find_symbols",
        "按名称检索顶层符号（函数/类/接口/类型等），返回 path:line: signature。",
        {
            "name": {"type": "string", "description": "符号名称片段（不区分大小写）"},
            "limit": {"type": "integer", "description": "本页最多返回条数，默认 50，上限 50"},
            "offset": {"type": "integer", "description": "分页偏移，默认 0"},
        },
        ["name"],
    ),
    _function_schema(
        "search_text",
        "按需读取文件内容做文本/正则检索，返回 path:line: content；跳过目录与 1MB 以上文件。",
        {
            "query": {"type": "string", "description": "要搜索的文本或正则表达式"},
            "path": {"type": "string", "description": "workspace 相对目录或文件路径；空字符串表示根目录"},
            "limit": {"type": "integer", "description": "本页最多返回条数，默认 50，上限 50"},
            "offset": {"type": "integer", "description": "分页偏移，默认 0"},
        },
        ["query", "path"],
    ),
    _function_schema(
        "update_plan",
        (
            "更新本次任务计划（整份快照）。items 需 3–8 项；status 只能是 "
            "pending/in_progress/done/failed/skipped；同一时刻最多 1 项 in_progress。"
        ),
        {
            "items": {
                "type": "array",
                "description": "完整计划项数组（不是增量）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "已有计划项 id；首次可省略"},
                        "title": {"type": "string", "description": "计划项标题"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done", "failed", "skipped"],
                        },
                        "note": {"type": "string", "description": "失败/跳过时的原因说明"},
                    },
                    "required": ["title", "status"],
                },
            }
        },
        ["items"],
    ),
    _function_schema(
        "write_patch",
        "在单个文件中把唯一的 search 文本替换为 replace；匹配 0 次或多次时拒绝执行。",
        {
            "path": {"type": "string", "description": "workspace 相对文件路径"},
            "search": {"type": "string", "description": "要替换的原文，必须唯一匹配"},
            "replace": {"type": "string", "description": "替换后的文本"},
        },
        ["path", "search", "replace"],
    ),
    _function_schema(
        "write_file",
        "新建文件或整文件写入。文件已存在时必须显式传 overwrite=true。",
        {
            "path": {"type": "string", "description": "workspace 相对文件路径"},
            "content": {"type": "string", "description": "文件的完整内容"},
            "overwrite": {"type": "boolean", "description": "覆盖已存在文件，默认 false"},
        },
        ["path", "content"],
    ),
    _function_schema(
        "delete_file",
        "删除 workspace 内的单个文件（目录不可删除）；删除永远需要用户确认。",
        {"path": {"type": "string", "description": "workspace 相对文件路径"}},
        ["path"],
    ),
    _function_schema(
        "run_command",
        (
            "在 workspace 内执行一条 shell 命令（用于构建、测试等验证）。"
            "只读命令可直接执行，其余命令需要用户审批。"
        ),
        {
            "command": {"type": "string", "description": "要执行的完整命令"},
            "reason": {"type": "string", "description": "为什么要执行这条命令（验证目的）"},
            "timeout_seconds": {
                "type": "integer",
                "description": "超时秒数，默认 30，上限 120",
            },
        },
        ["command", "reason"],
    ),
    _function_schema(
        "apply_patch",
        (
            "一次批量修改 1–10 个文件。每项提供 path + search/replace（唯一匹配），"
            "或 path + before/after（整文件替换，before 必须与当前内容一致）。"
        ),
        {
            "files": {
                "type": "array",
                "description": "文件变更数组，1–10 项",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "workspace 相对文件路径"},
                        "search": {"type": "string", "description": "要唯一匹配的原文"},
                        "replace": {"type": "string", "description": "替换后的文本"},
                        "before": {"type": "string", "description": "整文件替换前的原文"},
                        "after": {"type": "string", "description": "整文件替换后的全文"},
                    },
                    "required": ["path"],
                },
            }
        },
        ["files"],
    ),
]


class ToolRegistry:
    def __init__(
        self,
        checkpoints: CheckpointService | None = None,
        session_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        # 检查点服务由 main / AgentLoop 注入；工具层不直接访问数据库。
        self.checkpoints = checkpoints
        self.session_id = session_id
        self.checkpoint_id = checkpoint_id

    def to_schema(self) -> list[dict[str, Any]]:
        """返回 OpenAI function calling 格式的工具列表（深拷贝，防止调用方修改）。"""
        return [schema.copy() for schema in TOOL_SCHEMAS]

    async def call(
        self,
        name: str,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        dry_run: bool = False,
        permission_mode: str = "approval",
        repo_index: RepoIndex | None = None,
    ) -> ToolResult:
        if not isinstance(args, dict):
            args = {}

        started = time.perf_counter()
        if name == "list":
            result = self._list(args, sandbox, workspace_path)
        elif name == "read":
            result = self._read(args, sandbox, workspace_path)
        elif name == "search":
            result = self._search(args, sandbox, workspace_path)
        elif name == "search_files":
            result = self._search_files(args, sandbox, workspace_path, repo_index)
        elif name == "find_symbols":
            result = self._find_symbols(args, sandbox, workspace_path, repo_index)
        elif name == "search_text":
            result = self._search_text(args, sandbox, workspace_path, repo_index)
        elif name == "update_plan":
            result = ToolResult(
                ok=False,
                summary="update_plan 由 AgentLoop 处理，tool registry 不直接执行",
            )
        elif name == "write_patch":
            result = self._write_patch(
                args,
                sandbox,
                workspace_path,
                dry_run=dry_run,
                permission_mode=permission_mode,
                repo_index=repo_index,
            )
        elif name == "write_file":
            result = self._write_file(
                args,
                sandbox,
                workspace_path,
                dry_run=dry_run,
                permission_mode=permission_mode,
                repo_index=repo_index,
            )
        elif name == "delete_file":
            result = self._delete_file(
                args,
                sandbox,
                workspace_path,
                dry_run=dry_run,
                permission_mode=permission_mode,
                repo_index=repo_index,
            )
        elif name == "apply_patch":
            result = self._apply_patch(
                args,
                sandbox,
                workspace_path,
                dry_run=dry_run,
                permission_mode=permission_mode,
                repo_index=repo_index,
            )
        elif name == "run_command":
            # 只做校验与超时归一化；真正执行由 AgentLoop 负责（需要审批与流式输出）。
            result = self._run_command(args, permission_mode)
        else:
            result = ToolResult(ok=False, summary=f"unknown tool: {name}")

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ----------------------------------------------------------------- tools

    def _list(self, args: dict[str, Any], sandbox: SandboxService, workspace_path: str) -> ToolResult:
        raw_path = _require_str_arg(args, "path")
        relative = _normalize_optional_root(raw_path)

        try:
            entries = sandbox.list_files(workspace_path, relative)
        except FileNotFoundError:
            return ToolResult(ok=False, summary=f"path not found: {raw_path}")

        files = [entry for entry in entries if entry["type"] == "file"]
        dirs = [entry for entry in entries if entry["type"] == "dir"]
        display = relative or "."
        details = "\n".join(
            f"{'d' if entry['type'] == 'dir' else 'f'} {entry['path']}" for entry in entries
        )

        return ToolResult(
            ok=True,
            summary=f"{display}: {len(files)} file(s), {len(dirs)} dir(s)",
            details=details or None,
            extra={"entries": entries},
        )

    def _read(self, args: dict[str, Any], sandbox: SandboxService, workspace_path: str) -> ToolResult:
        raw_path = _require_str_arg(args, "path")
        relative = validate_workspace_path(raw_path)

        try:
            content = sandbox.read_file(workspace_path, relative)
        except FileNotFoundError:
            return ToolResult(ok=False, summary=f"file not found: {relative}")

        lines = content.splitlines()
        truncated = len(lines) > MAX_READ_LINES
        if truncated:
            details = "\n".join(lines[:MAX_READ_LINES]) + "\n" + TRUNCATED_MARKER
            summary = f"read {relative}: {len(lines)} lines (truncated to {MAX_READ_LINES})"
        else:
            details = "\n".join(lines)
            summary = f"read {relative}: {len(lines)} lines"

        return ToolResult(
            ok=True,
            summary=summary,
            details=details,
            extra={"lines": len(lines), "truncated": truncated},
        )

    def _search(
        self, args: dict[str, Any], sandbox: SandboxService, workspace_path: str
    ) -> ToolResult:
        query = _require_str_arg(args, "query")
        raw_path = _require_str_arg(args, "path")
        if not query:
            raise ValueError("search query must not be empty")

        relative = _normalize_optional_root(raw_path)
        matcher = _build_matcher(query)
        matches: list[str] = []
        files_scanned = 0
        truncated = False

        def walk(directory: str, depth: int) -> None:
            nonlocal files_scanned, truncated
            if truncated:
                return

            try:
                entries = sandbox.list_files(workspace_path, directory)
            except FileNotFoundError:
                return

            for entry in entries:
                if len(matches) >= MAX_SEARCH_MATCHES:
                    truncated = True
                    return

                if entry["type"] == "dir":
                    if depth + 1 <= MAX_SEARCH_DEPTH and entry["name"].lower() not in SKIP_DIR_NAMES:
                        walk(entry["path"], depth + 1)
                    continue

                if files_scanned >= MAX_SEARCH_FILES:
                    truncated = True
                    return
                if entry["size"] > MAX_SEARCH_FILE_BYTES:
                    continue

                files_scanned += 1
                try:
                    content = sandbox.read_file(workspace_path, entry["path"])
                except (FileNotFoundError, OSError, UnicodeDecodeError):
                    continue

                for line_number, line in enumerate(content.splitlines(), start=1):
                    if matcher(line):
                        matches.append(f"{entry['path']}:{line_number}: {line}")
                        if len(matches) >= MAX_SEARCH_MATCHES:
                            truncated = True
                            break
                if truncated:
                    return

        try:
            sandbox.list_files(workspace_path, relative)
        except FileNotFoundError:
            return ToolResult(ok=False, summary=f"path not found: {raw_path}")

        walk(relative, 0)

        display = relative or "."
        summary = f"found {len(matches)} match(es) for {query!r} in {display}"
        if truncated:
            summary += " (truncated)"

        return ToolResult(
            ok=True,
            summary=summary,
            details="\n".join(matches) or None,
            extra={
                "matches": len(matches),
                "files_scanned": files_scanned,
                "truncated": truncated,
            },
        )

    def _search_files(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        repo_index: RepoIndex | None,
    ) -> ToolResult:
        fragment = _require_str_arg(args, "fragment")
        index = self._resolve_index(repo_index, sandbox, workspace_path)
        page = index.search_files(fragment, limit=_int_arg(args, "limit", MAX_AGENT_SEARCH_RESULTS), offset=_int_arg(args, "offset"))
        return _page_result(page, f"file(s) matching {fragment!r}")

    def _find_symbols(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        repo_index: RepoIndex | None,
    ) -> ToolResult:
        name = _require_str_arg(args, "name")
        index = self._resolve_index(repo_index, sandbox, workspace_path)
        page = index.find_symbols(name, limit=_int_arg(args, "limit", MAX_AGENT_SEARCH_RESULTS), offset=_int_arg(args, "offset"))
        return _page_result(page, f"symbol(s) matching {name!r}")

    def _search_text(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        repo_index: RepoIndex | None,
    ) -> ToolResult:
        query = _require_str_arg(args, "query")
        raw_path = _require_str_arg(args, "path")
        if not query:
            raise ValueError("search query must not be empty")
        relative = _normalize_optional_root(raw_path)

        if not _path_exists(sandbox, workspace_path, relative):
            return ToolResult(ok=False, summary=f"path not found: {raw_path}")

        index = self._resolve_index(repo_index, sandbox, workspace_path)
        page = index.search_text(
            query,
            path=relative,
            limit=_int_arg(args, "limit", MAX_AGENT_SEARCH_RESULTS),
            offset=_int_arg(args, "offset"),
        )
        return _page_result(page, f"match(es) for {query!r}")

    def _resolve_index(
        self, repo_index: RepoIndex | None, sandbox: SandboxService, workspace_path: str
    ) -> RepoIndex:
        """调用方未传索引时建一个临时实例（未构建 → 实时扫描）。"""
        if repo_index is not None:
            return repo_index
        return RepoIndex(sandbox, workspace_path, max_results=MAX_AGENT_SEARCH_RESULTS)

    def _write_patch(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        dry_run: bool = False,
        permission_mode: str = "approval",
        repo_index: RepoIndex | None = None,
    ) -> ToolResult:
        if not can_write(permission_mode):
            return ToolResult(
                ok=False,
                summary="当前为只读模式，禁止写入",
                details=f"permission_mode={normalize_permission_mode(permission_mode)}",
            )

        raw_path = _require_str_arg(args, "path")
        search = _require_str_arg(args, "search")
        replace = _require_str_arg(args, "replace")
        if not search:
            return ToolResult(ok=False, summary="search pattern must not be empty")

        relative = validate_workspace_path(raw_path)
        try:
            before = sandbox.read_file(workspace_path, relative)
        except FileNotFoundError:
            return ToolResult(ok=False, summary=f"file not found: {relative}")

        occurrences = before.count(search)
        if occurrences == 0:
            return ToolResult(ok=False, summary=f"pattern not found in {relative}")
        if occurrences > 1:
            return ToolResult(
                ok=False,
                summary=f"ambiguous match: pattern occurs {occurrences} times in {relative}",
            )

        after = before.replace(search, replace, 1)
        if dry_run:
            return ToolResult(
                ok=True,
                summary=f"patch preview for {relative}",
                details=f"would replace 1 occurrence in {relative}",
                extra={
                    "path": relative,
                    "before": before,
                    "after": after,
                    "applied": False,
                },
            )

        sandbox.write_file(workspace_path, relative, after)
        if repo_index is not None:
            repo_index.invalidate(relative)

        return ToolResult(
            ok=True,
            summary=f"patched {relative}",
            details=f"replaced 1 occurrence in {relative}",
            extra={"path": relative, "before": before, "after": after, "applied": True},
        )

    # ------------------------------------------------------- command tools

    def _run_command(self, args: dict[str, Any], permission_mode: str) -> ToolResult:
        """命令执行前的校验：只读模式、危险命令、交互式命令一律拒绝。"""
        command = _string_value(args, "command").strip()
        if not command:
            return ToolResult(ok=False, summary="command 不能为空")

        if not can_run_command(permission_mode):
            return ToolResult(
                ok=False,
                summary="只读模式不执行命令",
                details=f"permission_mode={normalize_permission_mode(permission_mode)}；请只读取与规划。",
            )
        if is_dangerous_command(command):
            return ToolResult(
                ok=False,
                summary="命令包含危险操作，已拒绝执行",
                details=f"被拒绝的命令：{command}",
            )
        if is_interactive_command(command):
            return ToolResult(
                ok=False,
                summary="该命令需要交互输入，agent 无法执行",
                details="请改用非交互参数（例如 npm init -y、python script.py）。",
            )

        reason = _string_value(args, "reason").strip()
        timeout = _int_arg(args, "timeout_seconds", _SETTINGS.command_timeout_default)
        timeout = max(1, min(timeout, _SETTINGS.command_timeout_max))
        readonly = is_readonly_command(command)

        return ToolResult(
            ok=True,
            summary=("只读命令，可直接执行：" if readonly else "命令待审批：") + command,
            details=f"reason: {reason or '(未填写)'}",
            extra={
                "command": command,
                "reason": reason,
                "timeout": timeout,
                "needs_approval": not readonly,
                "readonly": readonly,
            },
        )

    # ------------------------------------------------- batch edit tools

    def _write_file(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        dry_run: bool = False,
        permission_mode: str = "approval",
        repo_index: RepoIndex | None = None,
    ) -> ToolResult:
        if not can_write(permission_mode):
            return ToolResult(
                ok=False,
                summary="当前为只读模式，禁止写入",
                details=f"permission_mode={normalize_permission_mode(permission_mode)}",
            )
        try:
            raw_path = _require_str_arg(args, "path")
            relative = validate_workspace_path(raw_path)
            content = _require_str_arg(args, "content")
            overwrite = _bool_arg(args, "overwrite", False)
            existing = _read_existing(sandbox, workspace_path, relative)
            if existing is not None and not overwrite:
                return ToolResult(
                    ok=False,
                    summary=f"file already exists: {relative}",
                    details="如需整文件覆盖，请传 overwrite=true。",
                )
            entry = _build_entry(
                sandbox,
                workspace_path,
                relative,
                operation="update" if existing is not None else "create",
                before=existing or "",
                after=content,
                exists=existing is not None,
                index=0,
            )
        except (ValueError, FileNotFoundError, UnicodeDecodeError, OSError) as exc:
            return ToolResult(ok=False, summary=f"无法准备变更：{exc}")

        return self._finish_edits(
            args, [entry], sandbox, workspace_path, dry_run, repo_index
        )

    def _delete_file(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        dry_run: bool = False,
        permission_mode: str = "approval",
        repo_index: RepoIndex | None = None,
    ) -> ToolResult:
        if not can_delete(permission_mode):
            return ToolResult(
                ok=False,
                summary="当前为只读模式，禁止删除",
                details=f"permission_mode={normalize_permission_mode(permission_mode)}",
            )
        try:
            raw_path = _require_str_arg(args, "path")
            relative = validate_workspace_path(raw_path)
            before = sandbox.read_file(workspace_path, relative)
            if len(before.encode("utf-8")) > MAX_AGENT_WRITE_BYTES:
                return ToolResult(ok=False, summary=f"文件超过 1MB 上限，拒绝删除：{relative}")
            entry = _build_entry(
                sandbox,
                workspace_path,
                relative,
                operation="delete",
                before=before,
                after="",
                exists=True,
                index=0,
            )
            info = _file_info(sandbox, workspace_path, relative)
            entry["size"] = int(info.get("size") or 0) if info else len(before.encode("utf-8"))
            entry["mtime"] = info.get("mtime") if info else None
        except (ValueError, FileNotFoundError, UnicodeDecodeError, OSError) as exc:
            return ToolResult(ok=False, summary=f"无法准备删除：{exc}")

        return self._finish_edits(
            args, [entry], sandbox, workspace_path, dry_run, repo_index
        )

    def _apply_patch(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        dry_run: bool = False,
        permission_mode: str = "approval",
        repo_index: RepoIndex | None = None,
    ) -> ToolResult:
        if not can_write(permission_mode):
            return ToolResult(
                ok=False,
                summary="当前为只读模式，禁止批量修改",
                details=f"permission_mode={normalize_permission_mode(permission_mode)}",
            )
        try:
            raw_files = args.get("files")
            if not isinstance(raw_files, list) or not raw_files:
                return ToolResult(ok=False, summary="files 必须是非空数组")
            if len(raw_files) > MAX_FILES_PER_CHANGE_SET:
                return ToolResult(
                    ok=False,
                    summary=f"单批次最多修改 {MAX_FILES_PER_CHANGE_SET} 个文件，本次 {len(raw_files)} 个",
                )

            entries: list[dict[str, Any]] = []
            seen: set[str] = set()
            for index, item in enumerate(raw_files):
                if not isinstance(item, dict):
                    return ToolResult(ok=False, summary=f"files[{index}] 必须是对象")
                relative = validate_workspace_path(_require_str_arg(item, "path"))
                if relative in seen:
                    return ToolResult(ok=False, summary=f"批次内路径重复：{relative}")
                seen.add(relative)
                existing = _read_existing(sandbox, workspace_path, relative)

                if "search" in item:
                    search = _string_value(item, "search")
                    replace = _string_value(item, "replace")
                    if not search:
                        return ToolResult(ok=False, summary=f"files[{index}] 的 search 不能为空")
                    if existing is None:
                        return ToolResult(ok=False, summary=f"file not found: {relative}")
                    occurrences = existing.count(search)
                    if occurrences == 0:
                        return ToolResult(ok=False, summary=f"pattern not found in {relative}")
                    if occurrences > 1:
                        return ToolResult(
                            ok=False,
                            summary=f"ambiguous match: pattern occurs {occurrences} times in {relative}",
                        )
                    before, after = existing, existing.replace(search, replace, 1)
                    operation = "update"
                else:
                    before_arg = _string_value(item, "before")
                    after_arg = _string_value(item, "after")
                    if "after" not in item:
                        return ToolResult(
                            ok=False, summary=f"files[{index}] 需要 search/replace 或 before/after"
                        )
                    if existing is None:
                        if before_arg:
                            return ToolResult(
                                ok=False,
                                summary=f"file not found: {relative}（新建时 before 必须为空）",
                            )
                        before, after, operation = "", after_arg, "create"
                    else:
                        if before_arg and before_arg != existing:
                            return ToolResult(
                                ok=False, summary=f"before 与 {relative} 当前内容不一致"
                            )
                        before, after, operation = existing, after_arg, "update"

                entries.append(
                    _build_entry(
                        sandbox,
                        workspace_path,
                        relative,
                        operation=operation,
                        before=before,
                        after=after,
                        exists=operation != "create",
                        index=index,
                    )
                )
        except (ValueError, FileNotFoundError, UnicodeDecodeError, OSError) as exc:
            return ToolResult(ok=False, summary=f"无法准备变更：{exc}")

        return self._finish_edits(
            args, entries, sandbox, workspace_path, dry_run, repo_index
        )

    def _finish_edits(
        self,
        args: dict[str, Any],
        entries: list[dict[str, Any]],
        sandbox: SandboxService,
        workspace_path: str,
        dry_run: bool,
        repo_index: RepoIndex | None,
    ) -> ToolResult:
        if dry_run:
            return _preview_result(entries)
        return self._apply_edits(args, entries, sandbox, workspace_path, repo_index)

    def _apply_edits(
        self,
        args: dict[str, Any],
        entries: list[dict[str, Any]],
        sandbox: SandboxService,
        workspace_path: str,
        repo_index: RepoIndex | None,
    ) -> ToolResult:
        expected = args.get("_expected") if isinstance(args.get("_expected"), dict) else {}

        # 冲突检测：落盘前重新读取每个文件，与预览阶段的 sha 比对。
        for entry in entries:
            relative = entry["path"]
            want = expected.get(relative) if isinstance(expected, dict) else None
            want_exists = bool(want.get("exists", entry["exists"])) if isinstance(want, dict) else entry["exists"]
            want_sha = want.get("sha") if isinstance(want, dict) else entry.get("sha")
            try:
                current = _read_existing(sandbox, workspace_path, relative)
            except UnicodeDecodeError:
                current = None
            current_exists = current is not None
            current_sha = hashlib.sha256((current or "").encode("utf-8")).hexdigest()
            if current_exists != want_exists or (want_exists and want_sha != current_sha):
                return ToolResult(
                    ok=False,
                    summary=f"批次冲突：{relative} 已被修改，整批未落盘",
                    details="文件内容与预览阶段不一致，请重新读取后再生成变更。",
                    extra={
                        "files": [{**item, "status": "conflict"} for item in entries],
                        "changes": [_change_record(item, "failed") for item in entries],
                        "conflict": True,
                    },
                )

        # 首次捕获原内容（惰性快照），失败不影响写入但会记录日志之外的异常。
        self._capture_checkpoints(workspace_path, [entry["path"] for entry in entries])

        written: list[dict[str, Any]] = []
        try:
            for entry in entries:
                if entry["operation"] == "delete":
                    sandbox.delete_file(workspace_path, entry["path"])
                else:
                    sandbox.write_file(workspace_path, entry["path"], entry["after"])
                written.append(entry)
        except (OSError, ValueError, FileNotFoundError) as exc:
            for entry in reversed(written):
                try:
                    if entry["exists"]:
                        sandbox.write_file(workspace_path, entry["path"], entry["before"])
                    else:
                        sandbox.delete_file(workspace_path, entry["path"])
                except (OSError, ValueError, FileNotFoundError):
                    pass
            return ToolResult(
                ok=False,
                summary=f"批次落盘失败，已回滚：{exc}",
                extra={
                    "files": [{**entry, "status": "failed"} for entry in entries],
                    "changes": [_change_record(entry, "failed") for entry in entries],
                    "failed": True,
                },
            )

        if repo_index is not None:
            for entry in entries:
                repo_index.invalidate(entry["path"])

        applied = [{**entry, "status": "applied"} for entry in entries]
        changes = [_change_record(entry, "applied") for entry in entries]
        summary = "已生效：" + "、".join(
            f"{entry['operation']} {entry['path']}" for entry in entries
        )
        return ToolResult(
            ok=True,
            summary=summary,
            details="\n".join(entry["diff"] for entry in entries),
            extra={"files": applied, "changes": changes, "applied": True},
        )

    def _capture_checkpoints(self, workspace_path: str, relative_paths: list[str]) -> None:
        if self.checkpoints is None or not self.session_id or not self.checkpoint_id:
            return
        for relative in relative_paths:
            self.checkpoints.capture_file(
                self.session_id,
                self.checkpoint_id,
                workspace_path,
                relative,
                workspace_path,
            )


def _string_value(args: dict[str, Any], key: str) -> str:
    value = args.get(key, "")
    return value if isinstance(value, str) else ""


def _bool_arg(args: dict[str, Any], key: str, default: bool) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return default


def _read_existing(
    sandbox: SandboxService, workspace_path: str, relative: str
) -> str | None:
    try:
        return sandbox.read_file(workspace_path, relative)
    except FileNotFoundError:
        return None


def _build_entry(
    sandbox: SandboxService,
    workspace_path: str,
    relative: str,
    *,
    operation: str,
    before: str,
    after: str,
    exists: bool,
    index: int,
) -> dict[str, Any]:
    after_bytes = len(after.encode("utf-8"))
    if after_bytes > MAX_AGENT_WRITE_BYTES:
        raise ValueError(
            f"{relative} 超过单文件 1MB 上限（{after_bytes} bytes）"
        )
    if len(before.encode("utf-8")) > MAX_AGENT_WRITE_BYTES:
        raise ValueError(f"{relative} 原文件超过 1MB 上限")
    note = None
    if operation == "create" and not _parent_exists(sandbox, workspace_path, relative):
        note = "将创建目录"
    return {
        "path": relative,
        "operation": operation,
        "before": before,
        "after": after,
        "diff": _render_patch_diff(relative, before, after),
        "sha": hashlib.sha256(before.encode("utf-8")).hexdigest(),
        "exists": exists,
        "index": index,
        "note": note,
    }


def _preview_result(entries: list[dict[str, Any]]) -> ToolResult:
    lines = []
    for entry in entries:
        line = f"{entry['operation']} {entry['path']}"
        if entry.get("note"):
            line += f"（{entry['note']}）"
        lines.append(line)
    return ToolResult(
        ok=True,
        summary=f"准备 {len(entries)} 个文件变更：" + "、".join(
            entry["path"] for entry in entries
        ),
        details="\n".join(lines),
        extra={"files": entries, "applied": False},
    )


def _parent_exists(sandbox: SandboxService, workspace_path: str, relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if len(parts) <= 1:
        return True
    parent = str(PurePosixPath(*parts[:-1]))
    try:
        sandbox.list_files(workspace_path, parent)
        return True
    except (FileNotFoundError, ValueError, OSError):
        return False


def _file_info(
    sandbox: SandboxService, workspace_path: str, relative: str
) -> dict[str, Any] | None:
    parts = PurePosixPath(relative).parts
    parent = str(PurePosixPath(*parts[:-1])) if len(parts) > 1 else ""
    try:
        entries = sandbox.list_files(workspace_path, parent)
    except (FileNotFoundError, ValueError, OSError):
        return None
    for entry in entries:
        if entry.get("path") == relative:
            return entry
    return None


def _change_record(entry: dict[str, Any], status: str) -> dict[str, Any]:
    added, deleted = _line_stats(entry.get("before", ""), entry.get("after", ""))
    return {
        "path": entry["path"],
        "operation": entry["operation"],
        "added": added,
        "deleted": deleted,
        "status": status,
    }


def _line_stats(before: str, after: str) -> tuple[int, int]:
    """用 difflib 统计新增/删除行数。"""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    added = deleted = 0
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


def _render_patch_diff(path: str, before: str, after: str) -> str:
    """生成统一 diff 文本；新建/删除文件也能得到完整增删内容。"""
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )


def _require_str_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"missing or invalid string argument: {key}")
    return value


def _normalize_optional_root(path: str) -> str:
    """目录类工具接受空字符串 / "." 表示 workspace 根目录；其余必须通过安全校验。"""
    stripped = path.strip()
    if stripped in ("", "."):
        return ""
    return validate_workspace_path(path)


def _int_arg(args: dict[str, Any], key: str, default: int = 0) -> int:
    value = args.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_result(page: SearchPage, label: str) -> ToolResult:
    """把索引分页结果转换成统一的 ToolResult（extra 字段固定）。"""
    summary = f"found {page.matches} {label}"
    if page.next_offset is not None:
        summary += f" (more available; next offset: {page.next_offset})"
    if page.index_building:
        summary += " (index building; scanned live)"

    return ToolResult(
        ok=True,
        summary=summary,
        details="\n".join(page.lines) or None,
        extra={
            "matches": page.matches,
            "files_scanned": page.files_scanned,
            "truncated": page.truncated,
            "next_offset": page.next_offset,
            "index_building": page.index_building,
        },
    )


def _path_exists(sandbox: SandboxService, workspace_path: str, relative: str) -> bool:
    try:
        sandbox.list_files(workspace_path, relative)
        return True
    except (FileNotFoundError, ValueError, OSError):
        pass
    if not relative:
        return False
    try:
        sandbox.read_file(workspace_path, relative)
        return True
    except UnicodeDecodeError:
        return True  # 存在但不可按 UTF-8 读取，交给检索层处理
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True


def _build_matcher(query: str):
    try:
        pattern = re.compile(query)
        return lambda line: pattern.search(line) is not None
    except re.error:
        return lambda line: query in line
