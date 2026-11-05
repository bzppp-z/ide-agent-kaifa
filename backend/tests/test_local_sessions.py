"""本地真实目录工作区：创建会话、目录浏览、回收/删除安全与 SQLite 迁移。

覆盖：
- POST /sessions 携带 local_path → demo_repo="local"、不复制 demo、GET 返回 local_path
- local_path 校验失败（不存在 / 相对路径 / 保护目录）→ 400
- ensure_container 直接返回用户目录；目录缺失抛 ValueError
- idle 回收跳过本地目录会话；DELETE 不删除用户目录
- GET /local/home、GET /local/browse 的正常与 404 行为
- init_db 对已有旧表补 local_path 列的轻量迁移且幂等
"""

import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import SessionLocal, utcnow
from app.models.session import Session as SessionModel
from app.services.workspace import WorkspaceService


@pytest_asyncio.fixture
async def api(db_setup):
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _insert_session(
    *,
    session_id: str,
    demo_repo: str = "todo-app",
    local_path: str | None = None,
    container_id: str | None = None,
    minutes_ago: int = 0,
) -> None:
    async with SessionLocal() as db:
        db.add(
            SessionModel(
                id=session_id,
                demo_repo=demo_repo,
                local_path=local_path,
                container_id=container_id,
                created_at=utcnow() - timedelta(minutes=minutes_ago),
                last_active_at=utcnow() - timedelta(minutes=minutes_ago),
            )
        )
        await db.commit()


# ---------------------------------------------------------------- create session


async def test_create_session_with_local_path_uses_user_directory(api, tmp_path):
    project = tmp_path / "real-project"
    project.mkdir()
    (project / "notes.txt").write_text("hello local\n", encoding="utf-8")

    response = await api.post("/sessions", json={"local_path": str(project)})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["demo_repo"] == "local"

    files = await api.get(f"/files/{payload['id']}/", headers=_auth(payload["token"]))
    assert files.status_code == 200, files.text
    names = {entry["name"] for entry in files.json()["entries"]}
    assert "notes.txt" in names

    detail = await api.get(f"/sessions/{payload['id']}", headers=_auth(payload["token"]))
    assert detail.status_code == 200, detail.text
    assert detail.json()["local_path"] == str(project.resolve())

    # 绝不把 demo repo 写进用户目录
    assert not (project / "package.json").exists()
    assert not (project / ".devflow-initialized").exists()
    assert (project / "notes.txt").read_text(encoding="utf-8") == "hello local\n"


async def test_create_session_without_local_path_still_creates_demo(api):
    response = await api.post("/sessions", json={"demo_repo": "todo-app"})

    assert response.status_code == 200, response.text
    assert response.json()["demo_repo"] == "todo-app"


async def test_create_session_rejects_missing_local_path(api, tmp_path):
    missing = tmp_path / "does-not-exist"

    response = await api.post("/sessions", json={"local_path": str(missing)})

    assert response.status_code == 400, response.text
    assert "exist" in response.json()["detail"].lower()


async def test_create_session_rejects_relative_local_path(api):
    response = await api.post("/sessions", json={"local_path": "relative-dir"})

    assert response.status_code == 400, response.text
    assert "absolute" in response.json()["detail"].lower()


_PROTECTED_PATHS = (
    ["C:\\", "C:\\Windows", "C:\\Program Files", "C:\\ProgramData"]
    if sys.platform == "win32"
    else ["/", "/etc", "/usr", "/bin", "/System"]
)


@pytest.mark.parametrize("protected", _PROTECTED_PATHS)
async def test_create_session_rejects_protected_local_path(api, protected):
    response = await api.post("/sessions", json={"local_path": protected})

    assert response.status_code == 400, response.text


# --------------------------------------------------------------- ensure_container


async def test_ensure_container_returns_local_dir_without_copying(db_setup, sandbox, tmp_path):
    local_dir = tmp_path / "user-dir"
    local_dir.mkdir()
    (local_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
    session_id = "local-session-1"
    await _insert_session(
        session_id=session_id, demo_repo="local", local_path=str(local_dir.resolve())
    )

    service = WorkspaceService(sandbox, session_factory=SessionLocal)
    container = await service.ensure_container(session_id)

    assert container == str(local_dir.resolve())
    assert (local_dir / "keep.txt").exists()
    assert not (local_dir / "package.json").exists()
    assert not (local_dir / ".devflow-initialized").exists()

    async with SessionLocal() as db:
        row = await db.get(SessionModel, session_id)
        assert row.container_id == str(local_dir.resolve())
        assert row.local_path == str(local_dir.resolve())


async def test_ensure_container_raises_when_local_dir_missing(db_setup, sandbox, tmp_path):
    session_id = "local-session-missing"
    await _insert_session(
        session_id=session_id,
        demo_repo="local",
        local_path=str((tmp_path / "gone").resolve()),
    )

    service = WorkspaceService(sandbox, session_factory=SessionLocal)
    with pytest.raises(ValueError):
        await service.ensure_container(session_id)


# ---------------------------------------------------------------- reclaim / delete


async def test_reclaim_skips_local_sessions(db_setup, sandbox, tmp_path):
    local_dir = tmp_path / "mine"
    local_dir.mkdir()
    local_id = "local-reclaim"
    await _insert_session(
        session_id=local_id,
        demo_repo="local",
        local_path=str(local_dir.resolve()),
        container_id=str(local_dir.resolve()),
        minutes_ago=120,
    )

    demo_id = "demo-reclaim"
    demo_container = sandbox.create_session_container(f"session-{demo_id}")
    await _insert_session(
        session_id=demo_id,
        demo_repo="todo-app",
        container_id=demo_container,
        minutes_ago=120,
    )

    service = WorkspaceService(sandbox, session_factory=SessionLocal, idle_timeout_min=30)
    reclaimed = await service.reclaim_idle_sessions()

    assert reclaimed == [demo_id]
    assert local_dir.is_dir()
    assert not Path(demo_container).exists()

    async with SessionLocal() as db:
        row = await db.get(SessionModel, local_id)
        assert row.container_id == str(local_dir.resolve())


async def test_delete_local_session_keeps_user_directory(api, tmp_path):
    project = tmp_path / "keep-on-delete"
    project.mkdir()
    (project / "main.py").write_text("print('hi')\n", encoding="utf-8")

    created = await api.post("/sessions", json={"local_path": str(project)})
    assert created.status_code == 200, created.text
    session = created.json()
    headers = _auth(session["token"])

    files = await api.get(f"/files/{session['id']}/", headers=headers)
    assert files.status_code == 200

    deleted = await api.delete(f"/sessions/{session['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    assert project.is_dir()
    assert (project / "main.py").exists()

    async with SessionLocal() as db:
        row = await db.get(SessionModel, session["id"])
        assert row is not None
        assert row.local_path == str(project.resolve())
        assert row.container_id is None


# ------------------------------------------------------------------ /local browse


async def test_local_home_returns_user_home(api):
    response = await api.get("/local/home")

    assert response.status_code == 200, response.text
    assert response.json()["path"] == str(Path.home().resolve())


async def test_local_browse_lists_only_directories(api, tmp_path):
    (tmp_path / "subdir").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    response = await api.get("/local/browse", params={"path": str(tmp_path)})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["path"] == str(tmp_path.resolve())
    assert payload["parent"] == str(tmp_path.resolve().parent)
    assert payload["entries"] == [
        {"name": "subdir", "path": str(tmp_path.resolve() / "subdir"), "type": "dir"}
    ]


async def test_local_browse_empty_path_returns_home_entries(api):
    response = await api.get("/local/browse")

    assert response.status_code == 200, response.text
    assert response.json()["path"] == str(Path.home().resolve())


async def test_local_browse_missing_path_returns_404(api, tmp_path):
    response = await api.get("/local/browse", params={"path": str(tmp_path / "missing")})

    assert response.status_code == 404, response.text


async def test_local_browse_file_path_returns_404(api, tmp_path):
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x", encoding="utf-8")

    response = await api.get("/local/browse", params={"path": str(file_path)})

    assert response.status_code == 404, response.text


# ----------------------------------------------------------------- init_db migration


async def test_init_db_migration_adds_local_path_column_idempotently(tmp_path, monkeypatch):
    import app.db as db_module

    legacy_db = tmp_path / "legacy.db"
    legacy_engine = create_async_engine("sqlite+aiosqlite:///" + legacy_db.as_posix())
    async with legacy_engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE sessions ("
            "id VARCHAR(36) PRIMARY KEY, "
            "demo_repo VARCHAR NOT NULL, "
            "container_id VARCHAR(512), "
            "created_at DATETIME, "
            "last_active_at DATETIME)"
        )

    monkeypatch.setattr(db_module, "engine", legacy_engine)

    await db_module.init_db()
    async with legacy_engine.begin() as conn:
        columns = {
            row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(sessions)")).fetchall()
        }
    assert "local_path" in columns

    # 幂等：第二次启动不报错，也不会重复加列
    await db_module.init_db()
    async with legacy_engine.begin() as conn:
        columns_again = {
            row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(sessions)")).fetchall()
        }
    assert columns_again == columns

    await legacy_engine.dispose()
