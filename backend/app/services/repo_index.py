"""会话内项目索引：只存相对路径、文件大小与顶层符号。

索引**不保存文件全文**；``search_text`` 命中时才按需通过 ``SandboxService``
读取单个文件内容（单文件上限 1MB）。索引未就绪（首次构建中 / 已失效）时，
检索方法退化为实时扫描，并在结果里标记 ``index_building=True``。
"""

import asyncio
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.core.security import validate_workspace_path
from app.services.sandbox import SandboxService

logger = logging.getLogger(__name__)

# 与 v1 搜索保持一致的跳过目录集合
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

INDEXABLE_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".go")
MAX_SYMBOL_FILE_BYTES = 1024 * 1024

# ------------------------------------------------------------------ 顶层符号正则
# 全部使用 ``match`` + 行首锚点，因此只识别顶格声明（忽略缩进的方法体）。

_JS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
        ),
        "function",
    ),
    (
        re.compile(
            r"^(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"
        ),
        "class",
    ),
    (
        re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?="),
        "const",
    ),
    (
        re.compile(r"^(?:export\s+)?(?:declare\s+)?interface\s+([A-Za-z_$][\w$]*)"),
        "interface",
    ),
    (
        re.compile(r"^(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*="),
        "type",
    ),
]

_PY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)"), "function"),
    (re.compile(r"^class\s+([A-Za-z_]\w*)"), "class"),
]

_GO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "function"),
    (re.compile(r"^type\s+([A-Za-z_]\w*)\s+struct\b"), "struct"),
    (re.compile(r"^type\s+([A-Za-z_]\w*)\s+interface\b"), "interface"),
]

_JAVA_PATTERN = re.compile(r"^(?:\w+\s+)*(class|interface|enum|record)\s+(\w+)")


def _extract_symbols(relative_path: str, content: str) -> list[dict[str, Any]]:
    """按行提取顶层符号，返回 path/line/name/kind 记录。"""
    lower = relative_path.lower()
    if lower.endswith((".js", ".jsx", ".ts", ".tsx")):
        patterns, java = _JS_PATTERNS, False
    elif lower.endswith(".py"):
        patterns, java = _PY_PATTERNS, False
    elif lower.endswith(".go"):
        patterns, java = _GO_PATTERNS, False
    elif lower.endswith(".java"):
        patterns, java = [], True
    else:
        return []

    symbols: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if java:
            match = _JAVA_PATTERN.match(line)
            if not match:
                continue
            kind = "class" if match.group(1) == "record" else match.group(1)
            name = match.group(2)
        else:
            found = None
            for pattern, matched_kind in patterns:
                match = pattern.match(line)
                if match:
                    found = (match.group(1), matched_kind)
                    break
            if found is None:
                continue
            name, kind = found

        symbols.append({"path": relative_path, "line": line_number, "name": name, "kind": kind})
    return symbols


def _is_indexable(relative_path: str) -> bool:
    return relative_path.lower().endswith(INDEXABLE_SUFFIXES)


@dataclass
class SearchPage:
    """一页检索结果；字段统一在索引层格式化为 ``path:line: ...``。"""

    lines: list[str]
    matches: int
    files_scanned: int
    truncated: bool
    next_offset: int | None
    index_building: bool


class RepoIndex:
    """单个会话的代码索引。

    ``build`` 是同步方法，由调用方放到线程池执行（避免阻塞事件循环）；
    构建完成后再原子替换 ``_files`` / ``_symbols``，检索永远读不到半成品。
    """

    def __init__(
        self,
        sandbox: SandboxService,
        workspace_path: str,
        max_files: int = 10000,
        max_results: int = 50,
    ) -> None:
        self.sandbox = sandbox
        self.workspace_path = workspace_path
        self.max_files = max(1, int(max_files))
        self.max_results = max(1, int(max_results))

        self._files: list[dict[str, Any]] = []
        self._symbols: list[dict[str, Any]] = []
        self._built = False
        self._stale = True
        self._build_truncated = False
        self._lock = threading.Lock()
        self._build_task: asyncio.Task | None = None

    # --------------------------------------------------------------- lifecycle

    @property
    def ready(self) -> bool:
        return self._built and not self._stale

    def schedule_build(self) -> None:
        """在后台线程构建索引；已在构建中则复用同一个 task。"""
        if self.ready:
            return
        if self._build_task is not None and not self._build_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - 仅在非异步上下文调用时兜底
            self.build()
            return
        self._build_task = loop.create_task(asyncio.to_thread(self._build_worker))

    def cancel_build(self) -> None:
        task = self._build_task
        if task is not None and not task.done():
            task.cancel()

    def mark_stale(self) -> None:
        """外部批量变更后标记索引失效，下次 ensure_container / 检索前重建。"""
        self._stale = True

    def invalidate(self, path: str) -> None:
        """单个文件写入/删除后增量更新索引；无法增量时退化为整体失效。"""
        try:
            relative = validate_workspace_path(path)
        except ValueError:
            self.mark_stale()
            return

        with self._lock:
            if not self.ready:
                self._stale = True
                return

            self._files = [entry for entry in self._files if entry["path"] != relative]
            self._symbols = [
                symbol for symbol in self._symbols if symbol["path"] != relative
            ]

            try:
                content = self.sandbox.read_file(self.workspace_path, relative)
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                return

            size = len(content.encode("utf-8", errors="ignore"))
            self._files.append({"path": relative, "size": size})
            self._files.sort(key=lambda entry: entry["path"])
            if _is_indexable(relative) and size <= MAX_SYMBOL_FILE_BYTES:
                self._symbols.extend(_extract_symbols(relative, content))
                self._symbols.sort(key=lambda symbol: (symbol["path"], symbol["line"]))

    def build(self) -> None:
        with self._lock:
            if self._built and not self._stale:
                return
            files, symbols, truncated = self._scan()
            self._files = files
            self._symbols = symbols
            self._build_truncated = truncated
            self._built = True
            self._stale = False

    def _build_worker(self) -> None:
        try:
            self.build()
        except Exception:  # pragma: no cover - 后台构建失败不能影响主流程
            logger.exception("repo index build failed: %s", self.workspace_path)

    def summary(self) -> str:
        """项目地图摘要（仅文件数/目录/符号数），用于系统提示。"""
        if not self._built:
            return "索引正在后台构建；检索工具会实时扫描并返回 index_building=true。"
        if self._stale:
            return "索引已失效，正在等待重建；检索工具会实时扫描并返回 index_building=true。"

        dir_counts: dict[str, int] = {}
        root_files = 0
        for entry in self._files:
            path = entry["path"]
            if "/" in path:
                top = path.split("/", 1)[0]
                dir_counts[top] = dir_counts.get(top, 0) + 1
            else:
                root_files += 1

        parts = [
            f"文件 {len(self._files)} 个（上限 {self.max_files}），"
            f"顶层符号 {len(self._symbols)} 个"
        ]
        if self._build_truncated:
            parts.append("文件数达到上限，索引已截断")
        if dir_counts:
            main_dirs = sorted(dir_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
            parts.append("主要目录：" + "、".join(f"{name}({count})" for name, count in main_dirs))
        if root_files:
            parts.append(f"根目录文件 {root_files} 个")
        return "；".join(parts) + "。"

    # ------------------------------------------------------------------ search

    def search_files(self, fragment: str, limit: int = 50, offset: int = 0) -> SearchPage:
        needle = fragment.strip().lower()
        if not needle:
            raise ValueError("search fragment must not be empty")

        limit, offset = self._page_args(limit, offset)
        records, building = self._file_records()
        matched: list[str] = []
        for entry in records:
            if needle in entry["path"].lower():
                matched.append(entry["path"])
                if len(matched) >= offset + limit + 1:
                    break

        page = matched[offset : offset + limit]
        truncated = len(matched) > offset + limit
        return SearchPage(
            lines=page,
            matches=len(page),
            files_scanned=len(records),
            truncated=truncated,
            next_offset=(offset + limit) if truncated else None,
            index_building=building,
        )

    def find_symbols(self, name: str, limit: int = 50, offset: int = 0) -> SearchPage:
        needle = name.strip().lower()
        if not needle:
            raise ValueError("symbol name must not be empty")

        limit, offset = self._page_args(limit, offset)
        if self.ready:
            symbols = self._symbols
            files_scanned = len(self._files)
            building = False
        else:
            symbols, files_scanned = self._live_symbols()
            building = True

        matched: list[dict[str, Any]] = []
        for symbol in symbols:
            if needle in str(symbol["name"]).lower():
                matched.append(symbol)
                if len(matched) >= offset + limit + 1:
                    break

        page = matched[offset : offset + limit]
        lines = [
            f"{symbol['path']}:{symbol['line']}: {symbol['name']} ({symbol['kind']})"
            for symbol in page
        ]
        truncated = len(matched) > offset + limit
        return SearchPage(
            lines=lines,
            matches=len(lines),
            files_scanned=files_scanned,
            truncated=truncated,
            next_offset=(offset + limit) if truncated else None,
            index_building=building,
        )

    def search_text(
        self,
        query: str,
        path: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> SearchPage:
        if not query:
            raise ValueError("search query must not be empty")

        relative = "" if path.strip() in ("", ".") else validate_workspace_path(path)
        limit, offset = self._page_args(limit, offset)

        records, building = self._file_records()
        candidates = self._filter_by_path(records, relative)
        matcher = _build_matcher(query)
        matched: list[str] = []
        files_scanned = 0

        for entry in candidates:
            if entry["size"] > MAX_SYMBOL_FILE_BYTES:
                continue
            files_scanned += 1
            try:
                content = self.sandbox.read_file(self.workspace_path, entry["path"])
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                continue

            for line_number, line in enumerate(content.splitlines(), start=1):
                if not matcher(line):
                    continue
                matched.append(f"{entry['path']}:{line_number}: {line}")
                if len(matched) >= offset + limit + 1:
                    break
            if len(matched) >= offset + limit + 1:
                break

        page = matched[offset : offset + limit]
        truncated = len(matched) > offset + limit
        return SearchPage(
            lines=page,
            matches=len(page),
            files_scanned=files_scanned,
            truncated=truncated,
            next_offset=(offset + limit) if truncated else None,
            index_building=building,
        )

    # ---------------------------------------------------------------- internal

    def _page_args(self, limit: int, offset: int) -> tuple[int, int]:
        try:
            effective_limit = max(1, min(int(limit), self.max_results))
        except (TypeError, ValueError):
            effective_limit = self.max_results
        try:
            effective_offset = max(0, int(offset))
        except (TypeError, ValueError):
            effective_offset = 0
        return effective_limit, effective_offset

    def _filter_by_path(
        self, records: list[dict[str, Any]], relative: str
    ) -> list[dict[str, Any]]:
        if not relative:
            return records
        if any(entry["path"] == relative for entry in records):
            return [entry for entry in records if entry["path"] == relative]
        prefix = relative + "/"
        return [entry for entry in records if entry["path"].startswith(prefix)]

    def _file_records(self) -> tuple[list[dict[str, Any]], bool]:
        """已就绪时返回索引内的文件清单；否则实时扫描并标记 building。"""
        if self.ready:
            return list(self._files), False

        records: list[dict[str, Any]] = []

        def walk(directory: str) -> None:
            if len(records) >= self.max_files:
                return
            try:
                entries = self.sandbox.list_files(self.workspace_path, directory)
            except (FileNotFoundError, ValueError, OSError):
                return
            for entry in entries:
                if entry["type"] == "dir":
                    if entry["name"].lower() in SKIP_DIR_NAMES:
                        continue
                    walk(entry["path"])
                    if len(records) >= self.max_files:
                        return
                else:
                    records.append({"path": entry["path"], "size": int(entry.get("size") or 0)})
                    if len(records) >= self.max_files:
                        return

        walk("")
        return records, True

    def _live_symbols(self) -> tuple[list[dict[str, Any]], int]:
        records, _ = self._file_records()
        candidates = [
            entry
            for entry in records
            if _is_indexable(entry["path"]) and entry["size"] <= MAX_SYMBOL_FILE_BYTES
        ]
        return self._load_symbols(candidates), len(candidates)

    def _load_symbols(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """并行读取索引文件并提取符号（IO 密集，线程池可显著缩短构建时间）。"""
        if not candidates:
            return []

        def load(entry: dict[str, Any]) -> list[dict[str, Any]]:
            try:
                content = self.sandbox.read_file(self.workspace_path, entry["path"])
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                return []
            return _extract_symbols(entry["path"], content)

        workers = min(16, (os.cpu_count() or 4) * 2)
        symbols: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for chunk in pool.map(load, candidates):
                symbols.extend(chunk)
        symbols.sort(key=lambda symbol: (symbol["path"], symbol["line"]))
        return symbols

    def _scan(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        files: list[dict[str, Any]] = []
        truncated = False

        def walk(directory: str) -> None:
            nonlocal truncated
            if truncated:
                return
            try:
                entries = self.sandbox.list_files(self.workspace_path, directory)
            except (FileNotFoundError, ValueError, OSError):
                return

            for entry in entries:
                if entry["type"] == "dir":
                    if entry["name"].lower() in SKIP_DIR_NAMES:
                        continue
                    walk(entry["path"])
                    if truncated:
                        return
                    continue

                if len(files) >= self.max_files:
                    truncated = True
                    return

                files.append({"path": entry["path"], "size": int(entry.get("size") or 0)})

        walk("")

        candidates = [
            entry
            for entry in files
            if _is_indexable(entry["path"]) and entry["size"] <= MAX_SYMBOL_FILE_BYTES
        ]
        return files, self._load_symbols(candidates), truncated


def _build_matcher(query: str):
    try:
        pattern = re.compile(query)
        return lambda line: pattern.search(line) is not None
    except re.error:
        return lambda line: query in line
