"""Phase 3 Task 3.1：Pydantic schemas + JWT dependency + 全局异常处理测试。"""

import uuid
from datetime import datetime

import httpx
import pytest
from fastapi import Depends, FastAPI
from pydantic import ValidationError

from app.services.auth import create_session_token


def _settings():
    from app.core.config import get_settings

    return get_settings()


# ------------------------------------------------------------------- schemas


def test_session_schemas_serialize_uuid_and_datetime():
    from app.schemas.message import MessageResponse
    from app.schemas.session import SessionCreate, SessionCreatedResponse, SessionDetailResponse

    session_id = uuid.uuid4()
    created_at = datetime(2026, 11, 5, 8, 0, 0)

    body = SessionCreate(demo_repo="todo-app")
    assert body.demo_repo == "todo-app"
    with pytest.raises(ValidationError):
        SessionCreate(demo_repo="")

    created = SessionCreatedResponse(
        id=str(session_id),
        demo_repo="todo-app",
        container_ready=False,
        created_at=created_at,
        token="jwt-token",
    )
    assert isinstance(created.id, uuid.UUID)
    payload = created.model_dump(mode="json")
    assert payload == {
        "id": str(session_id),
        "demo_repo": "todo-app",
        "container_ready": False,
        "created_at": "2026-11-05T08:00:00",
        "token": "jwt-token",
    }

    message = MessageResponse(
        id=str(uuid.uuid4()), role="user", content="你好", created_at=created_at
    )
    detail = SessionDetailResponse(
        id=str(session_id),
        demo_repo="todo-app",
        container_ready=True,
        created_at=created_at,
        messages=[message],
    )
    assert detail.messages[0].role == "user"
    assert detail.model_dump(mode="json")["messages"][0]["content"] == "你好"


def test_agent_and_file_schemas():
    from app.schemas.agent import AgentRunRequest, AgentRunResponse
    from app.schemas.file import FileContentResponse, FileWriteRequest

    assert AgentRunRequest(message="帮我看看 src 目录").message == "帮我看看 src 目录"
    with pytest.raises(ValidationError):
        AgentRunRequest(message="")
    assert AgentRunResponse(run_id="run-1").model_dump() == {"run_id": "run-1"}
    assert FileWriteRequest(content="new").content == "new"

    dir_payload = FileContentResponse(type="dir", entries=[{"name": "a.txt"}])
    assert dir_payload.content is None
    assert dir_payload.truncated is False

    file_payload = FileContentResponse(type="file", content="hello", truncated=True)
    assert file_payload.entries is None
    dumped = file_payload.model_dump(mode="json")
    assert dumped == {"type": "file", "content": "hello", "entries": None, "truncated": True}


def test_cors_settings_include_local_dev_ports():
    origins = _settings().cors_origin_list

    assert "http://localhost:5173" in origins
    assert "http://localhost:5174" in origins


# -------------------------------------------------------------- JWT dependency


@pytest.fixture
def auth_app():
    from app.api.deps import get_current_session

    app = FastAPI()
    app.state.settings = _settings()

    @app.get("/probe")
    async def probe(session_id: str = Depends(get_current_session)) -> dict[str, str]:
        return {"session_id": session_id}

    return app


async def _get(app, headers: dict[str, str] | None = None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/probe", headers=headers or {})


async def test_jwt_dependency_rejects_missing_token(auth_app):
    response = await _get(auth_app)

    assert response.status_code == 401


async def test_jwt_dependency_rejects_invalid_and_malformed_tokens(auth_app):
    for headers in (
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Bearer "},
        {"Authorization": "Token abc"},
        {"Authorization": create_session_token(str(uuid.uuid4()), "other-secret")},
    ):
        response = await _get(auth_app, headers)
        assert response.status_code == 401, headers


async def test_jwt_dependency_returns_session_id(auth_app):
    session_id = str(uuid.uuid4())
    token = create_session_token(session_id, _settings().jwt_secret)

    response = await _get(auth_app, {"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"session_id": session_id}


# ---------------------------------------------------------- 全局异常 handler


def test_global_exception_handlers_registered():
    from app.main import app

    assert app.exception_handlers.get(ValueError) is not None
    assert app.exception_handlers.get(FileNotFoundError) is not None
    assert app.exception_handlers.get(RuntimeError) is not None
