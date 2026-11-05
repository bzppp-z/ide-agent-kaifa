"""Phase 3 Task 3.4：WebSocket 终端测试。

使用 fastapi.testclient.TestClient.websocket_connect；后端优先使用持久
PowerShell 子进程（stdin/stdout 管道）。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _create_session(client: TestClient) -> dict:
    response = client.post("/sessions", json={"demo_repo": "todo-app"})
    assert response.status_code == 200, response.text
    return response.json()


def _read_until(ws, needle: str, max_messages: int = 12) -> str:
    collected = ""
    for _ in range(max_messages):
        collected += ws.receive_text()
        if needle in collected:
            return collected
    raise AssertionError(f"did not receive {needle!r} in terminal output: {collected!r}")


async def test_terminal_ws_echoes_and_keeps_shell_state(db_setup):
    from app.main import app

    with TestClient(app) as client:
        session = _create_session(client)
        with client.websocket_connect(
            f"/terminal/ws/{session['id']}?token={session['token']}"
        ) as ws:
            ws.send_text("echo hello\n")
            _read_until(ws, "hello")

            # 持久 shell：上一条命令定义的变量在下一条命令里仍可用
            ws.send_text("$devflow = 41\n")
            ws.send_text("Write-Output ($devflow + 1)")
            _read_until(ws, "42")


async def test_terminal_ws_rejects_invalid_token(db_setup):
    from app.main import app

    with TestClient(app) as client:
        session = _create_session(client)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/terminal/ws/{session['id']}?token=not-a-jwt"
            ) as ws:
                ws.receive_text()


async def test_terminal_ws_rejects_other_sessions_token(db_setup):
    from app.main import app

    with TestClient(app) as client:
        first = _create_session(client)
        second = _create_session(client)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/terminal/ws/{first['id']}?token={second['token']}"
            ) as ws:
                ws.receive_text()


async def test_terminal_ws_rejects_missing_token(db_setup):
    from app.main import app

    with TestClient(app) as client:
        session = _create_session(client)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/terminal/ws/{session['id']}") as ws:
                ws.receive_text()


async def test_terminal_ws_unknown_session_returns_error(db_setup):
    from app.core.config import get_settings
    from app.main import app
    from app.services.auth import create_session_token

    with TestClient(app) as client:
        missing_id = str(uuid.uuid4())
        token = create_session_token(missing_id, get_settings().jwt_secret)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/terminal/ws/{missing_id}?token={token}") as ws:
                ws.receive_text()
