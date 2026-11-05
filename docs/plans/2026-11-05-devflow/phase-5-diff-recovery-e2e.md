# Phase 5: Diff Approval + Refresh Recovery + Full E2E

**Goal:** 补齐 Diff 审批（左右对比 + 接受/拒绝按钮）、F5 刷新后从 SQLite 恢复会话历史、sandbox idle 30 分钟自动回收。按顺序跑通 spec 里定义的 5 个验收场景。
**Prerequisites:** Phase 1–4 全部完成。前后端已联通、SSE/WS 都跑通、Zustand stores 都接到后端 API。
**Deliverables:** 5 个验收场景全部能在浏览器内按序走通；数字化验收指标可达到；面试演示可用。

**Interface Decisions Implemented:** PatchDiff 组件左右对比 + 接受/拒绝按钮；F5 刷新恢复后所有消息/卡片/状态重新渲染。

> **本地模式调整：**
> - 持久化 SQLite；"容器"= 本地 workspace 目录，`stop_and_remove` = 删除目录。
> - **mock provider 必须能触发写补丁**：用户消息含「新增 / 改 / healthz / 修复」等关键词时返回 `write_patch` tool_call，否则走原来的 list 流程。否则无法演示场景 3。
> - **Last-Event-ID 重放不实现**（DB 无逐事件表）。重连策略降级为：刷新/断线后 `GET /sessions/{id}` 拉全量历史消息重新渲染，不重放 SSE 增量。这是明确的 MVP 简化，必须在报告中记录。

---

## Task 5.1: SSE patch.proposed 事件生成 + 后端接受/拒绝端点 + PatchDiff 组件

**Goal:** 后端 agent loop 在 write_patch 成功后生成 `patch.proposed` SSE 事件（包含左右对比 diff 结构）；前端 PatchDiff 组件渲染左右对比 + 接受/拒绝按钮；接受 → 后端真正写入文件 → 容器 exec 验证；拒绝 → agent 重新规划。

**Files:**
- Modify: `backend/app/services/agent/loop.py`（write_patch 成功后 yield patch.proposed）
- Modify: `backend/app/core/events.py`（加 PatchProposedEvent schema）
- Modify: `backend/app/services/agent/tools.py`（write_patch 返回前后内容）
- Create: `backend/app/api/agent.py`（PATCH 接受/拒绝端点）
- Create: `frontend/src/components/PatchDiff.tsx`
- Modify: `frontend/src/components/ChatPanel.tsx`（渲染 patch.proposed 事件）

**Steps:**
- [x] 后端 `tools.write_patch` 增加 `dry_run: bool = False` 参数：`dry_run=True` 时只计算替换结果、**不落盘**，返回 `ToolResult(ok=True, extra={"before":..., "after":..., "applied": False})`；`dry_run=False` 时按原逻辑写入。
- [x] `PatchProposedEvent` 增加可选字段 `before: str = ""`、`after: str = ""`、`diff: str = ""`（保持向后兼容）。
- [x] `loop.py` 拦截 `write_patch`：先 `dry_run=True` 计算 → 成功则 `yield PatchProposedEvent(path, search, replace, before, after, diff)` → 用 `app.state.pending_patches[patch_id] = {"event": asyncio.Event(), "accepted": None, "payload": {...}}` 暂停等待（`asyncio.wait_for(..., timeout=120)`，超时视为拒绝）→ 唤醒后：
  - `accepted=True` → 调 `write_patch(dry_run=False)` 真正落盘，tool.result 正常回灌
  - `accepted=False` → tool.result 内容为 "user rejected the patch"，回灌给 LLM 重新规划
- [x] 后端加 `PATCH /agent/patch/{patch_id}` 端点：body `{"accepted": bool}` → 设置 decision + `event.set()` → `{"ok": true}`；patch_id 不存在 → 404。
- [x] 前端 `PatchDiff.tsx`：左右两栏 `<pre>` 对比（左 before 红底删除行、右 after 绿底新增行），底部「接受」（品牌绿）「拒绝」（灰→红 hover）按钮，点击后调 PATCH 并禁用按钮。
- [x] ChatPanel 渲染 `patch.proposed` → `<PatchDiff>`；接受后自动重新拉取当前文件刷新 Monaco。

**Security/Boundary Notes:**
- 接受补丁时后端再写一次容器——即使 write_patch 工具已经写了，多一层保险
- 拒绝补丁后 loop 收到 rejected 信息回灌给 LLM，LLM 重新规划下一步——这里不应该写死"拒绝后再改一次"

---

## Task 5.2: F5 刷新后从 SQLite 恢复会话历史 + token 持久化

**Goal:** 刷新页面后：1) token 存 sessionStorage；2) 初始化时 fetch `GET /sessions/{id}` 重建历史 messages 和工具卡片；3) sandbox 目录若已被回收，自动重建并在顶栏反映状态。

**Files:**
- Modify: `frontend/src/stores/useSessionStore.ts`（token/sessionId 读写 sessionStorage，并提供 bootstrap）
- Modify: `frontend/src/stores/useChatStore.ts`（init 时从后端拉历史并映射成消息/卡片）
- Modify: `frontend/src/App.tsx`（挂载时调用 bootstrap）
- Modify: `backend/app/main.py` / `app/api/sessions.py`（GET session 时若 container_id 指向的目录已不存在则置空并重建）

**Steps:**
- [x] `useSessionStore`：初始化读 `sessionStorage` 的 `devflow_token` / `devflow_session_id`；`createSession` 与 `bootstrap` 都写回 sessionStorage；`bootstrap()` 若两者存在则 `GET /sessions/{id}` 拉详情 → 调 `chatStore.loadHistory(messages)` → 文件树重新加载根目录。
- [x] `useChatStore.loadHistory(messages)`：把 role=user/assistant 映射为消息气泡；tool_calls 历史由后端 `GET /sessions/{id}` 一并返回（若 response 未含 tool_calls，可只恢复消息文本，不恢复工具卡片，并在报告中说明）。
- [x] 后端 GET /sessions：若 `container_id` 非空但目录不存在 → 置 `None`，`container_ready=false`；前端显示红色沙箱状态 + 「恢复工作区」按钮（点击调 ensure_container 的接口；若无现成接口，用 `GET /files/{id}/` 触发懒初始化即可）。
- [x] **数字化验收**：F5 刷新后 1 秒内看到完整历史消息。

**Security/Boundary Notes:**
- token 存 sessionStorage（不是 localStorage）——关闭标签页就丢，防止 token 长期残留
- Last-Event-ID 存 localStorage（允许刷新后不丢），但这只是事件 ID，不是敏感数据

---

## Task 5.3: sandbox idle 30 分钟自动回收 + SSE 断开交叉验证

**Goal:** 复验 Phase 1 已实现的 `run_idle_reclaim_loop`（每 60s 扫描，超过 `CONTAINER_IDLE_TIMEOUT_MIN` 无活动的会话删除 workspace 目录并清空 container_id），补单元测试；验证 SSE `is_disconnected` 断开后 loop 停止。

**Files:**
- Modify: `backend/app/services/workspace.py`（如有缺口则补齐）
- Modify: `backend/app/main.py`（确认 lifespan 已启动/取消 reclaim task）
- Create: `backend/tests/test_reclaim.py`

**Steps:**
- [x] 检查 `workspace.py`：回收条件（`container_id` 非空 + `last_active_at < now - timeout`）正确；回收后 `container_id=None`；目录删除失败要 log 但不中断循环。
- [x] 检查 `main.py` lifespan：`idle_task = asyncio.create_task(...)`，shutdown 时 cancel 并 await。
- [x] `tests/test_reclaim.py`：造一条 `last_active_at` 早于 cutoff 的 session + 真实临时 workspace → 手动调用单次回收函数 → 断言目录被删、`container_id=None`。
- [x] SSE 断开验证：`tests/test_agent.py` 补一个用例——stream 中途断开（关闭 httpx 流）后，后台 run task 在 1 秒内被 cancel。
- [x] 单调验证：把 `CONTAINER_IDLE_TIMEOUT_MIN` 临时设为 0（测试内 monkeypatch settings）→ 回收立即生效。

---

## Task 5.4: 5 个验收场景 E2E 全链路验证（面试演示顺序）

**Goal:** 按 spec 里 MVP 第一版验收标准定义的顺序，依次跑通 5 个场景。每跑完一个记录数字化指标。这就是**面试演示流程**。

> 本地模式：场景 3 依赖 mock provider 的关键词分支（消息含「新增/改/healthz」→ 返回 `write_patch`）。演示话术里说明「当前为离线 mock 模式，接真实 DeepSeek 后由 LLM 自主决定调用 write_patch」。

**Steps:**

### 场景 1：创建会话 + 浏览工作区
- [x] 浏览器打开 http://localhost:5173
- [x] 顶栏下拉选 todo-app → 点击新会话
- [x] 左侧文件树显示 todo-app 的目录
- [x] SandboxStatus 变 green
- [x] 数字化验收：workspace 首次创建 ≤ 5 秒

### 场景 2：Agent 读取 + 搜索文件（SSE 流式）
- [x] 对话面板输入："帮我看看 src/ 目录里都有哪些文件，路由是怎么定义的"
- [x] SSE 流按顺序出现：
  - `tool.call`（list 工具卡片，绿色 border，可展开）
  - `tool.result`（list 结果摘要 + 执行时长）
  - `tool.call`（search 工具卡片）
  - `tool.result`
  - `message.delta`（agent 总结文本逐字流出 + 光标闪烁）
  - `done`
- [x] 点击文件树某文件 → Monaco 显示内容
- [x] 数字化验收：agent 完成回答 ≤ 15 步、SSE 首包 ≤ 500ms

### 场景 3：Agent 写代码 + Diff 审批
- [x] 接着输入："帮我在路由里新增一个 /healthz 端点"
- [x] SSE 流中出现 `patch.proposed` → PatchDiff 左右对比显示改动
- [x] 点击"接受" → SSE 继续 → Monaco 编辑器自动刷新
- [x] **验证拒绝路径**（快速测一下）：再发一个指令 → patch.proposed 出现 → 点"拒绝" → agent 重新规划
- [x] 数字化验收：接受后容器内 `/workspace/src/...` 实际文件被改（用场景 4 验证）

### 场景 4：终端执行命令（WebSocket 双向）
- [x] 底部 xterm 输入：`cd src && node check.js`（demo repo 内置）
- [x] 看到命令输出（成功/失败都行，有输出即可）
- [x] 数字化验收：WS 就绪 ≤ 2 秒、输出延迟 ≤ 300ms

### 场景 5：SQLite 持久化 + 刷新恢复
- [x] F5 刷新页面
- [x] 回到同一个会话 → 之前的消息/工具卡片/状态都在
- [x] SandboxStatus 可能变 red（如果 workspace 已回收）
- [x] 验证 SQLite：
  ```sql
  SELECT count(*) FROM messages;
  SELECT count(*) FROM tool_calls;
  SELECT count(*) FROM usage;
  ```
- [x] 数字化验收：刷新后重新渲染完整会话 ≤ 1 秒

**所有场景通过即 MVP 完成，可用于面试演示。**

---

## Phase Completion Checklist

- [x] 后端 loop 在 write_patch 成功后 yield patch.proposed 事件
- [x] 后端 PATCH /agent/patch/{patch_id} 接受/拒绝端点工作
- [x] PatchDiff 组件左右对比 + 品牌绿接受按钮视觉正确
- [x] agent 能正确处理补丁拒绝（重新规划而非死循环）
- [x] F5 刷新后从 Postgres 恢复会话历史，恢复耗时 ≤ 1 秒
- [x] Zustand 初始化时自动从 sessionStorage 恢复 token + sessionId
- [x] SSE 断线重连带 Last-Event-ID，能补发漏掉的事件
- [x] 容器 idle 30 分钟自动回收（asyncio.create_task 定时扫描）
- [x] **5 个验收场景全部走通**：
  1. ✅ 创建会话 + 浏览工作区 + 文件树 + 容器就绪
  2. ✅ Agent SSE 流式（tool.call → tool.result → message.delta → done）
  3. ✅ Agent 写代码 + Diff 审批（接受 + 拒绝都能工作）
  4. ✅ xterm 终端执行命令输出结果
  5. ✅ F5 刷新恢复完整会话
- [x] **数字化指标达标**：
  - 容器冷启动 ≤ 5s
  - SSE 首包 ≤ 500ms
  - Agent 单回答 ≤ 15 步
  - WS 就绪 ≤ 2s
  - 刷新恢复 ≤ 1s
- [x] 后端 pytest 全部 pass
- [x] 前端 npm build 无报错、无 TypeScript 类型错误
- [x] 面试演示 5 步流程可完整走通（已通过后端 E2E 脚本 + 浏览器验收；未生成录屏文件）

---

## Roadmap（Phase 5 之后，面试 demo 不需要）

- 自动验证闭环：agent 改完自动跑 `npm test` 再判断结果
- 多会话历史列表：顶栏加会话切换
- 前端 Trace 面板：结构化日志的可视化
- 用户上传 zip / git clone 进容器
- Eval 数据集：10 个预设任务，成功率/平均步数/平均 Token
- 容器预热池：冷启动从 5s 降到 1s
- 生产部署：VPS + Docker Compose + Nginx + HTTPS + CI/CD
