"""会话检查点服务。

每次 agent run 开始创建一个只含元数据的检查点；编辑类工具在真正落盘前
惰性调用 ``capture_file`` 保存被改文件的原内容（首次捕获优先）。
快照目录固定在 workspace 根下的 ``.checkpoints/<session_id>/``，
不会写入用户本地目录，也不会在回滚时整目录删除。
"""

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.security import validate_workspace_path
from app.db import utcnow
from app.services.sandbox import SandboxService


class CheckpointService:
    def __init__(
        self,
        root_dir: str | Path,
        sandbox: SandboxService | None = None,
        max_checkpoints: int = 50,
    ) -> None:
        base = Path(root_dir).expanduser().resolve()
        self.root_dir = base / ".checkpoints"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox = sandbox
        self.max_checkpoints = max(1, int(max_checkpoints))

    # ------------------------------------------------------------------ public

    def create(self, session_id: str, user_message: str) -> dict[str, Any]:
        """新建检查点元数据；超出上限时淘汰最旧并删除其快照目录。"""
        directory = self._session_dir(session_id)
        index = self._read_index(session_id)
        entries: list[dict[str, Any]] = list(index.get("checkpoints") or [])

        while len(entries) >= self.max_checkpoints:
            oldest = entries.pop(0)
            self._remove_checkpoint_dir(session_id, str(oldest.get("id") or ""))

        checkpoint = {
            "id": str(uuid4()),
            "session_id": session_id,
            "created_at": utcnow().isoformat(),
            "user_message": user_message,
            "file_count": 0,
            "files": {},
        }
        entries.append(checkpoint)
        index["checkpoints"] = entries
        self._write_index(session_id, index)
        directory.mkdir(parents=True, exist_ok=True)
        return self._public(checkpoint)

    def capture_file(
        self,
        session_id: str,
        checkpoint_id: str,
        workspace_path: str,
        relative_path: str,
        container_id: str,
    ) -> None:
        """首次捕获文件原内容；同一检查点内重复写入不覆盖第一次的快照。"""
        relative = validate_workspace_path(relative_path)
        index = self._read_index(session_id)
        checkpoint = self._find(index, checkpoint_id)
        if checkpoint is None:
            return

        files: dict[str, dict[str, Any]] = checkpoint.setdefault("files", {})
        if relative in files:
            return

        try:
            content = self._read_original(container_id, workspace_path, relative)
        except FileNotFoundError:
            files[relative] = {"exists": False}
        except UnicodeDecodeError:
            # 二进制文件无法以文本快照恢复；记录存在但不写快照，回滚时跳过。
            files[relative] = {"exists": True, "snapshot": None, "binary": True}
        else:
            name = hashlib.sha256(relative.encode("utf-8")).hexdigest() + ".txt"
            snapshot_dir = self._session_dir(session_id) / checkpoint_id / "files"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / name).write_text(content, encoding="utf-8")
            files[relative] = {"exists": True, "snapshot": name}

        checkpoint["file_count"] = len(files)
        self._write_index(session_id, index)

    def list(self, session_id: str) -> list[dict[str, Any]]:
        """按创建时间倒序返回最多 max_checkpoints 条公开元数据。"""
        entries = list(self._read_index(session_id).get("checkpoints") or [])
        entries.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return [self._public(entry) for entry in entries[: self.max_checkpoints]]

    def rollback(
        self,
        session_id: str,
        checkpoint_id: str,
        sandbox: SandboxService,
        container_id: str | None = None,
    ) -> int:
        """逐文件恢复检查点快照，返回恢复文件数；不删除整个目录。"""
        index = self._read_index(session_id)
        checkpoint = self._find(index, checkpoint_id)
        if checkpoint is None:
            raise ValueError("checkpoint not found")

        workspace = container_id or str(self.root_dir.parent / f"session-{session_id}")
        restored = 0
        for relative, meta in (checkpoint.get("files") or {}).items():
            safe_relative = validate_workspace_path(relative)
            if meta.get("exists"):
                snapshot = meta.get("snapshot")
                if not snapshot:
                    continue  # 二进制文件无快照，跳过而不是误删
                snapshot_path = self._session_dir(session_id) / checkpoint_id / "files" / str(snapshot)
                if not snapshot_path.is_file():
                    continue
                sandbox.write_file(workspace, safe_relative, snapshot_path.read_text(encoding="utf-8"))
            else:
                try:
                    sandbox.delete_file(workspace, safe_relative)
                except FileNotFoundError:
                    pass
            restored += 1
        return restored

    def get(self, session_id: str, checkpoint_id: str) -> dict[str, Any] | None:
        checkpoint = self._find(self._read_index(session_id), checkpoint_id)
        return self._public(checkpoint) if checkpoint is not None else None

    def delete_session(self, session_id: str) -> None:
        """清理会话的全部检查点快照（幂等）。"""
        directory = self._session_dir(session_id)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    # ----------------------------------------------------------------- internal

    def _session_dir(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must not be empty")
        if "/" in session_id or "\\" in session_id or ".." in session_id:
            raise ValueError("session_id must not contain path separators")
        return self.root_dir / session_id

    def _index_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "index.json"

    def _read_index(self, session_id: str) -> dict[str, Any]:
        path = self._index_path(session_id)
        if not path.is_file():
            return {"checkpoints": []}
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"checkpoints": []}
        if not isinstance(parsed, dict):
            return {"checkpoints": []}
        checkpoint_list = parsed.get("checkpoints")
        if not isinstance(checkpoint_list, list):
            parsed["checkpoints"] = []
        return parsed

    def _write_index(self, session_id: str, index: dict[str, Any]) -> None:
        path = self._index_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _read_original(self, container_id: str, workspace_path: str, relative: str) -> str:
        if self.sandbox is not None:
            return self.sandbox.read_file(container_id, relative)
        return (Path(workspace_path) / relative).read_text(encoding="utf-8")

    def _find(self, index: dict[str, Any], checkpoint_id: str) -> dict[str, Any] | None:
        for entry in index.get("checkpoints") or []:
            if entry.get("id") == checkpoint_id:
                return entry
        return None

    def _remove_checkpoint_dir(self, session_id: str, checkpoint_id: str) -> None:
        if not checkpoint_id:
            return
        directory = self._session_dir(session_id) / checkpoint_id
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    @staticmethod
    def _public(checkpoint: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": checkpoint.get("id"),
            "session_id": checkpoint.get("session_id"),
            "created_at": checkpoint.get("created_at"),
            "user_message": checkpoint.get("user_message") or "",
            "file_count": int(checkpoint.get("file_count") or 0),
        }


def checkpoint_datetime(checkpoint: dict[str, Any]) -> datetime:
    """把检查点的 ISO 时间串解析为 naive UTC datetime，供消息/变更记录比较。"""
    value = checkpoint.get("created_at")
    if not isinstance(value, str):
        raise ValueError("checkpoint created_at is missing")
    return datetime.fromisoformat(value)
