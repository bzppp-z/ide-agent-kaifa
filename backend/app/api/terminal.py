"""WebSocket 终端端点。

``/terminal/ws/{session_id}?token=xxx``：accept → JWT 校验（token 里的 session
必须等于 path）→ 懒初始化 workspace → 启动持久 PowerShell 子进程 →
前端 ↔ FastAPI ↔ 子进程 stdin/stdout 双向转发。

前端发来的内容只作为 stdin 文本，不解析成 exec 参数；断开时 kill 子进程。
"""

import asyncio
import contextlib

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.auth import verify_session_token

router = APIRouter(tags=["terminal"])


def _app_state(websocket: WebSocket):
    return websocket.app.state


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.close(code=code, reason=reason)


@router.websocket("/terminal/ws/{session_id}")
async def terminal_ws(
    websocket: WebSocket, session_id: str, token: str | None = Query(default=None)
) -> None:
    await websocket.accept()

    state = _app_state(websocket)
    token_session = (
        verify_session_token(token, state.settings.jwt_secret) if token else None
    )
    if token_session is None or token_session != session_id:
        await _close(websocket, 4401, "invalid session token")
        return

    from app.models.session import Session as SessionModel

    async with state.session_factory() as db:
        session_row = await db.get(SessionModel, session_id)
    if session_row is None:
        await _close(websocket, 4404, "session not found")
        return

    try:
        container_id = await state.workspace.ensure_container(session_id)
        process, stdout, stdin = await state.sandbox.open_shell(container_id)
    except (ValueError, RuntimeError) as exc:
        await _close(websocket, 1011, f"failed to start terminal: {exc}")
        return

    async def pump_stdout() -> None:
        while True:
            data = await stdout.read(4096)
            if not data:
                return
            await websocket.send_text(data.decode("utf-8", errors="replace"))

    async def pump_stdin() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None:
                text = (message.get("bytes") or b"").decode("utf-8", errors="replace")
            if not text:
                continue
            payload = text if text.endswith("\n") else text + "\n"
            stdin.write(payload.encode("utf-8"))
            await stdin.drain()

    stdout_task = asyncio.create_task(pump_stdout(), name="devflow-terminal-stdout")
    stdin_task = asyncio.create_task(pump_stdin(), name="devflow-terminal-stdin")

    try:
        done, pending = await asyncio.wait(
            {stdout_task, stdin_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        # 透传已完成任务里的异常（例如 websocket 已断开）
        for task in done:
            with contextlib.suppress(asyncio.CancelledError):
                exc = task.exception()
                if exc is not None:
                    raise exc
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(Exception):
            stdin.close()
        # 先让 PowerShell 在 stdin EOF 后自行退出，超时再强杀，避免残留 transport
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=5)
        await _close(websocket, 1000, "terminal closed")
