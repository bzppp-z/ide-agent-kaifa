"""SSE 事件定义。

所有事件继承 ``BaseSSEEvent``，统一通过 ``to_sse_str()`` 序列化为
``event: <type>\\ndata: <json>\\n\\n``。
"""

import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class BaseSSEEvent(BaseModel):
    type: str

    def to_sse_str(self) -> str:
        payload = self.model_dump(mode="json", exclude={"type"})
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.type}\ndata: {data}\n\n"


class MessageDeltaEvent(BaseSSEEvent):
    """assistant 增量文本。"""

    type: Literal["message.delta"] = "message.delta"
    content: str


class ToolCallEvent(BaseSSEEvent):
    """agent 发起一次工具调用。"""

    type: Literal["tool.call"] = "tool.call"
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(BaseSSEEvent):
    """工具调用结果。"""

    type: Literal["tool.result"] = "tool.result"
    call_id: str
    tool_name: str
    ok: bool
    result: Any = None
    duration_ms: int = 0


class PatchProposedEvent(BaseSSEEvent):
    """write_patch 工具产生待审批的修改建议。"""

    type: Literal["patch.proposed"] = "patch.proposed"
    patch_id: str
    path: str
    search: str
    replace: str


class RunOutputEvent(BaseSSEEvent):
    """命令执行期间产生的 stdout/stderr 输出。"""

    type: Literal["run.output"] = "run.output"
    stream: Literal["stdout", "stderr"] = "stdout"
    chunk: str


class ErrorEvent(BaseSSEEvent):
    """流式过程中的错误。"""

    type: Literal["error"] = "error"
    message: str
    code: str | None = None


class DoneEvent(BaseSSEEvent):
    """一次 run 结束。"""

    type: Literal["done"] = "done"
    run_id: str | None = None
    warning: str | None = None
    total_tokens: int = 0
    total_steps: int = 0


SSEEvent = Annotated[
    Union[
        MessageDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
        PatchProposedEvent,
        RunOutputEvent,
        ErrorEvent,
        DoneEvent,
    ],
    Field(discriminator="type"),
]
