# Phase 6: 前端体验与端到端验收

**Goal:** 多标签编辑器、检查点面板、变更清单收敛到可日常使用的界面，并跑通 PRD S1–S8 与 v1 回归。

**Prerequisites:** Phase 1–5 完成；全部后端事件与 API 可用。

**Deliverables:** 多标签编辑器与冲突三选一；检查点面板与回滚；变更清单；`file.changed` 同步；S1–S8 全部通过。

**Interface Decisions Implemented:** 多标签（PRD FR-06.1）；检查点入口（FR-06.3）；变更清单（FR-06.5）；停止按钮与权限下拉整合（FR-06.2/6.4）；深色 IDE 视觉不变（v1 设计文档）。

---

## Task 1: 多标签编辑器与文件变更同步

**Goal:** 同时打开多个文件，未保存/被 agent 修改/冲突状态可见，刷新后恢复标签。

**Files:**
- Modify: `backend/app/core/events.py`
- Modify: `backend/app/services/agent/tools.py`
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/api/files.py`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/sse.ts`
- Modify: `frontend/src/stores/useEditorStore.ts`
- Create: `frontend/src/components/EditorTabs.tsx`
- Modify: `frontend/src/components/MonacoEditor.tsx`
- Modify: `frontend/src/components/FileTree.tsx`

**Steps:**
- [ ] 后端在批次落盘成功、回滚完成、文件 API 保存成功后发 `FileChangedEvent(path, operation)`（写入/删除/回滚）；同一事件通过当前 run 的 SSE 队列送达前端（文件 API 无 SSE 时仅影响本地编辑器状态，不做轮询）。
- [ ] `useEditorStore.ts` 重构为多标签：`tabs: EditorTab[]`（`path/content/dirty/saving/loading/truncated/error/conflict/agentUpdated`）、`activePath`；`open` 已存在则激活，否则新增（上限 10，超过关闭最久未使用的非活动且非 dirty 标签）；自动保存仍为 300ms debounce。
- [ ] 关闭标签：无未保存直接关闭；有未保存弹确认（保存 / 放弃 / 取消）。刷新页面前用 `beforeunload` 提示存在未保存标签；标签列表与活动路径存 `sessionStorage`，刷新后重新加载文件内容。
- [ ] `file.changed` 处理：目标标签不存在则仅标记文件树；存在且 `dirty=false` → 重新拉取内容并短暂高亮；`dirty=true` → 置 `conflict=true`，显示"查看差异 / 保留我的 / 使用 agent 版本"三个操作，选择前不覆盖。
- [ ] `EditorTabs.tsx` 渲染标签栏：文件名、dirty 圆点、冲突黄色标记、关闭按钮；`MonacoEditor` 只渲染活动标签；`FileTree` 点击调用 `open` 而非单文件 `open`。
- [ ] 浏览器验证：依次打开 3 个文件 → 标签切换内容正确 → 改一个文件不保存 → agent 改为另一文件 → 目标标签自动刷新 → agent 改当前 dirty 文件 → 出现冲突三选一，三个选项行为正确。
- [ ] 运行：`npm run typecheck`（cwd `frontend`）
  - Expected: 通过。

**Security/Boundary Notes:** 冲突时默认不覆盖用户内容；"使用 agent 版本"必须用户显式点击。

---

## Task 2: 检查点面板、变更清单与顶栏整合

**Goal:** 用户能从界面回滚检查点、查看所有改动；权限与停止入口在同一套顶栏/对话面板中一致。

**Files:**
- Create: `frontend/src/components/CheckpointPanel.tsx`
- Create: `frontend/src/components/ChangeList.tsx`
- Modify: `frontend/src/components/TopBar.tsx`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/stores/useSessionStore.ts`
- Modify: `frontend/src/stores/useChatStore.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/theme.ts`

**Steps:**
- [ ] `TopBar` 增加"检查点"按钮，打开 `CheckpointPanel` 抽屉：列表显示时间、用户消息摘要、涉及文件数；最新在上；每条有"回滚到此前"按钮。
- [ ] 回滚确认弹窗必须写明"回滚会丢弃这之后的所有改动"；确认后调用 rollback API，成功后刷新文件树、活动标签与变更清单，并把回滚点之后的消息置灰（读取 `reverted`）。
- [ ] `ChangeList` 在对话面板底部入口打开：按时间倒序显示路径、操作、`+n/-n`、状态（待审批/已生效/已拒绝/已回滚/验证失败）；点击条目打开对应 diff；刷新后从 `GET /sessions/{id}/changes` 恢复。
- [ ] 检查点超过 50 条时界面只显示最新 50 条并提示"更早的检查点已淘汰"；回滚按钮在运行中禁用。
- [ ] 视觉回归：计划卡片、变更卡片、命令卡片、检查点抽屉均使用现有深色 tokens 与 8/12px 圆角；状态色只新增成功绿/失败红，沿用品牌绿 `#0FDC78`，不引入渐变或装饰图案。
- [ ] 浏览器验证 S6：做一次错误改动 → 打开检查点 → 回滚 → 二次确认 → 文件恢复、标签刷新、终端 `type/cat` 验证内容；消息置灰。
- [ ] 运行：`npm run typecheck` 与 `npm run build`（cwd `frontend`）
  - Expected: 两项均通过。

**Security/Boundary Notes:** 回滚必须二次确认；运行中禁止回滚；回滚后不允许残留待审批批次（调用 `discard_all`）。

---

## Task 3: 端到端验收与回归

**Goal:** 按 PRD 逐条走查，修复发现的问题，确认 v1 能力无回归与性能目标。

**Files:**
- Modify: 按走查发现的问题修改对应前后端文件（不新增测试框架）

**Steps:**
- [ ] 启动：运行 `start-devflow.bat`，确认后端 `/health` 返回 `{"ok": true, "db": true, "sandbox": true}`，前端 `http://localhost:5173` 可打开。
- [ ] 逐条执行 PRD S1–S8，记录每条的可见证据（计划卡片、变更卡片、命令输出、压缩提示、检查点回滚、停止时间、规则生效）。
- [ ] v1 回归：会话创建、SSE 工具调用、单文件 diff 审批、xterm 终端、F5 刷新恢复；3 个 demo 仓库各创建一次会话确认可用；本地目录会话创建与删除不删除用户目录。
- [ ] 性能走查（PRD 第 8 节）：计划卡片首次可见、索引 ≤ 3 秒、检索 ≤ 2 秒、变更卡片 ≤ 2 秒、命令首包 ≤ 500ms、回滚 ≤ 3 秒、停止 ≤ 1 秒；不达标项记录实测值并修复或标注偏差。
- [ ] 权限矩阵走查：只读模式下尝试写入/删除/命令均被拒；自动接受模式编辑不弹审批、删除与命令仍弹。
- [ ] 安全走查：构造 `../`、绝对路径、工作区外路径、`rm -rf /`、交互式命令，确认全部被拒绝。
- [ ] 运行：`C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m compileall app`（cwd `backend`）与 `npm run build`（cwd `frontend`）
  - Expected: 两项退出码 0。
- [ ] 更新本计划索引与各阶段 `Status` 为实际状态（Done 仅在对应验收通过后）。

**Security/Boundary Notes:** 验收中出现未通过项时不得把阶段标记为 Done；阻塞项写入 Blocking Questions 并保持 Blocked。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] `python -m compileall app` 与 `npm run build` 通过
- [ ] PRD S1–S8 全部通过并有可见证据
- [ ] v1 五个场景与 3 个 demo 仓库无回归
- [ ] 性能目标达标或偏差已记录
- [ ] 权限矩阵与安全边界走查通过
- [ ] 计划状态与索引同步更新
