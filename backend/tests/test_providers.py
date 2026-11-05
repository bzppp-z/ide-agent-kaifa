"""用户自主添加模型（Provider）的 CRUD / 连通性 / 运行时解析测试。

所有外部网络调用都通过 httpx.MockTransport 注入，不访问真实接口。
"""

import json

import httpx
import pytest
import pytest_asyncio

from app.db import utcnow
from app.models.provider import Provider

SAMPLE = {
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "sk-1234567890abcdef",
}


@pytest_asyncio.fixture
async def api(db_setup):
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def _create(api, **overrides) -> dict:
    payload = {**SAMPLE, **overrides}
    response = await api.post("/providers", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _sse(payloads: list[dict | str]) -> bytes:
    body: list[str] = []
    for payload in payloads:
        if payload == "[DONE]":
            body.append("data: [DONE]\n\n")
        else:
            body.append("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n")
    return "".join(body).encode("utf-8")


# ------------------------------------------------------------------ CRUD


async def test_first_provider_becomes_active(api):
    created = await _create(api, base_url="https://api.deepseek.com/v1/")

    assert created["is_active"] is True
    assert created["base_url"] == "https://api.deepseek.com/v1"
    assert created["api_key_masked"] == "sk-1...cdef"
    assert "api_key" not in created
    assert SAMPLE["api_key"] not in json.dumps(created)


async def test_second_provider_is_not_active(api):
    first = await _create(api)
    second = await _create(api, name="Moonshot", api_key="sk-abcdefghijklmnop")

    assert first["is_active"] is True
    assert second["is_active"] is False

    listed = (await api.get("/providers")).json()
    assert len(listed) == 2
    assert all(item["is_active"] is False for item in listed if item["id"] == second["id"])


async def test_list_masks_keys_and_never_returns_plaintext(api):
    long_key = "sk-1234567890abcdef"
    short_key = "abc"
    await _create(api, api_key=long_key)
    await _create(api, name="Short", api_key=short_key)
    await _create(api, name="NoKey", api_key="")

    response = await api.get("/providers")
    assert response.status_code == 200
    body = response.json()

    masked = {item["name"]: item["api_key_masked"] for item in body}
    assert masked["DeepSeek"] == "sk-1...cdef"
    assert masked["Short"] == "*" * len(short_key)
    assert masked["NoKey"] == ""

    assert long_key not in response.text
    assert short_key not in response.text
    assert all("api_key" not in item for item in body)


async def test_activate_leaves_only_one_active(api):
    first = await _create(api)
    second = await _create(api, name="Moonshot", api_key="sk-other-key-1234")

    response = await api.post(f"/providers/{second['id']}/activate")

    assert response.status_code == 200
    assert response.json()["is_active"] is True

    listed = (await api.get("/providers")).json()
    active = [item["id"] for item in listed if item["is_active"]]
    assert active == [second["id"]]
    assert first["id"] not in active


async def test_delete_active_transfers_to_first_remaining(api):
    first = await _create(api)
    second = await _create(api, name="Moonshot", api_key="sk-other-key-1234")
    assert first["is_active"] is True

    deleted = await api.delete(f"/providers/{first['id']}")
    assert deleted.status_code == 200

    listed = (await api.get("/providers")).json()
    assert [item["id"] for item in listed] == [second["id"]]
    assert listed[0]["is_active"] is True


async def test_patch_with_empty_api_key_keeps_existing_key(api):
    created = await _create(api)
    response = await api.patch(
        f"/providers/{created['id']}", json={"name": "Renamed", "api_key": ""}
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Renamed"
    assert updated["api_key_masked"] == created["api_key_masked"]


async def test_patch_updates_fields_and_trims_base_url(api):
    created = await _create(api)
    response = await api.patch(
        f"/providers/{created['id']}",
        json={"base_url": "https://example.test/v1/", "model": "custom-model"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["base_url"] == "https://example.test/v1"
    assert updated["model"] == "custom-model"


async def test_patch_non_empty_api_key_updates_mask(api):
    created = await _create(api)
    response = await api.patch(
        f"/providers/{created['id']}", json={"api_key": "sk-new-key-9876543210"}
    )

    assert response.status_code == 200
    assert response.json()["api_key_masked"] == "sk-n...3210"
    assert "sk-new-key-9876543210" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {**SAMPLE, "base_url": "ftp://api.example.test/v1"},
        {**SAMPLE, "base_url": "api.example.test/v1"},
        {**SAMPLE, "base_url": ""},
        {**SAMPLE, "name": "  "},
        {**SAMPLE, "model": ""},
    ],
)
async def test_create_rejects_invalid_fields_with_400(api, payload):
    response = await api.post("/providers", json=payload)

    assert response.status_code == 400, response.text


async def test_patch_rejects_invalid_base_url_with_400(api):
    created = await _create(api)

    response = await api.patch(
        f"/providers/{created['id']}", json={"base_url": "ftp://bad.test/v1"}
    )

    assert response.status_code == 400, response.text


async def test_unknown_provider_returns_404(api):
    assert (await api.patch("/providers/nope", json={"name": "x"})).status_code == 404
    assert (await api.delete("/providers/nope")).status_code == 404
    assert (await api.post("/providers/nope/activate")).status_code == 404
    assert (await api.post("/providers/nope/test")).status_code == 404


# --------------------------------------------------------- connectivity test


async def test_provider_test_endpoint_success(api):
    from app.main import app

    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            content=_sse(
                [
                    {"choices": [{"delta": {"content": "pong"}, "finish_reason": "stop"}]},
                    "[DONE]",
                ]
            ),
            headers={"content-type": "text/event-stream"},
        )

    app.state.provider_test_transport = httpx.MockTransport(handler)
    try:
        created = await _create(api)
        response = await api.post(f"/providers/{created['id']}/test")
    finally:
        del app.state.provider_test_transport

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    assert SAMPLE["api_key"] not in response.text

    assert len(seen_requests) == 1
    assert seen_requests[0].headers["authorization"] == f"Bearer {SAMPLE['api_key']}"
    assert json.loads(seen_requests[0].content)["messages"] == [
        {"role": "user", "content": "ping"}
    ]


async def test_provider_test_endpoint_reports_failure_without_leaking_key(api):
    from app.main import app

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    app.state.provider_test_transport = httpx.MockTransport(handler)
    try:
        created = await _create(api)
        response = await api.post(f"/providers/{created['id']}/test")
    finally:
        del app.state.provider_test_transport

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "401" in body["message"]
    assert isinstance(body["latency_ms"], int)
    assert SAMPLE["api_key"] not in response.text
    assert SAMPLE["api_key"] not in body["message"]


# --------------------------------------------------------- provider registry


async def test_resolve_provider_uses_active_db_provider(db_session):
    from app.services.agent.provider import DeepSeekProvider
    from app.services.provider_registry import resolve_provider

    db_session.add(
        Provider(
            id="provider-active",
            name="Custom",
            base_url="https://custom.test/v1",
            model="custom-model",
            api_key="secret-key",
            is_active=True,
            created_at=utcnow(),
        )
    )
    await db_session.commit()

    provider = await resolve_provider(db_session)

    assert isinstance(provider, DeepSeekProvider)
    assert provider.base_url == "https://custom.test/v1"
    assert provider.model == "custom-model"
    assert provider.api_key == "secret-key"


async def test_resolve_provider_falls_back_to_settings_mock(db_session):
    from app.services.agent.mock_provider import MockProvider
    from app.services.provider_registry import resolve_provider

    provider = await resolve_provider(db_session)

    assert isinstance(provider, MockProvider)


# ------------------------------------------------------------------ regression


async def test_run_still_completes_with_provider_registry(api):
    from app.db import SessionLocal
    from app.models.message import Message
    from app.models.provider import Provider as ProviderModel
    from app.models.session import Session as SessionModel
    from sqlalchemy import select

    session = await api.post("/sessions", json={"demo_repo": "todo-app"})
    assert session.status_code == 200
    session_id = session.json()["id"]

    run = await api.post(
        f"/sessions/{session_id}/run",
        json={"message": "帮我看看 src 目录"},
        headers={"Authorization": f"Bearer {session.json()['token']}"},
    )
    assert run.status_code == 200, run.text

    stream = await api.get(
        f"/agent/stream/{session_id}",
        headers={"Authorization": f"Bearer {session.json()['token']}"},
    )
    assert stream.status_code == 200
    assert "event: done" in stream.text

    async with SessionLocal() as db:
        await db.execute(select(ProviderModel))
        messages = (
            (await db.execute(select(Message).where(Message.session_id == session_id)))
            .scalars()
            .all()
        )
        assert [message.role for message in messages] == ["user", "assistant"]
        session_row = await db.get(SessionModel, session_id)
        assert session_row is not None
