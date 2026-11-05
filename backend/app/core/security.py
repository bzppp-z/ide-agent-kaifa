"""路径安全校验：后端唯一的路径安全防线。

所有文件读写、工具调用、文件 API 都必须把用户/模型传入的相对路径交给
``validate_workspace_path`` 校验后再拼接到会话 workspace 目录下。
"""

import re
from pathlib import PurePosixPath

# Windows 盘符路径（C:/、C:\ 已在替换反斜杠后统一为 C:/）
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


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
