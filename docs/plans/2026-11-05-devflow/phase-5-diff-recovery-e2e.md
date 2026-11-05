# Phase 5: Diff Approval + Refresh Recovery + Full E2E

**Goal:** 补齐 Diff 审批（左右对比 + 接受/拒绝按钮）、F5 刷新后从 Postgres 恢复会话历史、容器 idle 30 分钟自动回收。按顺序跑通 spec 里定义的 5 个验收场景。
**Prerequisites:** Phase 1–4 全部完成。前后端已联通、SSE/WS 都跑通、5 个 Zustand store 都接到后端 API。
**Deliverables:** 5 个验收场景全部能在浏览器内按序走通；数字化验收指标可达到；面试演示可用。

**Interface Decisions Implemented:** PatchDiff 组件左右对比 + 接受/拒绝按钮；F5 刷新恢复后所有消息/卡片/状态重新渲染。

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
- [ ] 后端 `write_patch` 工具返回时带上 before/after 内容，`loop.py` 里在 write_patch 成功后生成：
  ```python
  # tools.write_patch 返回 ToolResult(ok=True, before="...old content...", after="...new content...")
  # loop.py 里：
  diff_lines = unified_diff(before.splitlines(), after.splitlines())
  yield SSEEvent("patch.proposed", {
      "files": [{"path": target_path, "before": before, "after": after, "diff": diff_lines}],
      "patch_id": str(uuid4()),
  })
  # 此时 loop **暂停**，等待前端 POST /agent/patch/{patch_id} 接受或拒绝
  # 用一个 asyncio.Event 做暂停/唤醒
  loop._pending_patch_events[patch_id] = asyncio.Event()
  await loop._pending_patch_events[patch_id].wait()  # 等待前端响应
  ```
- [ ] 后端加 PATCH 端点：
  ```python
  @router.patch("/agent/patch/{patch_id}")
  async def respond_patch(patch_id: str, body: PatchRespondRequest, ...):
      # body: {"accepted": true|false}
      # accepted=True → 真正执行容器写入（其实 write_patch 工具已经写了，但保险起见再写一次）
      # accepted=False → 记录 rejection，唤醒 loop 让它重新规划
      loop._pending_patch_events[patch_id].set()  # 唤醒 loop
      return {"ok": True}
  ```
- [ ] 前端 `PatchDiff.tsx`：
  ```tsx
  // 左右两栏对比视图
  // 左边 before（红色背景标记删除行），右边 after（绿色背景标记新增行）
  // 使用 shadcn Card + 简单的 <pre> 渲染，MVP 不做真正的 diff lib
  // 底部：[品牌绿边框 + hover 变绿] 接受按钮 | 拒绝按钮（灰色 hover 变红）
  ```
- [ ] ChatPanel 里 `patch.proposed` 事件 → 渲染 `<PatchDiff>` 组件。

**Security/Boundary Notes:**
- 接受补丁时后端再写一次容器——即使 write_patch 工具已经写了，多一层保险
- 拒绝补丁后 loop 收到 rejected 信息回灌给 LLM，LLM 重新规划下一步——这里不应该写死"拒绝后再改一次"

---

## Task 5.2: F5 刷新后从 Postgres 恢复会话历史 + Zustand 初始化 + token 持久化

**Goal:** 刷新页面后——1) token 存 sessionStorage（比 localStorage 好，关闭标签页就丢）；2) Zustand 初始化时 fetch `GET /sessions/{id}` 拿到历史 messages 和 tool_calls；3) 重建 SSE 连接（用 Last-Event-ID 补发漏掉的事件）；4) 恢复后的容器状态能正确反映（重启后容器可能已被回收）。

**Files:**
- Modify: `frontend/src/stores/useSessionStore.ts`（token 读写 sessionStorage）
- Modify: `frontend/src/stores/useChatStore.ts`（init 时从 Postgres 拉历史）
- Modify: `frontend/src/lib/sse.ts`（断线重连带 Last-Event-ID）
- Modify: `backend/app/main.py`（启动时检查容器是否还活着，dead 的标记 container_ready=false）

**Steps:**
- [ ] `useSessionStore`：
  ```ts
  const initialToken = sessionStorage.getItem("devflow_token");
  const initialSessionId = sessionStorage.getItem("devflow_session_id");
  // store 初始化时检查这两个值
  create((set, get) => ({
    token: initialToken,
    id: initialSessionId,
    createSession: async (demoRepo) => {
      const res = await apiFetch("/sessions", { method: "POST", body: JSON.stringify({ demoRepo }) });
      sessionStorage.setItem("devflow_token", res.token);
      sessionStorage.setItem("devflow_session_id", res.id);
      set({ token: res.token, id: res.id });
      // 同时初始化 ChatStore 从后端拉历史
      await get().initFromBackend();
    },
    initFromSession: async () => {
      if (initialSessionId && initialToken) {
        set({ token: initialToken, id: initialSessionId });
        await get().initFromBackend();  // 拉历史 + 重建 SSE
      }
    },
  }));
  ```
- [ ] SSE hook 断线重连 + Last-Event-ID：
  ```ts
  let lastEventId = localStorage.getItem("devflow_last_event_id") || "";
  // 连接时带 Last-Event-ID header
  // 每次事件更新 lastEventId 写入 localStorage
  // 后端 SSE 端点处理：收到 Last-Event-ID 时从 Postgres 补发后续事件
  ```
- [ ] **数字化验收**：F5 刷新后 1 秒内看到完整历史消息 + 工具卡片 + 当前 SSE 重连中的状态。

**Security/Boundary Notes:**
- token 存 sessionStorage（不是 localStorage）——关闭标签页就丢，防止 token 长期残留
- Last-Event-ID 存 localStorage（允许刷新后不丢），但这只是事件 ID，不是敏感数据

---

## Task 5.3: 容器 idle 30 分钟自动回收 + Phase 3 的 SSE request.is_disconnected 交叉验证

**Goal:** FastAPI lifespan 内启动 `asyncio.create_task` 定时扫描所有 active session，超过 30 分钟无消息 → stop + remove 容器；Postgres session 记录更新 container_ready=false。Phase 3 已实现 SSE request.is_disconnected 检测，这里验证两者交叉工作：用户关闭 SSE 后 loop 确实停、容器 idle 到期自动回收。

**Files:**
- Modify: `backend/app/services/workspace.py`（加 idle reclaim loop）
- Modify: `backend/app/main.py`（lifespan 启动 reclaim task）

**Steps:**
- [ ] 在 WorkspaceService 里实现：
  ```python
  async def run_idle_reclaim_loop(self):
      while True:
          await asyncio.sleep(60)  # 每 1 分钟扫一次
          cutoff = datetime.utcnow() - timedelta(minutes=settings.CONTAINER_IDLE_TIMEOUT_MIN)
          async with async_session() as db:
              sessions = await db.execute(
                  select(Session).where(Session.container_id.isnot(None)).where(Session.last_active_at < cutoff)
              )
              for session in sessions.scalars().all():
                  try:
                      await self._container.stop_and_remove(session.container_id)
                      session.container_id = None
                      session.container_ready = False
                      await db.commit()
                  except Exception as e:
                      logger.error(f"Failed to reclaim session {session.id}: {e}")
  ```
- [ ] `main.py` lifespan 里 `yield` 前：
  ```python
  idle_task = asyncio.create_task(app.state.workspace.run_idle_reclaim_loop())
  yield
  idle_task.cancel()
  ```
- [ ] 验证流程：启动容器 → 发一条消息（更新 last_active_at）→ 不操作 30 分钟 → 容器自动 stop + remove → 顶栏 SandboxStatus 变 red + 重试按钮。

---

## Task 5.4: 5 个验收场景 E2E 全链路验证（面试演示顺序）

**Goal:** 按 spec 里 MVP 第一版验收标准定义的顺序，依次跑通 5 个场景。每跑完一个记录数字化指标。这就是**面试演示流程**。

**Steps:**

### 场景 1：创建会话 + 浏览工作区
- [ ] 浏览器打开 http://localhost:5173
- [ ] 顶栏下拉选 todo-app → 点击新会话
- [ ] 左侧文件树显示 todo-app 的目录
- [ ] SandboxStatus 变 green
- [ ] 数字化验收：容器首次启动 ≤ 5 秒（本地 Docker Desktop）

### 场景 2：Agent 读取 + 搜索文件（SSE 流式）
- [ ] 对话面板输入："帮我看看 src/ 目录里都有哪些文件，路由是怎么定义的"
- [ ] SSE 流按顺序出现：
  - `tool.call`（list 工具卡片，绿色 border，可展开）
  - `tool.result`（list 结果摘要 + 执行时长）
  - `tool.call`（search 工具卡片）
  - `tool.result`
  - `message.delta`（agent 总结文本逐字流出 + 光标闪烁）
  - `done`
- [ ] 点击文件树某文件 → Monaco 显示内容
- [ ] 数字化验收：agent 完成回答 ≤ 15 步、SSE 首包 ≤ 500ms

### 场景 3：Agent 写代码 + Diff 审批
- [ ] 接着输入："帮我在路由里新增一个 /healthz 端点"
- [ ] SSE 流中出现 `patch.proposed` → PatchDiff 左右对比显示改动
- [ ] 点击"接受" → SSE 继续 → Monaco 编辑器自动刷新
- [ ] **验证拒绝路径**（快速测一下）：再发一个指令 → patch.proposed 出现 → 点"拒绝" → agent 重新规划
- [ ] 数字化验收：接受后容器内 `/workspace/src/...` 实际文件被改（用场景 4 验证）

### 场景 4：终端执行命令（WebSocket 双向）
- [ ] 底部 xterm 输入：`cd src && node check.js`（demo repo 内置）
- [ ] 看到命令输出（成功/失败都行，有输出即可）
- [ ] 数字化验收：WS 就绪 ≤ 2 秒、输出延迟 ≤ 300ms

### 场景 5：Postgres 持久化 + 刷新恢复
- [ ] F5 刷新页面
- [ ] 回到同一个会话 → 之前的消息/工具卡片/状态都在
- [ ] SandboxStatus 可能变 red（如果容器 idle 已回收）
- [ ] 验证 Postgres：
  ```sql
  SELECT count(*) FROM messages;
  SELECT count(*) FROM tool_calls;
  SELECT count(*) FROM usage;
  ```
- [ ] 数字化验收：刷新后重新渲染完整会话 ≤ 1 秒

**所有场景通过即 MVP 完成，可用于面试演示。**

---

## Phase Completion Checklist

- [ ] 后端 loop 在 write_patch 成功后 yield patch.proposed 事件
- [ ] 后端 PATCH /agent/patch/{patch_id} 接受/拒绝端点工作
- [ ] PatchDiff 组件左右对比 + 品牌绿接受按钮视觉正确
- [ ] agent 能正确处理补丁拒绝（重新规划而非死循环）
- [ ] F5 刷新后从 Postgres 恢复会话历史，恢复耗时 ≤ 1 秒
- [ ] Zustand 初始化时自动从 sessionStorage 恢复 token + sessionId
- [ ] SSE 断线重连带 Last-Event-ID，能补发漏掉的事件
- [ ] 容器 idle 30 分钟自动回收（asyncio.create_task 定时扫描）
- [ ] **5 个验收场景全部走通**：
  1. ✅ 创建会话 + 浏览工作区 + 文件树 + 容器就绪
  2. ✅ Agent SSE 流式（tool.call → tool.result → message.delta → done）
  3. ✅ Agent 写代码 + Diff 审批（接受 + 拒绝都能工作）
  4. ✅ xterm 终端执行命令输出结果
  5. ✅ F5 刷新恢复完整会话
- [ ] **数字化指标达标**：
  - 容器冷启动 ≤ 5s
  - SSE 首包 ≤ 500ms
  - Agent 单回答 ≤ 15 步
  - WS 就绪 ≤ 2s
  - 刷新恢复 ≤ 1s
- [ ] 后端 pytest 全部 pass
- [ ] 前端 npm build 无报错、无 TypeScript 类型错误
- [ ] 面试演示 5 步流程录屏或 GIF 可产出（Phase 5 完成后自然可录）

---

## Roadmap（Phase 5 之后，面试 demo 不需要）

- 自动验证闭环：agent 改完自动跑 `npm test` 再判断结果
- 多会话历史列表：顶栏加会话切换
- 前端 Trace 面板：结构化日志的可视化
- 用户上传 zip / git clone 进容器
- Eval 数据集：10 个预设任务，成功率/平均步数/平均 Token
- 容器预热池：冷启动从 5s 降到 1s
- 生产部署：VPS + Docker Compose + Nginx + HTTPS + CI/CD
