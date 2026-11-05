# Phase 3: API Routes + SSE + WS + Persistence

**Goal:** 所有 FastAPI 路由 + SSE 流式 agent endpoint + WebSocket 终端 endpoint + SQLite 事件持久化。能用脚本完整触发 session 创建 → POST run → 看到 SSE 事件流出来 → 重启后端仍能从 SQLite 恢复会话历史。
**Prerequisites:** Phase 1 + 2 全部完成。SandboxService、WorkspaceService、AgentLoop、Provider、4 工具、SQLite 4 表全部就绪。
**Deliverables:** 完整可用的 FastAPI app，所有 spec 里定义的 API 路由都实现；SSE 端点事件格式与 `core/events.py` 一致；每 run 结束后 SQLite 中有 messages + tool_calls + usage 记录。

**Interface Decisions Implemented:** 无界面改动（这阶段只做后端路由 + 协议）。

> **本地模式调整：**
> - 持久化用 SQLite（不是 Postgres）。
> - 文件路由直接用 `SandboxService.list_files / read_file / write_file`，**不要**用 `test -d` / `cat` / heredoc 等 shell 命令。
> - 终端用本地持久 PowerShell 子进程（stdin/stdout 管道）实现，接口与 Docker hijack 版一致；若持久进程在 Windows 上不可靠，退回"每条命令一次 `sandbox.exec`"并自行维护 cwd。

---

## Task 3.1: 所有 schemas（Pydantic request/response）+ JWT dependency + 中间件

**Goal:** 所有 API 端点的请求/响应 schema 一次性定义好；JWT 校验做成 FastAPI dependency；CORS 中间件允许 `http://localhost:5173`（前端 dev server）。

**Files:**
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/session.py`
- Create: `backend/app/schemas/message.py`
- Create: `backend/app/schemas/agent.py`
- Create: `backend/app/schemas/file.py`
- Modify: `backend/app/main.py`（加 CORS 中间件、JWT dependency）

**Steps:**
- [x] 写所有 Pydantic schemas：
  ```python
  # schemas/session.py
  class SessionCreate(BaseModel):
      demo_repo: str  # "todo-app" | "express-demo" | "python-flask-demo"
  class SessionResponse(BaseModel):
      id: UUID
      demo_repo: str
      container_ready: bool
      created_at: datetime

  # schemas/message.py
  class MessageResponse(BaseModel):
      id: UUID
      role: str
      content: str
      created_at: datetime

  # schemas/agent.py
  class AgentRunRequest(BaseModel):
      message: str

  # schemas/file.py
  class FileWriteRequest(BaseModel):
      content: str
  ```
- [x] 写 JWT FastAPI dependency（`Depends(get_current_session)`）：从 `Authorization: Bearer {token}` 提取 → verify_session_token → 返回 session_id。
- [x] 加 CORS 中间件：
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["http://localhost:5173", "http://localhost:5174"],
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```
- [x] 加全局异常 handler：路径校验 ValueError → HTTP 403，容器错误 → HTTP 503。

---

## Task 3.2: api/sessions.py + api/files.py — CRUD + 路径校验 + 懒初始化

**Goal:** 会话 CRUD 路由 + 文件树/读写路由。容器懒初始化在 `GET /sessions/{id}` 或 `POST /sessions/{id}/run` 时触发（而不是在 session 创建时）。文件路由的所有 path 必须过 `validate_workspace_path`。

**Files:**
- Create: `backend/app/api/sessions.py`
- Create: `backend/app/api/files.py`

**Steps:**
- [x] 写 sessions 路由：
  ```python
  router = APIRouter(prefix="/sessions", tags=["sessions"])

  @router.post("", response_model=SessionResponse)
  async def create_session(body: SessionCreate, request: Request, db: AsyncSession = Depends(get_db)):
      # 1. Postgres 建 session 记录
      # 2. 签发 JWT token（存到 response 里）
      # 3. container_ready = False（懒初始化，此时还不起容器）
      return SessionResponse(..., token=token)

  @router.get("/{session_id}", response_model=SessionDetailResponse)
  async def get_session(session_id: UUID, request: Request, db: AsyncSession = Depends(get_db),
                        current_session: UUID = Depends(get_current_session)):
      # JWT verify 后 current_session 必须等于 path session_id
      # 从 Postgres 读 session + 所有 messages
      # 返回 container_ready 状态（查 container_id 是否在 session 记录里）

  @router.delete("/{session_id}")
  async def delete_session(session_id: UUID, request: Request, ...):
      # 触发 workspace.stop_and_remove_container(session_id)
      # Postgres 保留 session 和消息（软删除）
  ```
- [x] 写 files 路由（注意 URL path 里有动态 path segment）：
  ```python
  router = APIRouter(prefix="/files", tags=["files"])

  @router.get("/{session_id}/{path:path}")
  async def get_file_or_dir(session_id: UUID, path: str, ...):
      # 1. validate_workspace_path(path) — ValueError → 403
      # 2. workspace.ensure_container(session_id) — 懒初始化
      # 3. sandbox.list_files(container_id, path) 成功 → 返回 {"type": "dir", "entries": [...]}
      # 4. 否则 sandbox.read_file(container_id, path) → 返回 {"type": "file", "content": ..., "truncated": bool}
      # 5. 两者都失败 → 404

  @router.post("/{session_id}/{path:path}")
  async def write_file(session_id: UUID, path: str, body: FileWriteRequest, ...):
      # 1. validate_workspace_path(path)
      # 2. ensure_container(session_id)
      # 3. sandbox.write_file(container_id, path, body.content)
      # 4. 返回 200 {"ok": true, "path": path}
  ```
  > 本地模式不再需要 heredoc / docker cp：`SandboxService.write_file` 直接写文件。

**Security/Boundary Notes:** `GET /files/{session_id}/{path:path}` 里 FastAPI 的 path parameter 会把整个 path segment （包括 `/`）捕获进来。**必须** `validate_workspace_path` 处理。

---

## Task 3.3: api/agent.py — Agent run 路由 + SSE 流式端点（核心）

**Goal:** 两个端点配合使用——`POST /sessions/{id}/run` 触发 agent 并返回一个 `stream_id`（其实就是 session_id 本身）；`GET /agent/stream/{session_id}` 返回 FastAPI StreamingResponse，SSE 格式，实时推事件。前端先 POST 触发，再连 GET 接收流。后端 loop 内部每产生一个事件：1) 写入 Postgres；2) yield 到 SSE 流。

**Files:**
- Create: `backend/app/api/agent.py`

**Steps:**
- [x] 写 POST run 端点（简单同步确认 + 返回 run_id）：
  ```python
  @router.post("/sessions/{session_id}/run")
  async def run_agent(session_id: UUID, body: AgentRunRequest, request: Request, ...):
      # 1. workspace.ensure_container(session_id) — 懒初始化
      # 2. 写一条 user message 到 SQLite
      # 3. 记录 run_id（uuid）
      # 4. 启动一个 asyncio.create_task 跑 agent loop（后台跑，不要阻塞这个请求）
      # 5. 返回 {"run_id": str(run_id)}
  ```
- [x] **Provider 注入（本地模式必需）**：`main.py` lifespan 里根据 `settings.llm_provider` 选择 provider：
  - `deepseek`（默认，当配置了 `DEEPSEEK_API_KEY` 时）→ `DeepSeekProvider`
  - `mock`（当没有 API key 时自动降级，或显式 `LLM_PROVIDER=mock`）→ `MockProvider`
  `MockProvider` 放 `app/services/agent/mock_provider.py`，行为：第一轮返回 `list(path=self.mock_target_dir)` 的 tool_call（若历史里没有 tool 消息），第二轮返回固定中文总结文本。这样无 API key 也能完整演示 SSE 全链路。同时暴露 `app.state.provider`，测试可覆盖。
- [x] 写 SSE 流端点（FastAPI StreamingResponse）：
  ```python
  @router.get("/agent/stream/{session_id}")
  async def stream(session_id: UUID, request: Request, ...):
      async def event_generator():
          # 1. 检查 loop 是否已跑完 / 正在跑（用 asyncio.Queue 或简单状态 dict 管理）
          # 2. 如果正在跑 → 从 queue 里不断 yield 事件
          # 3. 每 yield 一个事件：
          #    a. 写 Postgres（messages / tool_calls 表）
          #    b. 格式化 SSE string：event.type + event.data JSON
          #    c. yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"
          #    d. await asyncio.sleep(0) — 让出控制权
          # 4. 当收到 done 事件 → 写 usage 表 → break
          # 5. 检查客户端是否已断开（await request.is_disconnected()）→ 如果断开停止推送
          # 6. 处理重连：check Last-Event-ID header → 从 Postgres 重读已发出的事件补发

      return StreamingResponse(event_generator(), media_type="text/event-stream")
  ```
- [x] 写 `_loop_state` 管理：用一个 app.state dict `{session_id: {"task": asyncio.Task, "queue": asyncio.Queue, "run_id": str}}` 跟踪每个 session 的 loop 状态。POST run 时 put 进去，SSE 端点从 queue 读，loop 结束后清理。
- [x] 实现 Postgres 事件持久化：
  - `tool.call` → 往内存 queue 放，等 `tool.result` 出来一起写 `tool_calls` 表
  - `tool.result` → 写 `tool_calls` 表（ok/result_json/duration_ms）
  - `message.delta` → 累计成 assistant message，done 时写 `messages` 表
  - `patch.proposed` → 写 `messages` 表（role=assistant, content=patch_json）
  - `done` → 写 `usage` 表（total_tokens, total_steps）

**Security/Boundary Notes:** SSE 端点必须 JWT verify。`request.is_disconnected()` 必须每 100ms 检查一次——如果浏览器断开，后端不能继续跑 loop 占资源。

---

## Task 3.4: api/terminal.py — WebSocket 终端端点（本地模式）

**Goal:** WebSocket 端点 `/terminal/ws/{session_id}`。FastAPI accept → ensure_container → 建立本地持久 PowerShell 子进程（cwd = 会话 workspace）→ 前端 ↔ FastAPI ↔ 子进程 stdin/stdout 双向转发。

**Files:**
- Create: `backend/app/api/terminal.py`
- Modify: `backend/app/services/sandbox.py`（新增 `open_shell(container_id)` 上下文管理器）

**Steps:**
- [x] 先在 `sandbox.py` 加 `async def open_shell(container_id)`：用 `asyncio.create_subprocess_exec` 启动 `powershell -NoProfile -NoLogo -Command -`（stdin=PIPE, stdout=PIPE, stderr=STDOUT, cwd=workspace），返回 `(process, reader, writer)`。失败时抛 `RuntimeError`。Docker 版预留同名方法（注释说明未来用 `exec_shell` + hijack）。
- [x] 写 WebSocket 端点：
  ```python
  @router.websocket("/terminal/ws/{session_id}")
  async def terminal_ws(websocket: WebSocket, session_id: UUID):
      await websocket.accept()
      # 1. JWT 从 query param 取 token → verify_session_token → 校验等于 path session_id
      # 2. ensure_container(session_id)
      # 3. sandbox.open_shell(container_id)
      # 4. 两个 asyncio Task：
      #    a. 子进程 stdout 读 → websocket.send_text / send_bytes
      #    b. websocket.receive_text → 写子进程 stdin（补 "\n"）
      # 5. 任一侧断开/出错 → 取消两个 task → process.kill() → websocket.close()
  ```
- [x] 若持久 PowerShell 管道在 Windows 上输出不可读（缓冲导致长时间无输出），退回方案：维护每条命令独立 `sandbox.exec`，会话级 cwd 由后端识别 `cd <path>` 命令更新（`cd` 命令返回空输出）。二选一，必须在报告中说明选了哪个及原因。
- [x] 写 `tests/test_terminal_ws.py`：用 `fastapi.testclient.TestClient` 的 `websocket_connect`（带 token query param），发送 `echo hello`，断言收到含 `hello` 的文本；发送非法 token 断言连接被拒。

**Security/Boundary Notes:**
- WebSocket JWT 走 query param（WebSocket 协议限制）：`/terminal/ws/{session_id}?token=xxx`
- 前端发来的内容只作为 stdin 文本，不给前端传入 exec 参数的机会
- 子进程在断开时必须 kill，不能泄漏

---

## Task 3.5: 全链路验证 — 完整 session → run → SSE

**Goal:** 用脚本（PowerShell `Invoke-RestMethod` 或 httpx Python 脚本）完整验证所有路由串起来：创建 session → 得到 token → POST run → 连 SSE stream → 看到事件 → 查 SQLite 有记录 → 重启后端后 GET session 能恢复消息。

**Files:**
- Create: `backend/scripts/e2e_curl_demo.md`（请求清单：curl 等价写法 + PowerShell 等价写法）
- Create: `backend/scripts/e2e_api_demo.py`（可执行的端到端脚本，使用 httpx + mock provider 注入，不依赖真实 LLM key）
- Create: `backend/tests/test_api.py`（pytest + httpx AsyncClient，覆盖所有路由）

**Steps:**
- [x] 按顺序验证（用近似 curl 的 PowerShell 写法）：
  ```powershell
  # 1. 创建 session
  Invoke-RestMethod -Method Post http://localhost:8000/sessions -ContentType 'application/json' -Body '{"demo_repo":"todo-app"}'
  # 返回 {"id","token","demo_repo","container_ready","created_at"}

  # 2. 触发 agent run
  Invoke-RestMethod -Method Post "http://localhost:8000/sessions/{id}/run" -Headers @{Authorization="Bearer $token"} -ContentType 'application/json' -Body '{"message":"帮我看看 src 目录"}'

  # 3. 连 SSE（PowerShell 无原生 SSE，用 python 脚本或 curl.exe -N）
  curl.exe -N "http://localhost:8000/agent/stream/{id}" -H "Authorization: Bearer $token"
  # 预期：event: tool.call / tool.result / message.delta / done

  # 4. 文件树  5. 读文件  6. 写文件  7. GET session 恢复
  ```
- [x] 验证 SQLite 记录（用 python sqlite3 查 4 张表）：
  ```
  SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1;
  SELECT * FROM messages WHERE session_id = ...;
  SELECT * FROM tool_calls WHERE session_id = ...;
  SELECT * FROM usage WHERE session_id = ...;
  ```
- [x] 重启 uvicorn → 重新 `GET /sessions/{id}` → 消息还在。
- [x] 跑全量 pytest，Expected 全部 pass。

---

## Phase Completion Checklist

- [x] FastAPI 启动无报错，`GET /health` 返回 3 个 ok
- [x] `POST /sessions` 创建成功，返回 session_id + JWT token
- [x] `POST /sessions/{id}/run` 触发 agent loop 后台跑
- [x] `GET /agent/stream/{session_id}` 返回正确格式的 SSE 事件流
- [x] SSE 事件类型：tool.call / tool.result / message.delta / done（至少这 4 种）
- [x] SSE 端点正确处理 `request.is_disconnected()`（前端断了后端 loop 会停）
- [x] WebSocket `/terminal/ws/{session_id}` 双向转发工作（本地持久 shell）
- [x] `GET /files` 文件树/读取工作，`POST /files` 写入工作
- [x] SQLite messages + tool_calls + usage 3 张表都有对应记录
- [x] 后端重启后 `GET /sessions/{id}` 能恢复历史消息
- [x] pytest `tests/` 全部 pass
- [x] 前端代码可以直接开始对接（所有 API 格式已稳定）
