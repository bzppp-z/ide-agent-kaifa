# Phase 3: API Routes + SSE + WS + Persistence

**Goal:** 所有 FastAPI 路由 + SSE 流式 agent endpoint + WebSocket 终端 endpoint + Postgres 事件持久化。能用 curl 完整触发 session 创建 → POST run → 看到 SSE 事件流出来 → 刷新后端容器仍能从 Postgres 恢复会话历史。
**Prerequisites:** Phase 1 + 2 全部完成。ContainerService、WorkspaceService、AgentLoop、Provider、4 工具、Postgres 4 表全部就绪。
**Deliverables:** 完整可用的 FastAPI app，所有 spec 里定义的 API 路由都实现；SSE 端点事件格式与 `core/events.py` 一致；每 run 结束后 Postgres 中有 messages + tool_calls + usage 记录。

**Interface Decisions Implemented:** 无界面改动（这阶段只做后端路由 + 协议）。

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
- [ ] 写所有 Pydantic schemas：
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
- [ ] 写 JWT FastAPI dependency（`Depends(get_current_session)`）：从 `Authorization: Bearer {token}` 提取 → verify_session_token → 返回 session_id。
- [ ] 加 CORS 中间件：
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["http://localhost:5173", "http://localhost:5174"],
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```
- [ ] 加全局异常 handler：路径校验 ValueError → HTTP 403，容器错误 → HTTP 503。

---

## Task 3.2: api/sessions.py + api/files.py — CRUD + 路径校验 + 懒初始化

**Goal:** 会话 CRUD 路由 + 文件树/读写路由。容器懒初始化在 `GET /sessions/{id}` 或 `POST /sessions/{id}/run` 时触发（而不是在 session 创建时）。文件路由的所有 path 必须过 `validate_workspace_path`。

**Files:**
- Create: `backend/app/api/sessions.py`
- Create: `backend/app/api/files.py`

**Steps:**
- [ ] 写 sessions 路由：
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
- [ ] 写 files 路由（注意 URL path 里有动态 path segment）：
  ```python
  router = APIRouter(prefix="/files", tags=["files"])

  @router.get("/{session_id}/{path:path}")
  async def get_file_or_dir(session_id: UUID, path: str, ...):
      # 1. validate_workspace_path(path) — ValueError → 403
      # 2. workspace.ensure_container(session_id) — 懒初始化
      # 3. container.exec(f"test -d /workspace/{path} && echo dir || echo file")
      # 4. 如果是目录 → container.exec(f"ls -la /workspace/{path}") → 返回文件树
      # 5. 如果是文件 → container.exec(f"cat /workspace/{path} | head -n 2000") → 返回内容

  @router.post("/{session_id}/{path:path}")
  async def write_file(session_id: UUID, path: str, body: FileWriteRequest, ...):
      # 1. validate_workspace_path(path)
      # 2. ensure_container(session_id)
      # 3. container.write_file(path, body.content) — 用 heredoc 写
      # 4. 返回 200 OK
  ```
- [ ] 实现容器写文件用 heredoc（不需要 docker cp）：
  ```python
  # container.exec(f"cat > /workspace/{normalized_path} << 'DEFFLOW_EOF'\n{content}\nDEFFLOW_EOF")
  ```
  注意 content 里如果有 `DEFFLOW_EOF` 要换分隔符。MVP 阶段固定用这个简单方案，不怕极端 case。

**Security/Boundary Notes:** `GET /files/{session_id}/{path:path}` 里 FastAPI 的 path parameter 会把整个 path segment （包括 `/`）捕获进来。**必须** `validate_workspace_path` 处理。

---

## Task 3.3: api/agent.py — Agent run 路由 + SSE 流式端点（核心）

**Goal:** 两个端点配合使用——`POST /sessions/{id}/run` 触发 agent 并返回一个 `stream_id`（其实就是 session_id 本身）；`GET /agent/stream/{session_id}` 返回 FastAPI StreamingResponse，SSE 格式，实时推事件。前端先 POST 触发，再连 GET 接收流。后端 loop 内部每产生一个事件：1) 写入 Postgres；2) yield 到 SSE 流。

**Files:**
- Create: `backend/app/api/agent.py`

**Steps:**
- [ ] 写 POST run 端点（简单同步确认 + 返回 token）：
  ```python
  @router.post("/sessions/{session_id}/run")
  async def run_agent(session_id: UUID, body: AgentRunRequest, request: Request, ...):
      # 1. workspace.ensure_container(session_id) — 懒初始化
      # 2. 写一条 user message 到 Postgres
      # 3. 记录 run_id（uuid）
      # 4. 启动一个 asyncio.create_task 跑 agent loop（后台跑，不要阻塞这个请求）
      # 5. 返回 {"run_id": str(run_id)}
  ```
- [ ] 写 SSE 流端点（FastAPI StreamingResponse）：
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
- [ ] 写 `_loop_state` 管理：用一个 app.state dict `{session_id: {"task": asyncio.Task, "queue": asyncio.Queue, "run_id": str}}` 跟踪每个 session 的 loop 状态。POST run 时 put 进去，SSE 端点从 queue 读，loop 结束后清理。
- [ ] 实现 Postgres 事件持久化：
  - `tool.call` → 往内存 queue 放，等 `tool.result` 出来一起写 `tool_calls` 表
  - `tool.result` → 写 `tool_calls` 表（ok/result_json/duration_ms）
  - `message.delta` → 累计成 assistant message，done 时写 `messages` 表
  - `patch.proposed` → 写 `messages` 表（role=assistant, content=patch_json）
  - `done` → 写 `usage` 表（total_tokens, total_steps）

**Security/Boundary Notes:** SSE 端点必须 JWT verify。`request.is_disconnected()` 必须每 100ms 检查一次——如果浏览器断开，后端不能继续跑 loop 占资源。

---

## Task 3.4: api/terminal.py — WebSocket 终端端点 + hijack 模式

**Goal:** WebSocket 端点 `/terminal/ws/{session_id}`。FastAPI accept → ensure_container → 用 Docker SDK exec_shell + hijack socket → 前端 ↔ FastAPI ↔ hijack ↔ 容器 shell 双向转发。

**Files:**
- Create: `backend/app/api/terminal.py`

**Steps:**
- [ ] 写 WebSocket 端点：
  ```python
  @router.websocket("/terminal/ws/{session_id}")
  async def terminal_ws(websocket: WebSocket, session_id: UUID):
      await websocket.accept()
      # 1. JWT 从 query param 取（WebSocket 不能带 header）：verify_session_token → session_id
      # 2. ensure_container(session_id)
      # 3. container.exec_shell() — Docker SDK exec create + start + start attach
      #    exec = container.exec_run(["bash"], stdin=True, stdout=True, stderr=True, tty=True, hijack=True, demux=False)
      #    exec_stream = exec.output  # 这是一个 raw TCP socket
      # 4. 开两个 asyncio Task：
      #    a. 从 exec_stream 读 → await websocket.send_text(data) （容器→前端）
      #    b. await websocket.receive_text() → exec_stream.sendall(data) （前端→容器）
      # 5. 任一侧断开 → 取消两个 task → exec.close() → websocket.close()
  ```
- [ ] Docker SDK hijack 模式注意点：exec 必须 `detach=False`，返回的 stream 是原生 socket，需要用 `asyncio.get_event_loop().sock_recv / sock_sendall` 包装成 async。MVP 阶段简单处理，一次只读 4096 bytes。
- [ ] 容器内 shell 用 node 用户（非 root）。如果 `bash` 不存在，fallback 到 `sh`。

**Security/Boundary Notes:**
- WebSocket JWT 走 query param（WebSocket 协议限制）：`/terminal/ws/{session_id}?token=xxx`
- exec 命令固定是 `bash` 或 `sh`，**不能**让前端传入 exec command 参数（防止绕过容器）
- exec stream 在任一侧断开时必须及时 cleanup，不能泄漏连接

---

## Task 3.5: 全链路验证 — 用 curl 触发完整 session → run → SSE

**Goal:** 用 curl（或 httpx 脚本）完整验证 Phase 3 + Phase 2 的所有路由都能串起来：创建 session → 得到 token → POST run → 连 SSE stream → 看到事件 → 查询 Postgres 有记录 → 刷新后端（uvicorn reload）后 GET session 能恢复消息。

**Files:**
- Create: `backend/scripts/e2e_curl_demo.md`（curl 命令清单）
- Create: `backend/tests/test_api.py`（用 pytest + httpx AsyncClient）

**Steps:**
- [ ] 按顺序执行：
  ```bash
  # 1. 创建 session
  curl -X POST http://localhost:8000/sessions \
    -H "Content-Type: application/json" \
    -d '{"demo_repo": "todo-app"}'
  # 返回：{"id": "...", "token": "eyJ...", ...}

  # 2. 触发 agent run（拿到 token）
  curl -X POST http://localhost:8000/sessions/{id}/run \
    -H "Authorization: Bearer {token}" \
    -H "Content-Type: application/json" \
    -d '{"message": "帮我看看 src 目录"}'
  # 返回：{"run_id": "..."}

  # 3. 连 SSE 看事件流
  curl -N http://localhost:8000/agent/stream/{session_id} \
    -H "Authorization: Bearer {token}"
  # 预期按 SSE 格式打印：
  # event: tool.call
  # data: {"tool": "list", "input": {...}}
  # event: tool.result
  # data: {"ok": true, "summary": "...", "duration_ms": 15}
  # event: message.delta
  # data: {"content": "好的..."}
  # event: done
  # data: {"totalTokens": 450, "totalSteps": 2}

  # 4. 容器文件树
  curl http://localhost:8000/files/{session_id}/ -H "Authorization: Bearer {token}"

  # 5. 读文件
  curl http://localhost:8000/files/{session_id}/src/check.js -H "Authorization: Bearer {token}"

  # 6. 写文件（Monaco 保存触发）
  curl -X POST http://localhost:8000/files/{session_id}/src/check.js \
    -H "Authorization: Bearer {token}" \
    -H "Content-Type: application/json" \
    -d '{"content": "console.log(\"hello\")"}'

  # 7. 刷新后端后恢复 session
  curl http://localhost:8000/sessions/{id} -H "Authorization: Bearer {token}"
  # 预期：messages 列表包含刚才的 user message 和 assistant reply
  ```
- [ ] 验证 Postgres 记录：
  ```sql
  SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1;
  SELECT * FROM messages WHERE session_id = ...;
  SELECT * FROM tool_calls WHERE session_id = ...;
  SELECT * FROM usage WHERE session_id = ...;
  ```
- [ ] 重启 uvicorn（--reload 会自动热更）→ 重新 `GET /sessions/{id}` → 消息还在。

---

## Phase Completion Checklist

- [ ] FastAPI 启动无报错，`GET /health` 返回 3 个 ok
- [ ] `POST /sessions` 创建成功，返回 session_id + JWT token
- [ ] `POST /sessions/{id}/run` 触发 agent loop 后台跑
- [ ] `GET /agent/stream/{session_id}` 返回正确格式的 SSE 事件流
- [ ] SSE 事件类型：tool.call / tool.result / message.delta / done（至少这 4 种）
- [ ] SSE 端点正确处理 `request.is_disconnected()`（前端断了后端 loop 会停）
- [ ] WebSocket `/terminal/ws/{session_id}` 双向转发工作（bash + exec + hijack）
- [ ] `GET /files` 容器内文件树/读取工作，`POST /files` 写入工作
- [ ] Postgres messages + tool_calls + usage 3 张表都有对应记录
- [ ] 后端重启后 `GET /sessions/{id}` 能恢复历史消息
- [ ] pytest `tests/` 全部 pass
- [ ] 前端代码可以直接开始对接（所有 API 格式已稳定）
