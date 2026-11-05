"""Phase 3 Task 3.5：完整 API 链路集成测试。

create → run → SSE 流 → files 读写 → GET session 恢复 → （模拟重启）→ DELETE。
"""

import json

import httpx
import pytest_asyncio


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


@pytest_asyncio.fixture
async def api(db_setup):
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def test_health_returns_three_ok_flags(api):
    response = await api.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "db": True, "sandbox": True}


async def test_full_api_flow_and_session_recovery(db_setup):
    from app.main import app
    from app.services.agent.mock_provider import MockProvider

    base_url = "http://testserver"
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.provider, MockProvider)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            created = await client.post("/sessions", json={"demo_repo": "todo-app"})
            assert created.status_code == 200
            session = created.json()
            session_id = session["id"]
            headers = {"Authorization": f"Bearer {session['token']}"}

            run = await client.post(
                f"/sessions/{session_id}/run",
                json={"message": "帮我看看 src 目录"},
                headers=headers,
            )
            assert run.status_code == 200
            run_id = run.json()["run_id"]

            stream = await client.get(f"/agent/stream/{session_id}", headers=headers)
            assert stream.status_code == 200
            events = parse_sse(stream.text)
            types = [event_type for event_type, _ in events]
            assert types[0] == "tool.call"
            assert "tool.result" in types
            assert "message.delta" in types
            assert types[-1] == "done"
            assert events[-1][1]["total_steps"] == 1
            assert run_id

            root = await client.get(f"/files/{session_id}/", headers=headers)
            assert root.status_code == 200
            assert root.json()["type"] == "dir"

            read = await client.get(f"/files/{session_id}/src/index.js", headers=headers)
            assert read.status_code == 200
            assert "addTodo" in read.json()["content"]

            write = await client.post(
                f"/files/{session_id}/e2e/note.txt",
                json={"content": "hello e2e\n"},
                headers=headers,
            )
            assert write.status_code == 200
            assert write.json() == {"ok": True, "path": "e2e/note.txt"}

            read_back = await client.get(f"/files/{session_id}/e2e/note.txt", headers=headers)
            assert read_back.status_code == 200
            assert read_back.json()["content"] == "hello e2e\n"

            detail = await client.get(f"/sessions/{session_id}", headers=headers)
            assert detail.status_code == 200
            detail_payload = detail.json()
            assert detail_payload["container_ready"] is True
            assert [message["role"] for message in detail_payload["messages"]] == [
                "user",
                "assistant",
            ]

    # 模拟后端重启：重新进入 lifespan（新的 provider / loop_states），DB 不变
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            recovered = await client.get(f"/sessions/{session_id}", headers=headers)
            assert recovered.status_code == 200
            payload = recovered.json()
            assert len(payload["messages"]) == 2
            assert payload["messages"][0]["content"] == "帮我看看 src 目录"
            assert "src" in payload["messages"][1]["content"]

            deleted = await client.delete(f"/sessions/{session_id}", headers=headers)
            assert deleted.status_code == 200
            assert deleted.json()["ok"] is True

            after_delete = await client.get(f"/sessions/{session_id}", headers=headers)
            assert after_delete.status_code == 200
            assert after_delete.json()["container_ready"] is False
            assert len(after_delete.json()["messages"]) == 2


async def test_api_routes_are_registered():
    from app.main import app

    paths = set(app.openapi()["paths"])

    assert {
        "/health",
        "/sessions",
        "/sessions/{session_id}",
        "/sessions/{session_id}/run",
        "/agent/stream/{session_id}",
        "/files/{session_id}/{path}",
    } <= paths
