"""Phase 2 核心验证：mock LLM + 真实本地 Sandbox 跑一次完整 agent loop。

不连真实 LLM、不用 Docker：临时目录 + 真实 ``SandboxService``，
MockProvider 依次返回 list → search → 最终文本，脚本打印全部 SSE 事件。

运行（在项目根目录）：
    C:\\Users\\bzp20\\miniconda3\\envs\\agent-dev\\python.exe backend/scripts/mock_loop_demo.py
"""

import asyncio
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.services.agent.loop import AgentLoop
from app.services.agent.provider import (
    ChatCompletionChunk,
    LLMProvider,
    ToolCallChunk,
    UsageDelta,
)
from app.services.agent.tools import ToolRegistry
from app.services.sandbox import SandboxService

FILES = {
    "src/index.js": "const app = require('./app');\n\napp.listen(3000);\n",
    "src/app.js": (
        "const express = require('express');\n"
        "const router = require('./router');\n\n"
        "const app = express();\n"
        "app.use(router);\n"
        "module.exports = app;\n"
    ),
    "src/router.js": (
        "const express = require('express');\n"
        "const router = express.Router();\n\n"
        "router.get('/health', (req, res) => res.json({ ok: true }));\n"
        "module.exports = router;\n"
    ),
}


class MockProvider(LLMProvider):
    """按调用次数返回预设的流式 chunk。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatCompletionChunk]:
        self.calls += 1
        if self.calls == 1:
            yield ChatCompletionChunk(
                tool_calls=[
                    ToolCallChunk(id="call-list", name="list", arguments={"path": "src"})
                ],
                finish_reason="tool_calls",
                usage=UsageDelta(total_tokens=120),
            )
        elif self.calls == 2:
            yield ChatCompletionChunk(
                tool_calls=[
                    ToolCallChunk(
                        id="call-search",
                        name="search",
                        arguments={"query": "router", "path": "src/"},
                    )
                ],
                finish_reason="tool_calls",
                usage=UsageDelta(total_tokens=130),
            )
        else:
            yield ChatCompletionChunk(
                content="好的，src/ 里有 3 个文件，其中 src/app.js 和 src/router.js 涉及 router。",
                finish_reason="stop",
                usage=UsageDelta(total_tokens=200),
            )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="devflow-mock-loop-") as tmp:
        sandbox = SandboxService(Path(tmp) / "workspaces")
        container_id = sandbox.create_session_container("session-mock-demo")
        for relative_path, content in FILES.items():
            sandbox.write_file(container_id, relative_path, content)

        loop = AgentLoop(provider=MockProvider(), tools=ToolRegistry(), sandbox=sandbox)
        async for event in loop.run_stream(
            "mock-session",
            "帮我看看 src 目录里有没有 router",
            session_workspace_path=container_id,
        ):
            print(event.to_sse_str(), end="")


if __name__ == "__main__":
    asyncio.run(main())
