"""Agent 可调用的 4 个工具：list / read / search / write_patch。

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
from app.services.sandbox import SandboxService

MAX_READ_LINES = 2000
MAX_SEARCH_MATCHES = 100
MAX_SEARCH_FILES = 200
MAX_SEARCH_DEPTH = 4
MAX_SEARCH_FILE_BYTES = 1024 * 1024
SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".venv",
    "venv",
    "__pycache__",
    "target",
}

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
        "在指定目录下递归搜索文本（深度 ≤ 4 层），返回 path:line: content。query 可以是正则。",
        {
            "query": {"type": "string", "description": "要搜索的文本或正则表达式"},
            "path": {"type": "string", "description": "workspace 相对目录路径；空字符串表示根目录"},
        },
        ["query", "path"],
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
        elif name == "write_patch":
            result = self._write_patch(args, sandbox, workspace_path, dry_run=dry_run)
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

    def _write_patch(
        self,
        args: dict[str, Any],
        sandbox: SandboxService,
        workspace_path: str,
        dry_run: bool = False,
    ) -> ToolResult:
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


def _build_matcher(query: str):
    try:
        pattern = re.compile(query)
        return lambda line: pattern.search(line) is not None
    except re.error:
        return lambda line: query in line
