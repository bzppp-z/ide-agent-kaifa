"""路径安全校验：后端唯一的路径安全防线。

所有文件读写、工具调用、文件 API 都必须把用户/模型传入的相对路径交给
``validate_workspace_path`` 校验后再拼接到会话 workspace 目录下。
"""

import os
import re
import sys
from pathlib import Path, PurePosixPath

# Windows 盘符路径（C:/、C:\ 已在替换反斜杠后统一为 C:/）
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")

# 用户真实目录会话拒绝系统保护目录（含其子目录）
_WINDOWS_PROTECTED_DIRS = (
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
)
_POSIX_PROTECTED_DIRS = ("/etc", "/usr", "/bin", "/System")


def validate_workspace_path(relative_path: str) -> str:
    """校验并规范化 workspace 内的相对路径。

    返回使用 ``/`` 分隔的规范化相对路径；任何绝对路径、父目录穿越、
    空路径或规范化后落在 workspace 根目录自身的路径都会抛出 ValueError。
    """
    if not isinstance(relative_path, str):
        raise ValueError("workspace path must be a string")

    candidate = relative_path.strip()
    if not candidate:
        raise ValueError("workspace path must not be empty")
    if "\x00" in candidate:
        raise ValueError("workspace path must not contain null bytes")

    normalized = candidate.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"absolute paths are not allowed: {relative_path!r}")
    if _DRIVE_PREFIX.match(normalized):
        raise ValueError(f"absolute paths are not allowed: {relative_path!r}")

    path = PurePosixPath(normalized)
    if any(part == ".." for part in path.parts):
        raise ValueError(f"parent directory traversal is not allowed: {relative_path!r}")
    if not path.parts or str(path) == ".":
        raise ValueError(f"path must point inside the workspace: {relative_path!r}")

    return str(path)


def _normalize_for_compare(path: str | Path) -> str:
    """跨平台可比较的绝对路径（Windows 下统一分隔符并忽略大小写）。"""
    return os.path.normcase(os.path.abspath(str(path)))


def _protected_local_dirs() -> list[str]:
    if sys.platform == "win32":
        protected = list(_WINDOWS_PROTECTED_DIRS)
        system_root = os.environ.get("SystemRoot")
        if system_root:
            protected.append(system_root)
        return protected
    return list(_POSIX_PROTECTED_DIRS)


def _is_protected_local_dir(resolved: Path) -> bool:
    target = _normalize_for_compare(resolved)
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(target)
        if tail.strip("\\/") == "":  # 任意盘符根，如 C:\
            return True
        candidates = _protected_local_dirs()
        separator = "\\"
    else:
        if target == "/":
            return True
        candidates = _protected_local_dirs()
        separator = "/"

    for candidate in candidates:
        protected = _normalize_for_compare(candidate)
        if target == protected or target.startswith(protected + separator):
            return True
    return False


def validate_local_workspace_path(raw_path: str) -> str:
    """校验用户选择的真实目录，返回 ``Path.resolve()`` 后的绝对路径。

    必须是绝对路径、存在且是目录，并拒绝系统保护目录；否则抛 ValueError。
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("local_path must not be empty")

    candidate = Path(raw_path.strip()).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"local_path must be an absolute path: {raw_path!r}")

    resolved = candidate.resolve()
    if not resolved.exists():
        raise ValueError(f"local_path does not exist: {raw_path!r}")
    if not resolved.is_dir():
        raise ValueError(f"local_path must be a directory: {raw_path!r}")
    if _is_protected_local_dir(resolved):
        raise ValueError(f"local_path points to a protected system directory: {resolved}")

    return str(resolved)
