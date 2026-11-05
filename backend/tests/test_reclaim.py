"""Phase 5 Task 5.3：idle workspace 回收 + lifespan 后台任务。

覆盖：过期的会话 workspace 被删除且 container_id 清空；活跃会话不受影响；
单条回收抛错不中断整体回收（且不误清 container_id）；lifespan 启停 idle task。
"""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncio

import pytest

from app.db import SessionLocal, utcnow
from app.models.session import Session as SessionModel
from app.services.workspace import WorkspaceService

IDLE_TIMEOUT_MIN = 30


async def _make_session(
    sandbox, *, minutes_ago: int, write_file: bool = True
) -> tuple[str, str]:
    session_id = str(uuid4())
    container_id = sandbox.create_session_container(f"session-{session_id}")
    if write_file:
        sandbox.write_file(container_id, "src/index.js", "console.log('hi');\n")

    async with SessionLocal() as db:
        db.add(
            SessionModel(
                id=session_id,
                demo_repo="todo-app",
                container_id=container_id,
                created_at=utcnow() - timedelta(minutes=minutes_ago),
                last_active_at=utcnow() - timedelta(minutes=minutes_ago),
            )
        )
        await db.commit()
    return session_id, container_id


def _service(sandbox) -> WorkspaceService:
    return WorkspaceService(
        sandbox,
        session_factory=SessionLocal,
        idle_timeout_min=IDLE_TIMEOUT_MIN,
    )


@pytest.mark.usefixtures("db_setup")
async def test_reclaim_removes_workspace_and_clears_container_id(sandbox):
    session_id, container_id = await _make_session(sandbox, minutes_ago=120)
    assert Path(container_id).is_dir()

    reclaimed = await _service(sandbox).reclaim_idle_sessions()

    assert reclaimed == [session_id]
    assert not Path(container_id).exists()

    async with SessionLocal() as db:
        row = await db.get(SessionModel, session_id)
        assert row is not None
        assert row.container_id is None


@pytest.mark.usefixtures("db_setup")
async def test_reclaim_keeps_recent_sessions(sandbox):
    recent_id, recent_container = await _make_session(sandbox, minutes_ago=0)
    stale_id, stale_container = await _make_session(sandbox, minutes_ago=120)

    reclaimed = await _service(sandbox).reclaim_idle_sessions()

    assert reclaimed == [stale_id]
    assert Path(recent_container).is_dir()
    assert not Path(stale_container).exists()

    async with SessionLocal() as db:
        recent = await db.get(SessionModel, recent_id)
        assert recent.container_id == recent_container


@pytest.mark.usefixtures("db_setup")
async def test_reclaim_continues_when_one_cleanup_raises(sandbox, monkeypatch):
    broken_id, broken_container = await _make_session(sandbox, minutes_ago=180)
    ok_id, ok_container = await _make_session(sandbox, minutes_ago=120)
    real_cleanup = sandbox.stop_and_remove

    async def flaky_cleanup(container_id: str) -> None:
        if container_id == broken_container:
            raise RuntimeError("cleanup boom")
        await real_cleanup(container_id)

    monkeypatch.setattr(sandbox, "stop_and_remove", flaky_cleanup)

    reclaimed = await _service(sandbox).reclaim_idle_sessions()

    # 抛错的那条不中断循环，也不误清 container_id（保留待下次重试）
    assert reclaimed == [ok_id]
    assert not Path(ok_container).exists()
    assert Path(broken_container).is_dir()

    async with SessionLocal() as db:
        row = await db.get(SessionModel, broken_id)
        assert row.container_id == broken_container


async def test_lifespan_starts_and_cancels_idle_reclaim_task():
    from app.main import app

    async with app.router.lifespan_context(app):
        task = app.state.idle_reclaim_task
        assert task is not None
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert task.cancelled()
