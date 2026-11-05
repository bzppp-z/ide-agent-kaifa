"""Phase 3 Task 3.2：sessions CRUD + files 路由测试。"""

import uuid

import httpx
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def api(db_setup):
    """带 lifespan 的 ASGI 客户端（SQLite 表已建好）。"""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def _create_session(api, demo_repo: str = "todo-app") -> dict:
    response = await api.post("/sessions", json={"demo_repo": demo_repo})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ sessions


async def test_create_session_returns_token_and_not_ready(api):
    body = await _create_session(api)

    assert uuid.UUID(body["id"])
    assert body["token"]
    assert body["demo_repo"] == "todo-app"
    assert body["container_ready"] is False
    assert body["created_at"]

    detail = await api.get(f"/sessions/{body['id']}", headers=_auth(body["token"]))
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["id"] == body["id"]
    assert payload["messages"] == []
    assert payload["container_ready"] is False


async def test_create_session_rejects_unknown_demo_repo(api):
    response = await api.post("/sessions", json={"demo_repo": "nope-repo"})

    assert response.status_code == 403


async def test_create_session_validates_body(api):
    response = await api.post("/sessions", json={"demo_repo": ""})

    assert response.status_code == 422


async def test_get_session_requires_jwt(api):
    body = await _create_session(api)

    for headers in ({}, {"Authorization": "Bearer garbage"}):
        response = await api.get(f"/sessions/{body['id']}", headers=headers)
        assert response.status_code == 401, headers


async def test_get_session_rejects_other_sessions_token(api):
    first = await _create_session(api)
    second = await _create_session(api)

    response = await api.get(f"/sessions/{first['id']}", headers=_auth(second["token"]))

    assert response.status_code == 403


async def test_get_missing_session_returns_404(api):
    token_session = await _create_session(api)
    from app.services.auth import create_session_token
    from app.core.config import get_settings

    missing_id = str(uuid.uuid4())
    token = create_session_token(missing_id, get_settings().jwt_secret)

    response = await api.get(f"/sessions/{missing_id}", headers=_auth(token))

    assert response.status_code == 404


async def test_delete_session_removes_workspace_but_keeps_row(api):
    body = await _create_session(api)
    session_id = body["id"]

    # 访问 files 触发容器懒初始化
    await api.get(f"/files/{session_id}/", headers=_auth(body["token"]))

    response = await api.delete(f"/sessions/{session_id}", headers=_auth(body["token"]))
    assert response.status_code == 200
    assert response.json()["ok"] is True

    detail = await api.get(f"/sessions/{session_id}", headers=_auth(body["token"]))
    assert detail.status_code == 200
    assert detail.json()["container_ready"] is False

    from app.main import app

    assert not (app.state.sandbox.root_dir / f"session-{session_id}").exists()


async def test_delete_session_requires_jwt(api):
    body = await _create_session(api)

    response = await api.delete(f"/sessions/{body['id']}")

    assert response.status_code == 401


# --------------------------------------------------------------------- files


async def test_list_workspace_root_directory(api):
    body = await _create_session(api)

    response = await api.get(f"/files/{body['id']}/", headers=_auth(body["token"]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "dir"
    assert payload["truncated"] is False
    names = {entry["name"] for entry in payload["entries"]}
    assert {"package.json", "README.md", "src"} <= names


async def test_list_subdirectory(api):
    body = await _create_session(api)

    response = await api.get(f"/files/{body['id']}/src", headers=_auth(body["token"]))

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert {entry["name"] for entry in entries} == {"index.js", "check.js"}


async def test_read_file_content(api):
    body = await _create_session(api)

    response = await api.get(f"/files/{body['id']}/src/index.js", headers=_auth(body["token"]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "file"
    assert payload["truncated"] is False
    assert "addTodo" in payload["content"]


async def test_read_large_file_is_truncated_to_2000_lines(api):
    body = await _create_session(api)
    content = "".join(f"line {index}\n" for index in range(2500))
    write = await api.post(
        f"/files/{body['id']}/big.txt", json={"content": content}, headers=_auth(body["token"])
    )
    assert write.status_code == 200

    response = await api.get(f"/files/{body['id']}/big.txt", headers=_auth(body["token"]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["truncated"] is True
    assert len(payload["content"].splitlines()) == 2000


async def test_write_file_then_read_back(api):
    body = await _create_session(api)

    write = await api.post(
        f"/files/{body['id']}/notes/todo.md",
        json={"content": "# 待办\n- 写测试\n"},
        headers=_auth(body["token"]),
    )

    assert write.status_code == 200
    assert write.json() == {"ok": True, "path": "notes/todo.md"}

    read = await api.get(f"/files/{body['id']}/notes/todo.md", headers=_auth(body["token"]))
    assert read.status_code == 200
    assert read.json()["content"] == "# 待办\n- 写测试\n"


async def test_missing_file_returns_404(api):
    body = await _create_session(api)

    response = await api.get(f"/files/{body['id']}/missing.txt", headers=_auth(body["token"]))

    assert response.status_code == 404


async def test_path_traversal_is_rejected(api):
    body = await _create_session(api)

    responses = [
        await api.get(f"/files/{body['id']}/%2E%2E%2Fsecret.txt", headers=_auth(body["token"])),
        await api.post(
            f"/files/{body['id']}/%2E%2E%2Fsecret.txt",
            json={"content": "x"},
            headers=_auth(body["token"]),
        ),
        await api.get(f"/files/{body['id']}/C:Windows", headers=_auth(body["token"])),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]


async def test_files_require_jwt_and_ownership(api):
    first = await _create_session(api)
    second = await _create_session(api)

    assert (await api.get(f"/files/{first['id']}/")).status_code == 401
    assert (
        await api.get(f"/files/{first['id']}/", headers=_auth(second["token"]))
    ).status_code == 403


async def test_files_missing_session_returns_404(api):
    from app.core.config import get_settings
    from app.services.auth import create_session_token

    missing_id = str(uuid.uuid4())
    token = create_session_token(missing_id, get_settings().jwt_secret)

    response = await api.get(f"/files/{missing_id}/", headers=_auth(token))

    assert response.status_code == 404
