# Phase 2: Agent Kernel

**Goal:** Agent 内核跑起来。Provider adapter + 4 个工具 + agent loop + RecentToolCallTracker 软检测死循环。能用 Python 脚本 mock 一次完整 agent loop（不依赖真实 LLM API 也不依赖前端），看到 tool.call → tool.result → done 的完整 SSE 事件输出。
**Prerequisites:** Phase 1 全部完成——ContainerService、WorkspaceService、Postgres 4 表、FastAPI lifespan 都跑通。
**Deliverables:** 能独立运行的 agent loop 模块（CLI 或 pytest 脚本能触发）、4 个工具全量可用、软检测死循环生效。

**Interface Decisions Implemented:** 无界面改动。

---

## Task 2.1: services/agent/provider.py — LLM Provider adapter

**Goal:** 实现 `LLMProvider` 抽象接口 + DeepSeek 具体实现。接口必须返回 `AsyncIterator` 供 loop 消费流式输出。

**Files:**
- Create: `backend/app/services/agent/provider.py`
- Create: `backend/tests/test_provider.py`

**Steps:**
- [ ] 定义抽象接口：
  ```python
  from abc import ABC, abstractmethod
  from collections.abc import AsyncIterator

  class ChatCompletionChunk(BaseModel):
      content: str | None = None           # text delta
      tool_calls: list[ToolCallChunk] | None = None
      finish_reason: str | None = None      # stop | tool_calls | length
      usage: UsageDelta | None = None

  class LLMProvider(ABC):
      @abstractmethod
      async def chat_completion_stream(
          self,
          messages: list[dict],
          tools: list[dict] | None = None,
          temperature: float = 0.3,
      ) -> AsyncIterator[ChatCompletionChunk]: ...
  ```
- [ ] 写 DeepSeek 实现（httpx 异步流式）：调 `POST https://api.deepseek.com/v1/chat/completions`，`stream=true`，逐行解析 SSE 格式 `data: {...}`，映射为 `ChatCompletionChunk`。
- [ ] 实现 Provider 层重试（指数退避 1s/2s/4s × 3 次）：
  ```python
  async def _with_retry(self, coro_factory, max_retries=3):
      for attempt in range(max_retries):
          try:
              return await coro_factory()
          except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
              if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 401:
                  raise  # key 过期不可重试
              await asyncio.sleep(2 ** attempt)
      raise
  ```
- [ ] 写 `test_provider.py`：mock httpx.AsyncClient.stream，返回固定 SSE 数据，验证 `ChatCompletionChunk` 解析正确。

**Security/Boundary Notes:** API key 只从 `core.config.settings.DEEPSEEK_API_KEY` 读取，**绝不出现在日志、测试代码、Postgres**。

---

## Task 2.2: services/agent/tools.py — 4 个工具实现 + ToolResult schema

**Goal:** 4 个工具（list / read / search / write_patch）实现，每个工具的入参必须先过 `core.security.validate_workspace_path` 才能进容器 exec。工具返回统一 `ToolResult(ok: bool, summary: str, details: str | None, duration_ms: int)`。

**Files:**
- Create: `backend/app/services/agent/tools.py`
- Create: `backend/tests/test_tools.py`

**Steps:**
- [ ] 定义工具 schema（Pydantic + 函数签名 + tool description）：
  ```python
  # 每个工具 = (function, json_schema_for_llm_prompt)
  TOOLS = [
      # list(path: str) → 列出目录内容
      {"name": "list", "description": "列出指定目录下的文件和子目录",
       "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "相对路径"}}, "required": ["path"]}},
      # read(path: str) → 读取文件内容（限制 2000 行，超过截断）
      {"name": "read", ...},
      # search(query: str, path: str) → ripgrep 搜索
      {"name": "search", ...},
      # write_patch(path: str, search: str, replace: str) → 单次 search/replace
      {"name": "write_patch", ...},
  ]
  ```
- [ ] 实现 `list`：`validate_workspace_path` → `container.exec("ls -la <normalized_path>")` → 解析输出 → 返回。
- [ ] 实现 `read`：`validate_workspace_path` → `container.exec("cat <path> | head -n 2000")` → 截断后返回。
- [ ] 实现 `search`：`validate_workspace_path` → `container.exec("cd /workspace && rg -n '<query>' <path> | head -n 100")` → 返回匹配行。
- [ ] 实现 `write_patch`：
  ```python
  # 1. validate_workspace_path(path)
  # 2. container.exec("cat <path>") 拿到当前文件内容
  # 3. Python 本地做一次 search/replace，记录 old 出现次数
  #    - 如果 search 出现 0 次 → ToolResult(ok=False, summary="pattern not found")
  #    - 如果出现 >1 次 → ToolResult(ok=False, summary="ambiguous match") — 强制精确替换
  # 4. 如果替换成功 → container.exec 用 heredoc 写回去
  # 5. 返回 ToolResult(ok=True, summary=f"patched {path}")
  ```
  **这里不做 diff 生成**——diff 由 agent loop 在 write_patch 成功后从容器 exec cat 读回来做对比（Phase 3 的 SSE patch.proposed 事件里生成）。
- [ ] 写 `test_tools.py`：mock ContainerService.exec 返回固定内容，验证 4 个工具的路径校验和返回结构。

**Security/Boundary Notes:**
- 所有工具入参 **强制先过** `validate_workspace_path`，**绝不用 try/except 吞掉异常**
- `read` 限制 2000 行，`search` 限制 100 条匹配——防止模型让工具产出超大结果占满 token
- `write_patch` 做搜索计数，多个匹配拒绝执行——防止意外破坏

---

## Task 2.3: services/agent/loop.py — Agent 主循环 + RecentToolCallTracker 软检测

**Goal:** Agent 主循环核心实现。输入：session_id + user_message → 输出：AsyncIterator[SSEEvent]。循环逻辑：构造 system prompt + 历史消息 + tools → Provider 流式 LLM → 若 tool_call → tools.call → 结果回灌 → 循环；若 finish_reason=stop 或 token 用尽 → done。

**Files:**
- Create: `backend/app/services/agent/loop.py`
- Create: `backend/app/services/agent/context.py`（上下文压缩，间接实现：token 超限时截断历史）
- Create: `backend/tests/test_loop.py`（mock LLM，验证循环）

**Steps:**
- [ ] 写 AgentLoop 类：
  ```python
  class AgentLoop:
      SYSTEM_PROMPT = """
          你是 DevFlow 的 AI coding agent。工作目录是 /workspace。
          你可以使用以下工具：{tool_descriptions}
          规则：
          - 路径必须是 /workspace 下的相对路径
          - 一次工具调用只处理一个文件的修改
          - 如果需要改多个文件，依次调用工具
          - 使用中文回复用户
      """

      async def run_stream(
          self,
          session_id: str,
          user_message: str,
          provider: LLMProvider,
          tools: ToolRegistry,
          container: ContainerService,
          session_workspace_path: str,
          max_steps: int = 25,
          max_tokens: int = 200000,
      ) -> AsyncIterator[SSEEvent]:
          tracker = RecentToolCallTracker(window=3)
          messages = await self._build_messages(session_id, user_message)
          tokens_used = 0
          steps = 0

          while steps < max_steps and tokens_used < max_tokens:
              chunks = []
              tool_calls = []
              async for chunk in provider.chat_completion_stream(messages, tools.to_schema()):
                  yield SSEEvent(message.delta, {"content": chunk.content or ""})
                  if chunk.tool_calls:
                      tool_calls.extend(chunk.tool_calls)
                  if chunk.usage:
                      tokens_used += chunk.usage.total_delta or 0
                  if chunk.finish_reason:
                      break

              if tool_calls:
                  # 构造 assistant tool_calls message
                  messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
                  for tc in tool_calls:
                      # yield SSEEvent tool.call
                      yield SSEEvent("tool.call", {"tool": tc.name, "input": tc.args})
                      # 执行
                      start = time.time()
                      result = await tools.call(tc.name, tc.args, container, session_workspace_path)
                      duration_ms = int((time.time() - start) * 1000)
                      # 软检测：同一工具 + 同一入参 hash 连续 tracker.window 次
                      call_hash = tracker.record(tc.name, tc.args)
                      if tracker.is_stuck():
                          # 注入提示词
                          messages.append({"role": "system", "content": f"你连续 3 次对 {tc.name} 使用了相同的入参。换个策略吧。"})
                      # yield tool.result
                      yield SSEEvent("tool.result", {**result.model_dump(), "duration_ms": duration_ms})
                      messages.append({"role": "tool", "tool_call_id": tc.id, "content": result.summary})
                  steps += 1
              else:
                  # 模型自然结束
                  yield SSEEvent("done", {"totalTokens": tokens_used, "totalSteps": steps})
                  return

          # 超出限制
          reason = "max_steps" if steps >= max_steps else "max_tokens"
          yield SSEEvent("done", {"totalTokens": tokens_used, "totalSteps": steps, "warning": f"stopped: {reason}"})
  ```
- [ ] 写 `RecentToolCallTracker`：窗口 3 个 `(tool_name, json.dumps(args, sort_keys=True))` 的 hash。`record` → 入队；`is_stuck` → 窗口内所有 hash 相同则 True。
- [ ] 写 `context.py`（简化版）：当 `messages` 总 token 数估算接近上限时，**只截断早期的 tool.result 消息**（保留 system prompt 和最近 N 轮），不截断用户消息和 assistant 消息。MVP 阶段不做真正的压缩重写，简单截断。

**Security/Boundary Notes:**
- `max_steps=25` 和 `max_tokens=200000` 是**硬限制**，loop 必须强制 break，**不能**依赖 LLM 自己说 stop
- 软检测是额外一层，硬限制是底线

---

## Task 2.4: services/auth.py — JWT + 用量记录 + 单元测试 + 完整 mock loop 验证

**Goal:** 实现 JWT 签发/校验 + 每 run 写 usage 到 Postgres。然后写一个 `scripts/mock_loop_demo.py`，能用 mock LLM + mock Container 完整跑一次 agent loop 并打印 SSE 事件。

**Files:**
- Create: `backend/app/services/auth.py`
- Create: `backend/tests/test_auth.py`
- Create: `backend/scripts/mock_loop_demo.py`

**Steps:**
- [ ] 写 `auth.py`：
  ```python
  from jose import jwt, JWTError
  from passlib.context import CryptContext

  def create_session_token(session_id: UUID, secret: str, expire_hours: int) -> str:
      payload = {"session_id": str(session_id), "exp": datetime.utcnow() + timedelta(hours=expire_hours)}
      return jwt.encode(payload, secret, algorithm="HS256")

  def verify_session_token(token: str, secret: str) -> UUID | None:
      try:
          payload = jwt.decode(token, secret, algorithms=["HS256"])
          return UUID(payload["session_id"])
      except (JWTError, KeyError, ValueError):
          return None

  async def record_usage(db_session, session_id: UUID, run_id: str, total_tokens: int, total_steps: int):
      usage = Usage(session_id=session_id, run_id=run_id, total_tokens=total_tokens, total_steps=total_steps)
      db_session.add(usage)
      await db_session.commit()
  ```
- [ ] 写 `test_auth.py`：create → verify → 过期 → tamper 4 种 case。
- [ ] 写 `scripts/mock_loop_demo.py`（**这是 phase 2 的核心交付验证**）：
  ```python
  # 不连真实 Postgres、不连真实 Docker、不连真实 LLM
  # Mock LLM 依次返回：
  #   chunk 1: tool_call list(path="src") + finish_reason=tool_calls
  #   chunk 2: tool_call search(query="router", path="src/") + finish_reason=tool_calls
  #   chunk 3: finish_reason=stop, content="好的，src/ 里有 3 个文件..."
  # Mock Container.exec 返回固定内容

  async def main():
      loop = AgentLoop(provider=MockProvider(), tools=ToolRegistry(), container=MockContainer())
      async for event in loop.run_stream(mock_session_id, "帮我看看 src 目录"):
          print(event)

  # 预期输出：
  # SSEEvent("tool.call", {"tool": "list", "input": {"path": "src"}})
  # SSEEvent("tool.result", {"ok": True, "summary": "src/ 有 3 个文件", "duration_ms": 12})
  # SSEEvent("tool.call", {"tool": "search", "input": {"query": "router", "path": "src/"}})
  # SSEEvent("tool.result", {"ok": True, ...})
  # SSEEvent("message.delta", {"content": "好的..."})
  # SSEEvent("done", {"totalTokens": 450, "totalSteps": 2})
  ```
- [ ] 运行 mock demo 脚本：
  ```
  C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe backend/scripts/mock_loop_demo.py
  ```
  Expected: 按顺序打印出 tool.call → tool.result → tool.call → tool.result → message.delta → done

---

## Phase Completion Checklist

- [ ] LLMProvider 抽象接口定义完成，DeepSeek 实现的 `chat_completion_stream` 能流式返回 chunks
- [ ] Provider 层重试（指数退避 × 3）实现
- [ ] 4 个工具（list/read/search/write_patch）全部实现，每个入参先过 `validate_workspace_path`
- [ ] `write_patch` 能检测 ambiguous match（>1 个匹配时拒绝执行）
- [ ] AgentLoop 主循环实现，max_steps / max_tokens 硬限制生效
- [ ] RecentToolCallTracker 软检测死循环（连续 3 次相同 hash → 注入提示词）
- [ ] JWT 创建/校验通过单测
- [ ] mock_loop_demo.py 输出完整事件流（tool.call → tool.result → ... → done）
- [ ] pytest `tests/` 下所有新测试 pass
- [ ] 下一 phase 可以直接把 loop 接到 FastAPI SSE 端点上
