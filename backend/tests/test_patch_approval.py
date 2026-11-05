"""Phase 5 Task 5.1：write_patch dry_run、patch 审批暂停/接受/拒绝/超时、PATCH 端点。

先写测试（RED），再实现（GREEN）。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.core.events import PatchProposedEvent
from app.services.agent.loop import AgentLoop
from app.services.agent.pending import PendingPatchRegistry
from app.services.agent.provider import (
    ChatCompletionChunk,
    LLMProvider,
    ToolCallChunk,
    UsageDelta,
)
from app.services.agent.tools import ToolRegistry

PATCH_ARGS = {
    "path": "src/app.js",
    "search": "const port = 3000;",
    "replace": "const port = 8080;",
}


class ScriptedProvider(LLMProvider):
    """按脚本逐轮返回 chunk，记录每次收到的 messages。"""

    def __init__(self, scripts: list[list[ChatCompletionChunk]]) -> None:
        self._scripts = list(scripts)
        self.calls: list[list[dict]] = []

    async def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatCompletionChunk]:
        self.calls.append([dict(message) for message in messages])
        for chunk in self._scripts.pop(0):
            yield chunk


def _tool_round(call_id: str, name: str, arguments: dict) -> list[ChatCompletionChunk]:
    return [
        ChatCompletionChunk(
            tool_calls=[ToolCallChunk(id=call_id, name=name, arguments=arguments)],
            finish_reason="tool_calls",
            usage=UsageDelta(total_tokens=20),
        )
    ]


def _final_text(text: str = "完成") -> list[ChatCompletionChunk]:
    return [ChatCompletionChunk(content=text, finish_reason="stop", usage=UsageDelta(total_tokens=10))]


@pytest.fixture
def container_id(sandbox):
    cid = sandbox.create_session_container(f"session-{uuid.uuid4()}")
    sandbox.write_file(cid, "src/app.js", "const port = 3000;\n")
    return cid


async def _drive(
    provider: LLMProvider,
    sandbox,
    container_id: str,
    registry: PendingPatchRegistry | None,
    *,
    decision: bool | None = None,
    patch_timeout: float = 120.0,
) -> list:
    """跑 loop；出现 patch.proposed 时按 decision 立即决议（None 表示不决议，等超时）。"""
    loop = AgentLoop(pending_patches=registry)
    events: list = []

    async def consume() -> None:
        async for event in loop.run_stream(
            "session-patch-test",
            "改端口",
            provider=provider,
            tools=ToolRegistry(),
            sandbox=sandbox,
            session_workspace_path=container_id,
            patch_approval_timeout=patch_timeout,
        ):
            events.append(event)
            if isinstance(event, PatchProposedEvent) and decision is not None:
                assert registry is not None
                registry.resolve(event.patch_id, decision)

    await asyncio.wait_for(consume(), timeout=5.0)
    return events


# ---------------------------------------------------------------- dry_run（工具层）


async def test_write_patch_dry_run_does_not_write_and_returns_before_after(sandbox, container_id):
    tools = ToolRegistry()

    result = await tools.call("write_patch", PATCH_ARGS, sandbox, container_id, dry_run=True)

    assert result.ok is True
    assert result.extra["applied"] is False
    assert result.extra["path"] == "src/app.js"
    assert result.extra["before"] == "const port = 3000;\n"
    assert result.extra["after"] == "const port = 8080;\n"
    # 未落盘
    assert sandbox.read_file(container_id, "src/app.js") == "const port = 3000;\n"


async def test_write_patch_dry_run_still_rejects_pattern_not_found(sandbox, container_id):
    tools = ToolRegistry()

    result = await tools.call(
        "write_patch",
        {"path": "src/app.js", "search": "not-there", "replace": "x"},
        sandbox,
        container_id,
        dry_run=True,
    )

    assert result.ok is False
    assert "pattern not found" in result.summary
    assert sandbox.read_file(container_id, "src/app.js") == "const port = 3000;\n"


# ------------------------------------------------------------------- loop 审批


async def test_loop_emits_patch_proposed_then_waits_for_accept(sandbox, container_id):
    registry = PendingPatchRegistry()
    provider = ScriptedProvider([_tool_round("c1", "write_patch", PATCH_ARGS), _final_text()])

    events = await _drive(provider, sandbox, container_id, registry, decision=True)

    types = [event.type for event in events]
    assert types == ["tool.call", "patch.proposed", "tool.result", "message.delta", "done"]

    patch = events[1]
    assert isinstance(patch, PatchProposedEvent)
    assert patch.path == "src/app.js"
    assert patch.search == "const port = 3000;"
    assert patch.replace == "const port = 8080;"
    assert patch.before == "const port = 3000;\n"
    assert patch.after == "const port = 8080;\n"
    assert "-const port = 3000;" in patch.diff
    assert "+const port = 8080;" in patch.diff

    tool_result = events[2]
    assert tool_result.ok is True
    assert "patched" in tool_result.result["summary"]

    # 接受后真正落盘
    assert sandbox.read_file(container_id, "src/app.js") == "const port = 8080;\n"
    # 审批条目已被清理
    assert registry.get(patch.patch_id) is None
    # 下一轮 LLM 看到成功的工具结果
    assert "patched" in provider.calls[1][-1]["content"]


async def test_loop_reject_does_not_write_and_feeds_rejection_back(sandbox, container_id):
    registry = PendingPatchRegistry()
    provider = ScriptedProvider([_tool_round("c1", "write_patch", PATCH_ARGS), _final_text()])

    events = await _drive(provider, sandbox, container_id, registry, decision=False)

    assert [event.type for event in events] == [
        "tool.call",
        "patch.proposed",
        "tool.result",
        "message.delta",
        "done",
    ]
    patch = events[1]
    tool_result = events[2]

    assert tool_result.ok is False
    assert tool_result.result["summary"] == "user rejected the patch"
    assert sandbox.read_file(container_id, "src/app.js") == "const port = 3000;\n"
    assert registry.get(patch.patch_id) is None
    assert "rejected" in provider.calls[1][-1]["content"]
    # 拒绝后 LLM 仍被再次调用（重新规划）
    assert len(provider.calls) == 2


async def test_loop_treats_approval_timeout_as_rejection(sandbox, container_id):
    registry = PendingPatchRegistry()
    provider = ScriptedProvider([_tool_round("c1", "write_patch", PATCH_ARGS), _final_text()])

    events = await _drive(
        provider, sandbox, container_id, registry, decision=None, patch_timeout=0.05
    )

    assert [event.type for event in events] == [
        "tool.call",
        "patch.proposed",
        "tool.result",
        "message.delta",
        "done",
    ]
    assert events[2].ok is False
    assert "rejected" in events[2].result["summary"]
    assert sandbox.read_file(container_id, "src/app.js") == "const port = 3000;\n"


async def test_registry_resolve_unknown_patch_returns_false_and_wait_times_out():
    registry = PendingPatchRegistry()

    assert registry.resolve("missing", True) is False
    assert await registry.wait("missing", timeout=0.01) is False


# --------------------------------------------------------------- PATCH 端点


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


async def _register_pending(session_id: str, path: str = "src/index.js") -> tuple[str, PendingPatchRegistry]:
    from app.main import app

    registry: PendingPatchRegistry = app.state.pending_patches
    patch_id = str(uuid.uuid4())
    registry.register(
        patch_id,
        session_id=session_id,
        path=path,
        search="a",
        replace="b",
        before="a\n",
        after="b\n",
        diff="-a\n+b",
    )
    return patch_id, registry


async def test_patch_endpoint_accept_records_decision_and_wakes_waiter(api):
    session = await _create_session(api)
    patch_id, registry = await _register_pending(session["id"])

    response = await api.patch(
        f"/agent/patch/{patch_id}",
        json={"accepted": True},
        headers=_auth(session["token"]),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "patch_id": patch_id, "accepted": True}
    assert await registry.wait(patch_id, timeout=0.5) is True


async def test_patch_endpoint_reject_records_false_decision(api):
    session = await _create_session(api)
    patch_id, registry = await _register_pending(session["id"])

    response = await api.patch(
        f"/agent/patch/{patch_id}",
        json={"accepted": False},
        headers=_auth(session["token"]),
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert await registry.wait(patch_id, timeout=0.5) is False


async def test_patch_endpoint_unknown_id_returns_404(api):
    session = await _create_session(api)

    response = await api.patch(
        f"/agent/patch/{uuid.uuid4()}",
        json={"accepted": True},
        headers=_auth(session["token"]),
    )

    assert response.status_code == 404


async def test_patch_endpoint_requires_auth(api):
    session = await _create_session(api)
    patch_id, _ = await _register_pending(session["id"])

    response = await api.patch(f"/agent/patch/{patch_id}", json={"accepted": True})

    assert response.status_code == 401


async def test_patch_endpoint_rejects_foreign_session(api):
    first = await _create_session(api)
    second = await _create_session(api)
    patch_id, registry = await _register_pending(first["id"])

    response = await api.patch(
        f"/agent/patch/{patch_id}",
        json={"accepted": True},
        headers=_auth(second["token"]),
    )

    assert response.status_code == 403
    registry.discard(patch_id)
