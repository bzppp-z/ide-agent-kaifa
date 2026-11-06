"""会话 workspace 生命周期管理。

- ``ensure_container``：幂等创建会话 workspace，首次创建时写入 demo repo。
- ``run_idle_reclaim_loop``：定时扫描 ``last_active_at`` 超时的会话并清理 workspace。
"""

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import utcnow
from app.models.session import Session as SessionModel
from app.services.demo_repos import DEFAULT_DEMO_REPO, get_demo_repo
from app.services.repo_index import RepoIndex
from app.services.sandbox import SandboxService

logger = logging.getLogger(__name__)

INIT_MARKER = ".devflow-initialized"


class WorkspaceService:
    def __init__(
        self,
        sandbox: SandboxService,
        session_factory: async_sessionmaker | None = None,
        idle_timeout_min: int = 30,
        idle_interval_seconds: float = 60.0,
        repo_indexes: dict[str, RepoIndex] | None = None,
        repo_index_max_files: int = 10000,
        repo_index_max_results: int = 50,
    ) -> None:
        self.sandbox = sandbox
        self.session_factory = session_factory
        self.idle_timeout_min = idle_timeout_min
        self.idle_interval_seconds = idle_interval_seconds
        self.repo_indexes = repo_indexes if repo_indexes is not None else {}
        self.repo_index_max_files = repo_index_max_files
        self.repo_index_max_results = repo_index_max_results

    async def ensure_container(self, session_id: str, demo_repo: str | None = None) -> str:
        """幂等创建会话 workspace 并写入 demo repo，返回 workspace 绝对路径。"""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must not be empty")
        if "/" in session_id or "\\" in session_id:
            raise ValueError("session_id must not contain path separators")

        container_name = f"session-{session_id}"
        session_row: SessionModel | None = None
        if self.session_factory is not None:
            async with self.session_factory() as db:
                session_row = await db.get(SessionModel, session_id)

        # 用户真实目录会话：直接复用该目录，不复制 demo、不写标记、不建子目录
        if session_row is not None and session_row.local_path:
            local_dir = Path(session_row.local_path)
            if not local_dir.exists() or not local_dir.is_dir():
                raise ValueError(f"local workspace does not exist: {session_row.local_path}")
            local_dir = self.sandbox.allow_workspace(local_dir)
            if self.session_factory is not None:
                async with self.session_factory() as db:
                    row = await db.get(SessionModel, session_id)
                    if row is not None:
                        row.container_id = str(local_dir)
                        row.last_active_at = utcnow()
                        await db.commit()
            self._ensure_repo_index(session_id, str(local_dir))
            return str(local_dir)

        existing_container_id = session_row.container_id if session_row else None
        if existing_container_id:
            container_id = self.sandbox.create_session_container(
                container_name, workspace_path=existing_container_id
            )
        else:
            container_id = self.sandbox.create_session_container(container_name)

        if demo_repo is None:
            demo_repo = session_row.demo_repo if session_row else DEFAULT_DEMO_REPO

        if not self._is_initialized(container_id):
            for relative_path, content in get_demo_repo(demo_repo).items():
                self.sandbox.write_file(container_id, relative_path, content)
            self.sandbox.write_file(container_id, INIT_MARKER, "initialized\n")

        if session_row is not None and self.session_factory is not None:
            async with self.session_factory() as db:
                row = await db.get(SessionModel, session_id)
                if row is not None:
                    row.container_id = container_id
                    row.last_active_at = utcnow()
                    await db.commit()

        self._ensure_repo_index(session_id, container_id)
        return container_id

    def _ensure_repo_index(self, session_id: str, workspace_path: str) -> None:
        """按需注册并后台构建项目索引；索引已就绪时直接复用。"""
        index = self.repo_indexes.get(session_id)
        if index is None or index.workspace_path != workspace_path:
            index = RepoIndex(
                self.sandbox,
                workspace_path,
                max_files=self.repo_index_max_files,
                max_results=self.repo_index_max_results,
            )
            self.repo_indexes[session_id] = index
        index.schedule_build()

    def _is_initialized(self, container_id: str) -> bool:
        try:
            self.sandbox.read_file(container_id, INIT_MARKER)
            return True
        except FileNotFoundError:
            return False

    async def reclaim_idle_sessions(self) -> list[str]:
        """清理 last_active_at 超过 idle 阈值的会话 workspace，返回被回收的 session id。"""
        if self.session_factory is None:
            return []

        cutoff = utcnow() - timedelta(minutes=self.idle_timeout_min)
        reclaimed: list[str] = []
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    select(SessionModel).where(
                        SessionModel.container_id.is_not(None),
                        SessionModel.local_path.is_(None),
                        SessionModel.last_active_at < cutoff,
                    )
                )
            ).scalars().all()

            for row in rows:
                try:
                    await self.sandbox.stop_and_remove(row.container_id)
                except Exception:
                    # 单条清理失败不能中断整轮回收；保留 container_id 等下一轮重试
                    logger.exception("failed to reclaim workspace: %s", row.container_id)
                    continue
                row.container_id = None
                reclaimed.append(row.id)

            if reclaimed:
                await db.commit()

        for session_id in reclaimed:
            index = self.repo_indexes.pop(session_id, None)
            if index is not None:
                index.cancel_build()

        return reclaimed

    async def run_idle_reclaim_loop(self, stop_event: asyncio.Event | None = None) -> None:
        """后台 idle 回收循环，由 FastAPI lifespan 启动，shutdown 时 cancel。"""
        while True:
            try:
                await self.reclaim_idle_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - 防御性日志，避免任务静默退出
                logger.exception("idle reclaim failed")

            if stop_event is not None and stop_event.is_set():
                return

            try:
                if stop_event is not None:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.idle_interval_seconds)
                    return
                await asyncio.sleep(self.idle_interval_seconds)
            except asyncio.TimeoutError:
                continue
