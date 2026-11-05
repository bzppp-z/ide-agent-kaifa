"""AgentLoop 主循环、RecentToolCallTracker 与上下文截断测试（全 mock LLM）。"""

import copy
import uuid
from collections.abc import AsyncIterator

import pytest

from app.services.agent.context import estimate_tokens, truncate_messages
from app.services.agent.loop import AgentLoop, RecentToolCallTracker
from app.services.agent.provider import (
    ChatCompletionChunk,
    LLMProvider,
    ToolCallChunk,
    UsageDelta,
)
from app.services.agent.tools import ToolRegistry


class ScriptedProvider(LLMProvider):
    """按脚本逐个返回 chunk 的 mock provider，记录每次收到的 messages。"""

    def __init__(self, scripts: list[list[ChatCompletionChunk]]) -> None:
        self._scripts = list(scripts)
        self.calls: list[list[dict]] = []

    async def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatCompletionChunk]:
        self.calls.append(copy.deepcopy(messages))
        for chunk in self._scripts.pop(0):
            yield chunk


@pytest.fixture
def container_id(sandbox):
    cid = sandbox.create_session_container(f"session-{uuid.uuid4()}")
    sandbox.write_file(cid, "a.txt", "alpha\n")
    sandbox.write_file(cid, "src/app.js", "const a = 1;\n")
    return cid


async def _run(
    provider: LLMProvider, sandbox, container_id: str, message: str = "hi", **kwargs
) -> list:
    loop = AgentLoop()
    events = []
    async for event in loop.run_stream(
        "session-loop-test",
        message,
        provider=provider,
        tools=ToolRegistry(),
        sandbox=sandbox,
        session_workspace_path=container_id,
        **kwargs,
    ):
        events.append(event)
    return events


def _tool_round(
    call_id: str, name: str, arguments: dict, tokens: int = 20
) -> list[ChatCompletionChunk]:
    return [
        ChatCompletionChunk(
            tool_calls=[ToolCallChunk(id=call_id, name=name, arguments=arguments)],
            finish_reason="tool_calls",
            usage=UsageDelta(total_tokens=tokens),
        )
    ]


# ---------------------------------------------------------------- tracker / context


def test_recent_tool_call_tracker_detects_identical_window():
    tracker = RecentToolCallTracker(window=3)

    assert tracker.record("list", {"path": "src"})
    assert not tracker.is_stuck()
    tracker.record("list", {"path": "src"})
    assert not tracker.is_stuck()
    tracker.record("list", {"path": "src"})
    assert tracker.is_stuck()

    tracker.record("read", {"path": "src"})
    assert not tracker.is_stuck()


def test_recent_tool_call_tracker_ignores_argument_key_order():
    tracker = RecentToolCallTracker(window=3)

    tracker.record("search", {"query": "a", "path": "src"})
    tracker.record("search", {"path": "src", "query": "a"})
    tracker.record("search", {"query": "a", "path": "src"})

    assert tracker.is_stuck()


def test_estimate_tokens_scales_with_content():
    assert estimate_tokens([]) == 0
    assert estimate_tokens([{"role": "system", "content": "x"}]) >= 1
    assert estimate_tokens([{"role": "system", "content": "x" * 4000}]) > estimate_tokens(
        [{"role": "system", "content": "x"}]
    )


def test_truncate_messages_keeps_system_and_recent_rounds():
    old_tool = {"role": "tool", "tool_call_id": "t1", "content": "x" * 4000}
    early = [
        old_tool,
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    recent = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "tool_call_id": "t2", "content": "recent tool"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "tool", "tool_call_id": "t3", "content": "recent tool 2"},
    ]
    messages = [{"role": "system", "content": "sys"}] + early + recent

    truncated = truncate_messages(messages, max_tokens=300, keep_recent=6)

    assert truncated[0] == {"role": "system", "content": "sys"}
    assert old_tool not in truncated
    assert truncated[-6:] == recent
    assert estimate_tokens(truncated) < estimate_tokens(messages)


def test_truncate_messages_is_noop_under_budget():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

    assert truncate_messages(messages, max_tokens=100000) == messages


# --------------------------------------------------------------------- loop


async def test_single_text_turn_yields_deltas_and_done(sandbox, container_id):
    provider = ScriptedProvider(
        [
            [
                ChatCompletionChunk(content="你"),
                ChatCompletionChunk(content="好"),
                ChatCompletionChunk(finish_reason="stop", usage=UsageDelta(total_tokens=30)),
            ]
        ]
    )

    events = await _run(provider, sandbox, container_id)

    assert [event.type for event in events] == ["message.delta", "message.delta", "done"]
    assert events[0].content == "你"
    assert events[1].content == "好"
    done = events[-1]
    assert done.warning is None
    assert done.total_tokens == 30
    assert done.total_steps == 0
    assert len(provider.calls) == 1
    assert provider.calls[0][0]["role"] == "system"
    assert "list" in provider.calls[0][0]["content"]


async def test_tool_call_round_then_final_text(sandbox, container_id):
    provider = ScriptedProvider(
        [
            _tool_round("c1", "read", {"path": "a.txt"}),
            [
                ChatCompletionChunk(
                    content="读到了 alpha", finish_reason="stop", usage=UsageDelta(total_tokens=10)
                )
            ],
        ]
    )

    events = await _run(provider, sandbox, container_id)

    assert [event.type for event in events] == [
        "tool.call",
        "tool.result",
        "message.delta",
        "done",
    ]
    tool_call = events[0]
    assert tool_call.call_id == "c1"
    assert tool_call.tool_name == "read"
    assert tool_call.arguments == {"path": "a.txt"}

    tool_result = events[1]
    assert tool_result.call_id == "c1"
    assert tool_result.ok is True
    assert "alpha" in tool_result.result["details"]

    assert events[-1].total_steps == 1
    assert events[-1].total_tokens == 30

    second_call_messages = provider.calls[1]
    assert second_call_messages[-2]["role"] == "assistant"
    assert second_call_messages[-2]["tool_calls"][0]["id"] == "c1"
    assert second_call_messages[-1]["role"] == "tool"
    assert second_call_messages[-1]["tool_call_id"] == "c1"
    assert "alpha" in second_call_messages[-1]["content"]


async def test_repeated_identical_tool_calls_inject_system_hint(sandbox, container_id):
    provider = ScriptedProvider(
        [
            _tool_round("c1", "list", {"path": "src"}),
            _tool_round("c2", "list", {"path": "src"}),
            _tool_round("c3", "list", {"path": "src"}),
            [
                ChatCompletionChunk(
                    content="换个思路", finish_reason="stop", usage=UsageDelta(total_tokens=5)
                )
            ],
        ]
    )

    events = await _run(provider, sandbox, container_id)

    assert [event.type for event in events].count("tool.call") == 3
    assert events[-1].type == "done"
    assert events[-1].warning is None

    fourth_call_messages = provider.calls[3]
    hints = [
        message
        for message in fourth_call_messages
        if message["role"] == "system" and "连续 3 次" in message["content"]
    ]
    assert len(hints) == 1
    assert "list" in hints[0]["content"]


async def test_max_steps_hard_limit_yields_warning(sandbox, container_id):
    provider = ScriptedProvider(
        [_tool_round(f"c{index}", "list", {"path": "src"}) for index in range(5)]
    )

    events = await _run(provider, sandbox, container_id, max_steps=2)

    assert [event.type for event in events].count("tool.call") == 2
    done = events[-1]
    assert done.type == "done"
    assert "max_steps" in done.warning
    assert done.total_steps == 2
    assert len(provider.calls) == 2


async def test_max_tokens_hard_limit_yields_warning(sandbox, container_id):
    provider = ScriptedProvider(
        [
            _tool_round(f"c{index}", "list", {"path": "src"}, tokens=600)
            for index in range(5)
        ]
    )

    events = await _run(provider, sandbox, container_id, max_steps=5, max_tokens=1000)

    assert [event.type for event in events].count("tool.call") == 2
    done = events[-1]
    assert done.type == "done"
    assert "max_tokens" in done.warning
    assert done.total_tokens == 1200
    assert len(provider.calls) == 2
