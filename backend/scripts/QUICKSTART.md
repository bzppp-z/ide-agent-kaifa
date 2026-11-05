# DevFlow 快速启动与 5 场景演示

本文档对应 Phase 5 验收：Diff 审批、F5 刷新恢复、idle 回收。
默认 **本地模式**（SQLite + 本地 workspace + MockProvider），无需 Docker / Postgres / API key。

## 1. 启动（两个终端）

终端 A（后端，端口 8000）：

```powershell
cd backend
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端 B（前端，端口 5173）：

```powershell
cd frontend
npm run dev
```

打开 http://127.0.0.1:5173 ，右下角/顶栏状态为绿色即 workspace 就绪。
`backend/devflow.db`（SQLite）与 `backend/workspaces/` 会在首次请求时自动创建，均已被 gitignore。

## 2. 5 场景演示步骤

### 场景 1：流式对话 + 工具调用 + 持久化
1. 页面加载后自动创建会话（token/sessionId 存在 sessionStorage）。
2. 输入「帮我看看 src 目录」发送。
3. 观察 SSE：`tool.call` → `tool.result` → 逐字 `message.delta` → `done`；左侧文件树出现 `src/index.js`。

### 场景 2：Diff 审批（接受）
1. 输入「给 src/index.js 新增一个 health 端点」发送。
2. 对话区出现左右两栏的 patch 卡片：左栏红底为修改前，右栏绿底为修改后。
3. 点击「接受」；卡片置为「已接受」，编辑器中的 `src/index.js` 自动重新加载，能看到新增的 `function health()`。
4. 后端行为：dry_run 计算 before/after → 发 `patch.proposed` 并暂停 → 收到 `PATCH /agent/patch/{patch_id} {"accepted": true}` → 真正写盘 → 继续流。

### 场景 3：Diff 审批（拒绝）
1. 输入「再修复一个 healthz 端点」发送。
2. 出现新的 patch 卡片后点击「拒绝」。
3. 文件内容保持不变；该工具结果以 `user rejected the patch` 回灌给 LLM，LLM 继续给出总结。
4. 审批等待 120 秒未响应会被视为拒绝（loop 超时保护）。

### 场景 4：F5 刷新恢复
1. 在场景 2/3 之后按 F5 刷新页面。
2. 页面从 `GET /sessions/{id}` 拉取全量历史，重建所有气泡；已持久化的 `patch.proposed` 事件会还原为审批卡片。
3. token/sessionId 来自 sessionStorage，无需重新建会话；文件树重新加载根目录。
4. 若 workspace 已被回收，`GET /sessions/{id}` 会把失效的 `container_id` 清空并返回 `container_ready=false`，下次操作会自动重建。

### 场景 5：终端 + idle 回收
1. 打开底部终端面板，执行 `echo hello`，应回显输出（WebSocket `/terminal/ws/{session_id}`）。
2. idle 回收：后端每 60 秒扫描一次，清理 `last_active_at` 超过 `CONTAINER_IDLE_TIMEOUT_MIN`（默认 30 分钟）的 workspace，目录删除且 `container_id` 置空。
3. 单元验证：`backend/tests/test_reclaim.py`（含「单条回收抛错不中断循环」）。

## 3. 自动化验收

后端全量测试（141 passed）：

```powershell
cd backend
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m pytest -q
```

Phase 5 端到端脚本（对已启动的后端跑 5 场景，无需前端）：

```powershell
cd backend
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe scripts\e2e_phase5.py --base-url http://127.0.0.1:8000
```

前端验证：

```powershell
cd frontend
npm test        # 35 passed
npm run typecheck
npm run build
```

## 4. Mock 模式与真实 DeepSeek

- `LLM_PROVIDER` 缺省为 `mock`：MockProvider 不访问外网，按剧本返回
  `list(src)`，或当用户消息含「新增/添加/改/修复/healthz/端点」时返回一次
  `write_patch`（在 `src/index.js` 的 `function listTodos() {` 前插入 health 函数）。
- 切换真实模型：在 `backend/.env`（参考 `.env.example`）中设置

```ini
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
```

  没有有效 key 时后端会打印 warning 并自动降级回 MockProvider，不会启动失败。
- 演示数据：`todo-app` / `express-demo` / `python-flask-demo` 三套内置 demo repo，可在顶栏切换。

## 5. 常见问题

- 端口占用：改 `--port` 与前端 `vite` 端口，并同步 `CORS_ORIGINS`（默认 `http://localhost:5173`）。
- 前端白屏/连不上：确认终端 A 的 `/health` 返回 `{"ok": true, "db": true, "sandbox": true}`。
- 想重置演示数据：停止后端，删除 `backend/devflow.db` 与 `backend/workspaces/` 后重启。
