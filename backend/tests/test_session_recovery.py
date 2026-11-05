"""Phase 5 Task 5.2：GET /sessions/{id} 对失效 workspace 的自愈行为。

workspace 被 idle 回收/手动删除后，DB 里的 container_id 仍指向已不存在的目录；
GET session 应清空 container_id 并返回 container_ready=false，前端据此重建 sandbox。
"""

import httpx
import pytest_asyncio


@pytest_asyncio.fixture
async def api(db_setup):
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def _create_session(api) -> dict:
    response = await api.post("/sessions", json={"demo_repo": "todo-app"})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_get_session_clears_missing_container_id(api, sandbox):
    session = await _create_session(api)
    session_id = session["id"]

    # 先懒初始化 workspace，让 container_id 指向真实目录
    files = await api.get(f"/files/{session_id}/", headers=_auth(session["token"]))
    assert files.status_code == 200, files.text

    from app.db import SessionLocal
    from app.models.session import Session as SessionModel

    async with SessionLocal() as db:
        row = await db.get(SessionModel, session_id)
        assert row.container_id is not None
        container_id = row.container_id

    # 模拟 idle 回收：目录被删但 DB 残留 container_id
    assert sandbox.read_file(container_id, "src/index.js")
    await sandbox.stop_and_remove(container_id)

    detail = await api.get(f"/sessions/{session_id}", headers=_auth(session["token"]))
    assert detail.status_code == 200, detail.text
    assert detail.json()["container_ready"] is False

    async with SessionLocal() as db:
        row = await db.get(SessionModel, session_id)
        assert row.container_id is None


async def test_get_session_keeps_existing_container_id(api):
    session = await _create_session(api)
    session_id = session["id"]

    files = await api.get(f"/files/{session_id}/", headers=_auth(session["token"]))
    assert files.status_code == 200

    detail = await api.get(f"/sessions/{session_id}", headers=_auth(session["token"]))
    assert detail.status_code == 200
    assert detail.json()["container_ready"] is True
