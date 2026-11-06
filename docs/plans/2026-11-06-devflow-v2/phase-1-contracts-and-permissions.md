# Phase 1: 基础契约与权限模式

**Goal:** 扩展事件与数据契约，落地三档权限门控、会话偏好持久化和"停止生成"，为后续所有阶段提供统一入口。

**Prerequisites:** v1 已验收；后端可启动；`backend/app/core/events.py`、`models/session.py`、`api/agent.py` 为现有实现。

**Deliverables:** 新事件类型可从 SSE 输出并被前端识别；权限模式按会话持久化并在写入工具上生效；停止按钮 1 秒内结束 run。

**Interface Decisions Implemented:** 权限模式下拉（PRD FR-06.2 / 5.1）；停止生成状态（FR-06.4）；深色顶栏沿用 v1 tokens。

---

## Task 1: 扩展 SSE 事件与会话字段

**Goal:** 定义 v2 所有新增事件类型，并为会话增加权限、计划、摘要字段；旧 SQLite 库可平滑升级。

**Files:**
- Modify: `backend/app/core/events.py`
- Modify: `backend/app/models/session.py`
- Modify: `backend/app/models/message.py`
- Create: `backend/app/models/change.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/db.py`
- Modify: `frontend/src/lib/types.ts`

**Steps:**
- [x] 在 `events.py` 增加：`PlanUpdateEvent`（`plan` 完整快照）、`ChangeSetProposedEvent`（`change_set_id` + `files[]`，每项含 `path/operation/before/after/diff`）、`CommandProposedEvent`（`command_id/command/cwd/timeout_seconds/reason`）、`ContextCompressedEvent`（`summary/kept_messages/dropped_messages/warning`）、`FileChangedEvent`（`path/operation`）；`DoneEvent` 增加 `stopped: bool = False`。保留现有全部事件与 `SSEEvent` 判别联合。
- [x] `Session` 增加 `permission_mode: str = "approval"`、`plan_json: str | None`、`summary_text: str | None`；`Message` 增加 `reverted: bool = False`；新建 `ChangeRecord`（`id/session_id/path/operation/added/deleted/status/created_at`）。
- [x] 在 `db.init_db()` 建表后执行轻量迁移：对 `sessions`/`messages` 用 `PRAGMA table_info` 检查，缺列时 `ALTER TABLE ... ADD COLUMN`（SQLite 不支持 `ADD COLUMN IF NOT EXISTS`）。仅本地模式；Postgres 恢复时改为 Alembic。
- [x] 在 `frontend/src/lib/types.ts` 增加与后端一一对应的 5 个事件类型，扩展 `done` 的 `stopped`，并补 `ChangeFile`、`PlanItem`、`Checkpoint`、`ChangeRecord` 接口。
- [x] 运行：`C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m compileall app`（cwd `backend`）
  - Expected: 无 SyntaxError，退出码 0。
- [x] 运行：`npm run typecheck`（cwd `frontend`）
  - Expected: 无 TS 错误（此时事件尚未被消费，只验证类型定义）。

**Security/Boundary Notes:** 迁移只加列不删列；`ChangeRecord` 只记录工作区相对路径，不落绝对路径。

---

## Task 2: 权限门控与会话偏好

**Goal:** 让"只读 / 需审批 / 自动接受"成为服务端强制的唯一入口，前端可切换并按会话记忆。

**Files:**
- Create: `backend/app/services/permissions.py`
- Modify: `backend/app/schemas/session.py`
- Modify: `backend/app/api/sessions.py`
- Modify: `backend/app/services/agent/tools.py`
- Modify: `backend/app/services/agent/loop.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/stores/useSessionStore.ts`
- Modify: `frontend/src/components/TopBar.tsx`

**Steps:**
- [x] `permissions.py` 定义 `PermissionMode = Literal["readonly", "approval", "auto"]` 与 `can_write(mode)`、`can_delete(mode)`、`need_command_approval(mode)`、`can_auto_apply(mode)`。规则严格按 PRD 5.1：只读全部拒绝；自动接受仅编辑自动，删除与命令仍审批。
- [x] `schemas/session.py`：`SessionResponse`/`SessionDetailResponse` 返回 `permission_mode`、`plan`；新增 `SessionPreferencesUpdate(permission_mode)`。`api/sessions.py` 增加 `PATCH /sessions/{id}/preferences`，校验归属后写库并返回最新值。
- [x] `tools.py` 的 `ToolRegistry.call` 增加 `permission_mode` 参数；`write_patch` 在 `readonly` 下直接返回 `ok=False, summary="当前为只读模式，禁止写入"`，不读文件、不落盘。
- [x] `loop.py` 构造工具调用时传入会话权限模式（从 `session` 行读取，由 `api/agent.py` 传入）。
- [x] 前端 `TopBar.tsx` 增加三档下拉（只读/需审批/自动接受），`useSessionStore` 保存当前值并在 `bootstrap`/`load` 时从会话详情恢复；切换调用 `setPermissionMode`，失败回滚 UI 并提示。
- [x] 浏览器验证：新建会话 → 切只读 → 发送"把 README 第一行改成 hello" → 期望工具卡片返回只读失败、文件未变；切回需审批后同样请求出现审批卡片。

**Security/Boundary Notes:** 权限判定必须在服务端；前端下拉只是输入。未知模式一律按 `approval` 处理。

---

## Task 3: 停止生成

**Goal:** 生成/工具执行中可停止，1 秒内结束且不留下半审批状态。

**Files:**
- Modify: `backend/app/api/agent.py`
- Modify: `backend/app/services/agent/pending.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/stores/useChatStore.ts`

**Steps:**
- [x] `api/agent.py` 增加 `POST /sessions/{id}/stop`：校验归属；从 `loop_states` 取 `task` 与 `queue`；向队列先放 `DoneEvent(run_id=..., warning="stopped by user", stopped=True)` 再放 `None`；随后 `task.cancel()`。重复停止返回 `{"ok": true, "already_stopped": true}`。
- [x] `PendingPatchRegistry` 增加 `discard_all(session_id)`；停止时清理该会话所有待审批条目，等待方以拒绝返回。
- [x] 前端 `useChatStore` 增加 `stopping` 状态；`ChatPanel` 在 `running` 时把发送按钮替换为"停止"，点击调用 stop 并立即将本地状态置为"已停止"；收到 `done.stopped=true` 时清除 running。
- [x] 浏览器验证：让 agent 执行一个多步任务，中途点停止，记录点击到界面停止的时间 ≤ 1 秒；随后可立即发送下一条消息。
- [x] 回归：正常 run 不点停止时仍以 `done.stopped=false` 结束，刷新恢复不受影响。

**Security/Boundary Notes:** 停止只取消当前 run 的 task，不删除会话与已落盘文件。

---

## Phase Completion Checklist

- [x] 所有任务完成
- [x] `python -m compileall app` 通过
- [x] `npm run typecheck` 通过
- [x] 权限三档在浏览器中可切换且只读生效
- [x] 停止生成 ≤ 1 秒且可继续对话
- [x] v1 的会话创建、SSE 对话、diff 审批、终端、刷新恢复仍可用
- [x] 可以进入 Phase 2
