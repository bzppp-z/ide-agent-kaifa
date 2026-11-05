"""LLM Provider adapter。

接口：``LLMProvider.chat_completion_stream(messages, tools, temperature)``
返回 ``AsyncIterator[ChatCompletionChunk]``，供 agent loop 消费流式输出。

``DeepSeekProvider`` 使用 httpx 异步流式请求 OpenAI 兼容的
``POST {base_url}/chat/completions`` 接口。API key 只从构造参数 /
``app.core.config.settings`` 读取，绝不写入日志或数据库。
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

_RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.HTTPStatusError)


class UsageDelta(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ToolCallChunk(BaseModel):
    id: str | None = None
    name: str | None = None
    # OpenAI 流式协议里 arguments 是分片字符串；provider 会尽量解析成 dict，
    # 解析失败时保留原始字符串，由调用方决定如何处理。
    arguments: dict[str, Any] | str = Field(default_factory=dict)


class ChatCompletionChunk(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCallChunk] | None = None
    finish_reason: str | None = None
    usage: UsageDelta | None = None


class LLMProvider(ABC):
    @abstractmethod
    def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """流式返回模型输出。实现必须是 async generator。"""
        raise NotImplementedError


class DeepSeekProvider(LLMProvider):
    """DeepSeek（OpenAI 兼容）流式实现，带指数退避重试。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self.transport = transport
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout

    async def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatCompletionChunk]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        emitted = False
        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(
                    transport=self.transport, timeout=self.timeout
                ) as client:
                    async for chunk in self._stream_once(client, payload, headers):
                        emitted = True
                        yield chunk
                return
            except _RETRYABLE_ERRORS as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
                    # key 无效/无权限，重试没有意义
                    raise
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                if emitted:
                    # 已经向调用方输出了部分内容，重试会造成重复输出
                    raise
                attempt += 1
                if attempt > self.max_retries:
                    raise
                delay = self.backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "LLM request failed (%s), retrying in %.2fs (attempt %d/%d)",
                    type(exc).__name__,
                    delay,
                    attempt,
                    self.max_retries,
                )
                await asyncio.sleep(delay)

    async def _stream_once(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = f"{self.base_url}/chat/completions"
        partial_calls: dict[int, dict[str, Any]] = {}

        async with client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()

            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue

                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning("ignore malformed SSE data line")
                    continue

                if not isinstance(data, dict):
                    continue

                choices = data.get("choices") or []
                choice = choices[0] if isinstance(choices, list) and choices else {}
                if not isinstance(choice, dict):
                    choice = {}

                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    delta = {}

                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield ChatCompletionChunk(content=content)

                for tool_delta in delta.get("tool_calls") or []:
                    if not isinstance(tool_delta, dict):
                        continue
                    index = tool_delta.get("index", 0)
                    if not isinstance(index, int):
                        index = 0
                    acc = partial_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
                    if tool_delta.get("id"):
                        acc["id"] = tool_delta["id"]
                    function = tool_delta.get("function") or {}
                    if isinstance(function, dict):
                        if function.get("name"):
                            acc["name"] = function["name"]
                        if function.get("arguments"):
                            acc["arguments"] += function["arguments"]

                finish_reason = choice.get("finish_reason")
                usage_payload = data.get("usage")

                if finish_reason is not None:
                    tool_calls = self._build_tool_calls(partial_calls)
                    partial_calls = {}
                    yield ChatCompletionChunk(
                        tool_calls=tool_calls or None,
                        finish_reason=finish_reason,
                        usage=self._build_usage(usage_payload),
                    )
                elif usage_payload:
                    yield ChatCompletionChunk(usage=self._build_usage(usage_payload))

        if partial_calls:
            yield ChatCompletionChunk(tool_calls=self._build_tool_calls(partial_calls))

    def _build_tool_calls(self, partial_calls: dict[int, dict[str, Any]]) -> list[ToolCallChunk]:
        calls: list[ToolCallChunk] = []
        for index in sorted(partial_calls):
            acc = partial_calls[index]
            raw_arguments = acc.get("arguments") or ""
            arguments: dict[str, Any] | str
            if raw_arguments.strip():
                try:
                    parsed = json.loads(raw_arguments)
                    arguments = parsed if isinstance(parsed, dict) else raw_arguments
                except json.JSONDecodeError:
                    arguments = raw_arguments
            else:
                arguments = {}
            calls.append(
                ToolCallChunk(
                    id=acc.get("id"),
                    name=acc.get("name"),
                    arguments=arguments,
                )
            )
        return calls

    @staticmethod
    def _build_usage(usage_payload: Any) -> UsageDelta | None:
        if not isinstance(usage_payload, dict):
            return None
        return UsageDelta(
            prompt_tokens=int(usage_payload.get("prompt_tokens") or 0),
            completion_tokens=int(usage_payload.get("completion_tokens") or 0),
            total_tokens=int(usage_payload.get("total_tokens") or 0),
        )
