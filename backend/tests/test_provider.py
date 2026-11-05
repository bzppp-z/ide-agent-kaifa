"""DeepSeekProvider 流式解析与重试行为测试（全部使用 MockTransport，不访问网络）。"""

import json

import httpx
import pytest

from app.services.agent.provider import (
    ChatCompletionChunk,
    DeepSeekProvider,
    LLMProvider,
    ToolCallChunk,
)


def _sse(payloads: list[dict | str]) -> bytes:
    body: list[str] = []
    for payload in payloads:
        if payload == "[DONE]":
            body.append("data: [DONE]\n\n")
        else:
            body.append("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n")
    return "".join(body).encode("utf-8")


def _provider(handler, **kwargs) -> DeepSeekProvider:
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("base_url", "https://provider.test/v1")
    kwargs.setdefault("model", "deepseek-chat")
    kwargs.setdefault("backoff_base", 0.0)
    return DeepSeekProvider(transport=httpx.MockTransport(handler), **kwargs)


async def _collect(provider: LLMProvider, messages: list[dict] | None = None) -> list[ChatCompletionChunk]:
    return [
        chunk
        async for chunk in provider.chat_completion_stream(
            messages or [{"role": "user", "content": "hello"}]
        )
    ]


def test_provider_implements_abstract_interface():
    assert issubclass(DeepSeekProvider, LLMProvider)

    chunk = ChatCompletionChunk(
        tool_calls=[ToolCallChunk(id="c1", name="list", arguments={"path": "src"})],
        finish_reason="tool_calls",
    )
    assert chunk.content is None


async def test_streams_text_deltas_and_finish_reason():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            content=_sse(
                [
                    {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
                    {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
                    {
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    },
                    "[DONE]",
                ]
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = await _collect(_provider(handler))

    assert [chunk.content for chunk in chunks if chunk.content] == ["Hel", "lo"]
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 15

    request = seen_requests[0]
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["stream"] is True
    assert body["model"] == "deepseek-chat"


async def test_accumulates_fragmented_tool_calls_by_index():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_a",
                                            "type": "function",
                                            "function": {"name": "list", "arguments": '{"pa'},
                                        },
                                        {
                                            "index": 1,
                                            "id": "call_b",
                                            "type": "function",
                                            "function": {"name": "search", "arguments": '{"query": "r'},
                                        },
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {"index": 0, "function": {"arguments": 'th": "src"}'}},
                                        {"index": 1, "function": {"arguments": 'outer", "path": "src/"}'}},
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                    "[DONE]",
                ]
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = await _collect(_provider(handler, max_retries=0))
    tool_chunks = [chunk for chunk in chunks if chunk.tool_calls]

    assert len(tool_chunks) == 1
    calls = tool_chunks[0].tool_calls
    assert [call.id for call in calls] == ["call_a", "call_b"]
    assert [call.name for call in calls] == ["list", "search"]
    assert calls[0].arguments == {"path": "src"}
    assert calls[1].arguments == {"query": "router", "path": "src/"}
    assert tool_chunks[0].finish_reason == "tool_calls"


async def test_stops_after_done_marker():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                [
                    {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]},
                    "[DONE]",
                    {"choices": [{"delta": {"content": "IGNORED"}, "finish_reason": None}]},
                ]
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = await _collect(_provider(handler, max_retries=0))

    assert [chunk.content for chunk in chunks] == ["a"]


async def test_401_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await _collect(_provider(handler, max_retries=3))

    assert exc_info.value.response.status_code == 401
    assert len(calls) == 1


async def test_5xx_is_retried_three_times_then_raises():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"error": {"message": "server error"}})

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await _collect(_provider(handler, max_retries=3))

    assert exc_info.value.response.status_code == 500
    assert len(calls) == 4  # 首次 + 3 次重试


async def test_5xx_then_success_recovers():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return httpx.Response(
            200,
            content=_sse(
                [
                    {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
                    "[DONE]",
                ]
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = await _collect(_provider(handler, max_retries=3))

    assert [chunk.content for chunk in chunks if chunk.content] == ["ok"]
    assert len(calls) == 2


async def test_timeout_is_retried_then_raises():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.TimeoutException("timed out", request=request)

    with pytest.raises(httpx.TimeoutException):
        await _collect(_provider(handler, max_retries=3))

    assert len(calls) == 4
