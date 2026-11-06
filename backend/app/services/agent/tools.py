"""Agent 可调用的工具：list / read / search / write_patch / search_text 等。

**本地模式适配：** 所有文件访问都走 ``SandboxService`` 的抽象方法
（``list_files`` / ``read_file`` / ``write_file``），搜索用 Python 递归遍历实现，
不依赖 ``ls`` / ``rg`` / ``cat`` 等 shell 命令。Docker 模式恢复后工具层零改动。

**安全边界：** 所有路径参数都必须先经过 ``core.security.validate_workspace_path``，
任何 ValueError 都直接抛出，绝不吞掉。``read`` 限制 2000 行、``search`` 最多 100 条
匹配，防止模型用工具产出超大结果占满 token。
"""

import re
import time
from typing import Any

from pydantic import BaseModel

from app.core.security import validate_workspace_path
from app.services.permissions import can_write, normalize_permission_mode
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
]


class ToolRegistry:
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
