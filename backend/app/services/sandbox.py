"""本地模式沙箱服务。

公开方法签名与未来的 Docker 版 ContainerService 完全一致：

- ``create_session_container(container_name, workspace_path=None) -> str``
- ``async exec(container_id, command, timeout=30) -> tuple[int, str]``
- ``async stop_and_remove(container_id) -> None``
- ``write_file(container_id, relative_path, content) -> None``
- ``read_file(container_id, relative_path) -> str``
- ``list_files(container_id, relative_path="") -> list[dict]``

本地实现中 ``container_id`` 实际是会话 workspace 的绝对路径，语义与 Docker 的
container id 保持一致：调用方只把它当作不透明句柄在以上方法之间传递。

**安全边界：** 本地模式命令直接在宿主机执行（隔离性弱于容器）。所有 exec 的
cwd 固定为会话 workspace，所有文件路径必须经过 ``core.security.validate_workspace_path``，
container_id / workspace_path 必须落在 ``WORKSPACE_ROOT_DIR`` 内。

**Docker 模式（磁后恢复）资源限制参数（保留在 config 中）：**
``--cpus=1 --memory=1g --pids-limit=256 --cap-drop=ALL
--security-opt=no-new-privileges --user=node``。

生产部署必须切回 Docker 模式。
"""

import asyncio
import contextlib
import shutil
import sys
from pathlib import Path
from typing import Any

from app.core.security import validate_workspace_path

MAX_EXEC_TIMEOUT_SECONDS = 30.0


class SandboxService:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ sessions

    def create_session_container(self, container_name: str, workspace_path: str | None = None) -> str:
        """创建（幂等）会话工作目录，返回 workspace 绝对路径（等价 Docker container id）。"""
        if not isinstance(container_name, str) or not container_name.strip():
            raise ValueError("container_name must not be empty")

        if workspace_path is None:
            workspace = self.root_dir / container_name
        else:
            workspace = Path(workspace_path)

        resolved = workspace.expanduser().resolve()
        self._assert_inside_root(resolved)
        resolved.mkdir(parents=True, exist_ok=True)
        return str(resolved)

    async def exec(self, container_id: str, command: str, timeout: float = MAX_EXEC_TIMEOUT_SECONDS) -> tuple[int, str]:
        """在会话 workspace 内执行 shell 命令，返回 (exit_code, combined_output)。"""
        workspace = self._resolve_container(container_id)
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must not be empty")

        effective_timeout = min(float(timeout), MAX_EXEC_TIMEOUT_SECONDS)
        if effective_timeout <= 0:
            raise ValueError("timeout must be positive")

        process = await asyncio.create_subprocess_exec(
            *_shell_argv(command),
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
            return 124, f"[timeout] command timed out after {effective_timeout:g}s: {command}"

        output = (stdout or b"").decode("utf-8", errors="replace")
        return (process.returncode if process.returncode is not None else -1), output

    async def stop_and_remove(self, container_id: str) -> None:
        """清理会话 workspace（幂等）。"""
        workspace = self._resolve_container(container_id, must_exist=False)
        if workspace.exists():
            await asyncio.to_thread(shutil.rmtree, workspace, True)

    # --------------------------------------------------------------------- files

    def write_file(self, container_id: str, relative_path: str, content: str) -> None:
        workspace = self._resolve_container(container_id)
        target = workspace / validate_workspace_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_file(self, container_id: str, relative_path: str) -> str:
        workspace = self._resolve_container(container_id)
        target = workspace / validate_workspace_path(relative_path)
        return target.read_text(encoding="utf-8")

    def list_files(self, container_id: str, relative_path: str = "") -> list[dict[str, Any]]:
        workspace = self._resolve_container(container_id)
        if relative_path.strip():
            relative = validate_workspace_path(relative_path)
            base = workspace / relative
        else:
            relative = ""
            base = workspace

        if not base.exists():
            raise FileNotFoundError(f"path does not exist in workspace: {relative_path!r}")
        if not base.is_dir():
            raise ValueError(f"path is not a directory: {relative_path!r}")

        entries: list[dict[str, Any]] = []
        for entry in sorted(base.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if entry.is_dir():
                entry_type, size = "dir", 0
            else:
                entry_type, size = "file", entry.stat().st_size
            entries.append(
                {
                    "name": entry.name,
                    "path": f"{relative}/{entry.name}" if relative else entry.name,
                    "type": entry_type,
                    "size": size,
                }
            )
        return entries

    # ------------------------------------------------------------------ internal

    def _assert_inside_root(self, path: Path) -> None:
        if path == self.root_dir or self.root_dir not in path.parents:
            raise ValueError(f"path must stay inside the workspace root: {path}")

    def _resolve_container(self, container_id: str, must_exist: bool = True) -> Path:
        if not isinstance(container_id, str) or not container_id.strip():
            raise ValueError("container_id must not be empty")

        path = Path(container_id).expanduser()
        if not path.is_absolute():
            raise ValueError(f"container_id must be an absolute workspace path: {container_id!r}")

        resolved = path.resolve()
        self._assert_inside_root(resolved)
        if must_exist and not resolved.exists():
            raise ValueError(f"workspace does not exist: {container_id!r}")
        return resolved


def _shell_argv(command: str) -> list[str]:
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-c", command]
