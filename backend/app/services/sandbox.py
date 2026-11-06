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
import hashlib
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.security import validate_workspace_path

MAX_EXEC_TIMEOUT_SECONDS = 30.0


class SandboxService:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        # 用户显式选择的真实目录（local_path 会话），允许在 root 之外操作
        self._external_workspaces: set[Path] = set()
        # 每个 workspace 一把命令锁：agent 命令与终端输入互斥。
        self.command_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ sessions

    def allow_workspace(self, path: str | Path) -> Path:
        """登记用户真实目录为合法 workspace（仅 local_path 会话使用）。"""
        resolved = Path(path).expanduser().resolve()
        self._external_workspaces.add(resolved)
        return resolved

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

    async def exec_stream(
        self,
        container_id: str,
        command: str,
        timeout: float,
        on_output: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> tuple[int, str]:
        """流式执行命令：逐行回传输出，同时累积完整结果。

        ``on_output(stream, chunk)`` 每读到一行就调用一次；``exec`` 的既有行为不变。
        执行期间持有该 workspace 的命令锁，终端输入会等命令结束后再写入。
        超时返回 ``(124, 已有输出 + 超时说明)``；被取消时 kill 子进程并继续抛出。
        """
        workspace = self._resolve_container(container_id)
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must not be empty")

        try:
            effective_timeout = float(timeout)
        except (TypeError, ValueError):
            raise ValueError("timeout must be a number") from None
        if effective_timeout <= 0:
            raise ValueError("timeout must be positive")

        async with self.command_lock(container_id):
            process = await asyncio.create_subprocess_exec(
                *_shell_argv(command),
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None  # PIPE 一定存在，防御性断言
            output_parts: list[str] = []

            async def read_lines() -> None:
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        return
                    chunk = line.decode("utf-8", errors="replace")
                    output_parts.append(chunk)
                    if on_output is not None:
                        await on_output("stdout", chunk)

            try:
                await asyncio.wait_for(read_lines(), timeout=effective_timeout)
                await process.wait()
                exit_code = process.returncode if process.returncode is not None else -1
            except asyncio.TimeoutError:
                exit_code = 124
                output_parts.append(
                    f"[timeout] command timed out after {effective_timeout:g}s: {command}"
                )
            except asyncio.CancelledError:
                raise
            finally:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(Exception):
                        await process.wait()

            return exit_code, "".join(output_parts)

    def command_lock(self, container_id: str) -> asyncio.Lock:
        """返回该 workspace 共享的命令锁（按绝对路径归一化后复用同一把）。"""
        key = str(self._resolve_container(container_id, must_exist=False))
        lock = self.command_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.command_locks[key] = lock
        return lock

    async def stop_and_remove(self, container_id: str) -> None:
        """清理会话 workspace（幂等）。用户真实目录永远不会被删除。"""
        workspace = self._resolve_container(container_id, must_exist=False)
        if self._is_external_workspace(workspace):
            return
        if workspace.exists():
            await asyncio.to_thread(shutil.rmtree, workspace, True)

    # --------------------------------------------------------------------- shell

    async def open_shell(
        self, container_id: str
    ) -> tuple[asyncio.subprocess.Process, asyncio.StreamReader, asyncio.StreamWriter]:
        """启动持久交互式 shell（cwd=会话 workspace），供 WebSocket 终端双向转发。

        本地模式：Windows 用 ``powershell -NoProfile -NoLogo -Command -``，
        其他平台用 ``/bin/sh -i``。stdin/stdout 为管道，stderr 合并到 stdout。
        Docker 模式恢复后，这里改为 ``exec_shell`` + hijack 并返回同等语义的
        (process, reader, writer) 适配对象。

        子进程生命周期由调用方负责 kill，不能泄漏。
        """
        workspace = self._resolve_container(container_id)
        try:
            process = await asyncio.create_subprocess_exec(
                *_interactive_shell_argv(),
                cwd=str(workspace),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to start interactive shell: {exc}") from exc

        if process.stdin is None or process.stdout is None:  # pragma: no cover - 防御性
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise RuntimeError("interactive shell pipes are unavailable")

        return process, process.stdout, process.stdin

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

    def delete_file(self, container_id: str, relative_path: str) -> None:
        """删除单个文件；目录、绝对路径与越界路径一律拒绝。"""
        workspace = self._resolve_container(container_id)
        target = workspace / validate_workspace_path(relative_path)
        if not target.exists():
            raise FileNotFoundError(f"file does not exist in workspace: {relative_path!r}")
        if target.is_dir():
            raise ValueError(f"path is a directory, refusing to delete: {relative_path!r}")
        target.unlink()

    def path_hash(self, container_id: str, relative_path: str) -> str | None:
        """返回文件内容的 sha256；文件不存在（或不是文件）时返回 None。"""
        workspace = self._resolve_container(container_id)
        target = workspace / validate_workspace_path(relative_path)
        if not target.is_file():
            return None
        return hashlib.sha256(target.read_bytes()).hexdigest()

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
                    "mtime": entry.stat().st_mtime,
                }
            )
        return entries

    # ------------------------------------------------------------------ internal

    def _is_external_workspace(self, path: Path) -> bool:
        return any(root == path or root in path.parents for root in self._external_workspaces)

    def _assert_inside_root(self, path: Path) -> None:
        if path == self.root_dir or self.root_dir not in path.parents:
            if not self._is_external_workspace(path):
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


def _interactive_shell_argv() -> list[str]:
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-NoLogo", "-Command", "-"]
    return ["/bin/sh", "-i"]
