"""Phase 3 Task 3.3：MockProvider + POST run + SSE 流 + SQLite 持久化测试。"""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def api(db_setup):
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


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event_type = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_type = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        assert event_type is not None and data is not None
        events.append((event_type, data))
    return events


# ------------------------------------------------------------------ MockProvider


async def _collect(provider, messages):
    return [
        chunk
        async for chunk in provider.chat_completion_stream(messages, tools=[], temperature=0.3)
    ]


async def test_mock_provider_first_turn_requests_list_src():
    from app.services.agent.mock_provider import MockProvider

    provider = MockProvider()
    chunks = await _collect(provider, [{"role": "user", "content": "看看代码"}])

    tool_calls = [call for chunk in chunks for call in (chunk.tool_calls or [])]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "list"
    assert tool_calls[0].arguments == {"path": "src"}
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens > 0


async def test_mock_provider_returns_summary_after_successful_tool():
    from app.services.agent.mock_provider import MockProvider

    provider = MockProvider()
    messages = [
        {"role": "user", "content": "看看代码"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "tool_call_id": "c1", "content": "src: 2 file(s)"},
    ]
    chunks = await _collect(provider, messages)

    assert all(chunk.tool_calls is None for chunk in chunks)
    content = "".join(chunk.content or "" for chunk in chunks)
    assert "src" in content
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage.total_tokens > 0


async def test_mock_provider_falls_back_to_workspace_root_when_src_missing():
    from app.services.agent.mock_provider import MockProvider

    provider = MockProvider()
    messages = [
        {"role": "user", "content": "看看代码"},
        {"role": "tool", "tool_call_id": "c1", "content": "path not found: src"},
    ]
    chunks = await _collect(provider, messages)

    tool_calls = [call for chunk in chunks for call in (chunk.tool_calls or [])]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "list"
    assert tool_calls[0].arguments == {"path": ""}


async def test_mock_provider_implemented_against_llm_interface():
    from app.services.agent.mock_provider import MockProvider
    from app.services.agent.provider import LLMProvider

    assert isinstance(MockProvider(), LLMProvider)


# --------------------------------------------------------------- run + SSE


async def test_full_run_streams_events_and_persists(api):
    session = await _create_session(api)
    session_id = session["id"]

    run = await api.post(
        f"/sessions/{session_id}/run",
        json={"message": "帮我看看 src 目录"},
        headers=_auth(session["token"]),
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]

    stream = await api.get(f"/agent/stream/{session_id}", headers=_auth(session["token"]))
    assert stream.status_code == 200, stream.text
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["cache-control"] == "no-cache"
    assert stream.headers["x-accel-buffering"] == "no"

    events = parse_sse(stream.text)
    types = [event_type for event_type, _ in events]
    assert types[0] == "tool.call"
    assert "tool.result" in types
    assert "message.delta" in types
    assert types[-1] == "done"

    tool_call_data = events[0][1]
    assert tool_call_data["tool_name"] == "list"
    assert tool_call_data["arguments"] == {"path": "src"}

    tool_result_data = next(data for event_type, data in events if event_type == "tool.result")
    assert tool_result_data["ok"] is True

    assistant_text = "".join(
        data["content"] for event_type, data in events if event_type == "message.delta"
    )
    assert "src" in assistant_text

    done_data = events[-1][1]
    assert done_data["run_id"] == run_id
    assert done_data["total_steps"] == 1
    assert done_data["total_tokens"] > 0

    from app.db import SessionLocal
    from app.models.message import Message
    from app.models.tool_call import ToolCall
    from app.models.usage import Usage

    async with SessionLocal() as db:
        messages = (
            (await db.execute(select(Message).where(Message.session_id == session_id)))
            .scalars()
            .all()
        )
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[0].content == "帮我看看 src 目录"
        assert "src" in messages[1].content

        tool_rows = (
            (await db.execute(select(ToolCall).where(ToolCall.session_id == session_id)))
            .scalars()
            .all()
        )
        assert len(tool_rows) == 1
        assert tool_rows[0].tool_name == "list"
        assert tool_rows[0].input_json == {"path": "src"}
        assert tool_rows[0].ok is True
        assert tool_rows[0].result_json is not None

        usage_rows = (
            (await db.execute(select(Usage).where(Usage.session_id == session_id)))
            .scalars()
            .all()
        )
        assert len(usage_rows) == 1
        assert usage_rows[0].run_id == run_id
        assert usage_rows[0].total_steps == 1

    from app.main import app

    assert session_id not in app.state.loop_states


async def test_run_falls_back_to_root_listing_for_repo_without_src(api):
    session = await _create_session(api, demo_repo="express-demo")

    run = await api.post(
        f"/sessions/{session['id']}/run",
        json={"message": "看看项目"},
        headers=_auth(session["token"]),
    )
    assert run.status_code == 200

    stream = await api.get(f"/agent/stream/{session['id']}", headers=_auth(session["token"]))
    events = parse_sse(stream.text)
    list_calls = [
        data for event_type, data in events if event_type == "tool.call"
    ]
    assert [call["arguments"] for call in list_calls] == [{"path": "src"}, {"path": ""}]
    assert events[-1][0] == "done"


async def test_stream_accepts_token_query_param(api):
    session = await _create_session(api)
    await api.post(
        f"/sessions/{session['id']}/run",
        json={"message": "hi"},
        headers=_auth(session["token"]),
    )

    stream = await api.get(f"/agent/stream/{session['id']}?token={session['token']}")

    assert stream.status_code == 200
    assert "event: done" in stream.text


async def test_stream_requires_auth_and_ownership(api):
    first = await _create_session(api)
    second = await _create_session(api)
    await api.post(
        f"/sessions/{first['id']}/run",
        json={"message": "hi"},
        headers=_auth(first["token"]),
    )

    assert (await api.get(f"/agent/stream/{first['id']}")).status_code == 401
    assert (
        await api.get(f"/agent/stream/{first['id']}", headers=_auth(second["token"]))
    ).status_code == 403


async def test_stream_without_active_run_returns_404(api):
    session = await _create_session(api)

    response = await api.get(f"/agent/stream/{session['id']}", headers=_auth(session["token"]))

    assert response.status_code == 404


async def test_run_requires_auth_and_ownership(api):
    first = await _create_session(api)
    second = await _create_session(api)

    assert (
        await api.post(f"/sessions/{first['id']}/run", json={"message": "hi"})
    ).status_code == 401
    assert (
        await api.post(
            f"/sessions/{first['id']}/run",
            json={"message": "hi"},
            headers=_auth(second["token"]),
        )
    ).status_code == 403


async def test_run_rejects_empty_message(api):
    session = await _create_session(api)

    response = await api.post(
        f"/sessions/{session['id']}/run", json={"message": ""}, headers=_auth(session["token"])
    )

    assert response.status_code == 422


async def test_sse_disconnect_cancels_background_task(api):
    from app.api.agent import sse_event_generator
    from app.db import SessionLocal

    class DisconnectingRequest:
        def __init__(self) -> None:
            self.checks = 0

        async def is_disconnected(self) -> bool:
            self.checks += 1
            return True

    session = await _create_session(api)
    session_id = session["id"]

    dummy_task = asyncio.create_task(asyncio.sleep(30))
    state = {"task": dummy_task, "queue": asyncio.Queue(), "run_id": "run-disconnect"}
    loop_states = {session_id: state}

    chunks = [
        chunk
        async for chunk in sse_event_generator(
            DisconnectingRequest(), session_id, state, loop_states, SessionLocal
        )
    ]
    await asyncio.sleep(0)

    assert chunks == []
    assert dummy_task.cancelled()
    assert session_id not in loop_states


async def test_sse_midstream_disconnect_cancels_run_task_within_second(api):
    """客户端在收到部分事件后断开：后台 run task 必须在 1 秒内被取消。"""
    from app.api.agent import sse_event_generator
    from app.core.events import MessageDeltaEvent
    from app.db import SessionLocal

    class MidStreamDisconnect:
        def __init__(self, disconnect_after_checks: int) -> None:
            self.checks = 0
            self._after = disconnect_after_checks

        async def is_disconnected(self) -> bool:
            self.checks += 1
            return self.checks > self._after

    session = await _create_session(api)
    session_id = session["id"]

    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(MessageDeltaEvent(content="部分输出"))
    run_task = asyncio.create_task(asyncio.sleep(30))
    state = {"task": run_task, "queue": queue, "run_id": "run-mid-disconnect"}
    loop_states = {session_id: state}
    request = MidStreamDisconnect(disconnect_after_checks=1)

    chunks = [
        chunk
        async for chunk in sse_event_generator(
            request, session_id, state, loop_states, SessionLocal
        )
    ]

    assert len(chunks) == 1
    assert "message.delta" in chunks[0]
    await asyncio.wait_for(
        asyncio.gather(run_task, return_exceptions=True), timeout=1.0
    )
    assert run_task.cancelled()
    assert session_id not in loop_states
