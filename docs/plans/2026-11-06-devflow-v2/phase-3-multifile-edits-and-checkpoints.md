# Phase 3: 多文件编辑与检查点

**Goal:** agent 能新建、整文件写入、删除和批量修改；批次原子落盘、按文件审批；每次 run 有可回滚检查点；会话变更清单可查。

**Prerequisites:** Phase 1 权限门控与事件契约；Phase 2 索引失效钩子。

**Deliverables:** `write_file`、`delete_file`、`apply_patch` 三个工具；`change_set.proposed` 事件与逐文件决议 API；检查点创建/列表/回滚；`changes` 记录与查询。

**Interface Decisions Implemented:** 多文件 diff 卡片、删除二次确认（PRD FR-03）；检查点入口数据（FR-06.3）；变更清单（FR-06.5）。

---

## Task 1: 编辑工具与批次原子落盘

**Goal:** 支持四种编辑操作，且一个批次要么全部生效、要么全部不生效。

**Files:**
- Modify: `backend/app/services/agent/tools.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/sandbox.py`

**Steps:**
- [ ] `config.py` 增加 `max_files_per_change_set=10`、`max_agent_write_bytes=1048576`。
- [ ] 新增工具：
  - `write_file(path, content, overwrite=false)`：文件存在且 `overwrite=false` 返回 `ok=False`；存在且 `overwrite=true` 视为整文件写入；父目录不存在时在预览中标注"将创建目录"。
  - `delete_file(path)`：返回文件大小与最后修改时间；单独成批，不得与写操作混合。
  - `apply_patch(files)`：`files` 为 `[{path, search, replace}]` 或 `[{path, before, after}]`；单批次 1–10 个文件；`search/replace` 仍要求唯一匹配。
- [ ] 批次执行采用两阶段：先在内存中计算每个文件的 `after` 与 `before`，校验路径、二进制（读取失败 `UnicodeDecodeError`）、大小 ≤ 1MB；`dry_run=true` 只返回预览；落盘时记录已写文件列表，任一失败则把已写文件恢复为 `before`，新建文件删除，返回 `ok=False` 与失败原因。
- [ ] 预览阶段计算每个文件的内容哈希（`sha256(before)`），落盘前重新读取比对；不一致则该批次判为冲突，整批不落盘。
- [ ] `sandbox.py` 增加 `delete_file(container_id, relative_path)` 与 `path_hash(container_id, relative_path)`（不存在返回 `None`）。
- [ ] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 删除路径必须过 `validate_workspace_path`；不得批量删除目录；`..`、绝对路径、工作区外路径一律拒绝。

---

## Task 2: 变更批次审批与前端卡片

**Goal:** 一个批次一次提案、逐文件接受/拒绝、整批原子生效；删除有二次确认。

**Files:**
- Modify: `backend/app/core/events.py`
- Modify: `backend/app/services/agent/pending.py`
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/schemas/agent.py`
- Modify: `backend/app/api/agent.py`
- Create: `frontend/src/components/ChangeSetCard.tsx`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/stores/useChatStore.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`

**Steps:**
- [ ] `pending.py` 增加 `PendingChangeSetRegistry`：条目含 `change_set_id/session_id/files[{path,operation,before,after,diff}]`、逐文件决议字典、`asyncio.Event`；`resolve(change_set_id, decisions)` 与 `wait(..., timeout=120)`；超时视为全部拒绝；`discard_all(session_id)` 供停止使用。
- [ ] `loop.py` 对编辑类工具：`readonly` 直接返回失败；`auto` 且非删除直接落盘并发 `change_set` 摘要事件；`approval` 先 dry_run → `ChangeSetProposedEvent` → 等待决议 → 只对接受的子集执行原子落盘；被拒文件作为失败结果回灌。v1 的 `write_patch` 单文件流程保持原有 `patch.proposed` + `PATCH /agent/patch/{id}` 不变，两套审批卡片并存。
- [ ] `schemas/agent.py` 增加 `ChangeSetDecisionRequest(decisions: dict[str, bool])`；`api/agent.py` 增加 `PATCH /agent/change-set/{id}`，校验会话归属，返回 `applied/rejected/failed`。
- [ ] 删除操作单独提案并在前端强制二次确认：`ChangeSetCard` 对 `operation=delete` 的文件渲染红色提示与"确认删除"复选，未勾选时"接受"禁用。
- [ ] `ChangeSetCard` 用现有 `PatchDiff` 渲染每个文件 diff，支持逐文件切换接受/拒绝、整批接受/整批拒绝；接受中显示 loading，结果显示"已生效/已拒绝/冲突"。
- [ ] 浏览器验证 S3：要求"新建 utils/format.js 并删除 legacy.js" → 出现两个批次（新建批次、删除批次）→ 删除批次需勾选确认 → 接受后文件树立即更新；拒绝删除时文件仍在。
- [ ] 运行：`npm run typecheck`（cwd `frontend`）
  - Expected: 通过。

**Security/Boundary Notes:** 决议 API 必须校验 `patch/change_set` 属于当前会话；过期条目返回 404；不得自动重试相同批次。

---

## Task 3: 检查点创建、回滚与消息置灰

**Goal:** 每次 run 开始有检查点，写入前惰性快照原文件；可回滚到任意检查点，回滚后消息标记。

**Files:**
- Create: `backend/app/services/checkpoints.py`
- Modify: `backend/app/models/session.py`
- Modify: `backend/app/models/message.py`
- Modify: `backend/app/api/sessions.py`
- Modify: `backend/app/api/agent.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/workspace.py`
- Modify: `backend/app/core/config.py`

**Steps:**
- [ ] `checkpoints.py` 实现 `CheckpointService`：元数据 JSON 存 `workspaces/.checkpoints/<session_id>/index.json`；检查点字段 `id/session_id/created_at/user_message/file_count`。
- [ ] `create(session_id, user_message)` 在每次 run 开始时调用，只写元数据；`capture_file(session_id, checkpoint_id, workspace_path, relative_path, container_id)` 在每次写入前调用：首次捕获该文件原内容（不存在记 `{exists:false}`），重复写入不覆盖第一次的原内容。
- [ ] `list(session_id)` 按时间倒序，最多 50 条；超出淘汰最旧并删除对应快照文件；`rollback(session_id, checkpoint_id, sandbox)` 逐文件恢复（存在则写回原内容，原本不存在则删除），并返回恢复文件数。
- [ ] `api/agent.py` 在 run 启动时创建检查点；`tools.py` 在 `write_file/delete_file/apply_patch` 落盘前调用 `capture_file`（通过注入的 checkpoint 服务）。
- [ ] `api/sessions.py` 增加 `GET /sessions/{id}/checkpoints` 与 `POST /sessions/{id}/checkpoints/{cid}/rollback`；回滚后把 `created_at > checkpoint.created_at` 的消息 `reverted=True` 并提交；会话详情返回该字段。
- [ ] `workspace.stop_and_remove`/删除会话时清理 `.checkpoints/<session_id>`；`config.py` 增加 `max_checkpoints_per_session=50`。
- [ ] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 快照只存工作区内文件；回滚路径必须重新校验；用户本地目录只恢复被 agent 改过的文件，绝不整目录删除。

---

## Task 4: 变更记录与变更清单 API

**Goal:** 会话内所有 agent 改动可查询，供前端变更清单使用。

**Files:**
- Modify: `backend/app/models/change.py`
- Modify: `backend/app/api/sessions.py`
- Modify: `backend/app/schemas/session.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`

**Steps:**
- [ ] 在批次落盘成功后写入 `ChangeRecord`：`operation in create/update/delete`、`added/deleted` 行数（用 `difflib` 统计）、`status=applied`；被拒绝写 `rejected`；回滚时把该检查点之后的记录标记 `reverted`。
- [ ] `GET /sessions/{id}/changes` 返回按时间倒序的变更列表；会话详情不内联全量清单，避免首屏过大。
- [ ] 前端 `api.ts` 增加 `listChanges(sessionId)` 与类型 `ChangeRecord`。
- [ ] 浏览器验证：完成 S3 后调用变更清单（Phase 6 才做 UI，本任务用浏览器开发者工具或临时控制台确认 JSON 含 2 条记录：create + delete）。
- [ ] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 变更记录不含文件全文，只含路径、操作、行数。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] `python -m compileall app` 与 `npm run typecheck` 通过
- [ ] 新建/整文件写入/删除/批量补丁均可用
- [ ] 批次冲突或失败时整批不落盘
- [ ] 删除必须二次确认
- [ ] 检查点可回滚，回滚后文件内容与终端验证一致
- [ ] 变更清单 API 返回正确记录
- [ ] v1 单文件 patch 审批无回归
- [ ] 可以进入 Phase 4
