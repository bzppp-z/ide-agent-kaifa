# Phase 2: 任务规划与代码检索

**Goal:** agent 先给计划再执行，并能用文件/符号/文本三种检索定位代码；前端看到可逐项更新的计划卡片。

**Prerequisites:** Phase 1 完成（事件契约、权限模式、停止）。

**Deliverables:** 会话内项目索引；`update_plan`、`search_files`、`find_symbols`、`search_text` 四个工具；计划卡片在对话面板顶部常驻并随刷新恢复。

**Interface Decisions Implemented:** 计划卡片位置与状态（PRD FR-01 / 3.3）；检索工具卡片展示命中数；品牌绿用于"进行中"计划项。

---

## Task 1: 项目地图与符号索引

**Goal:** 会话内维护文件地图和顶层符号索引，文件变更后能增量失效，1 万文件内可用。

**Files:**
- Create: `backend/app/services/repo_index.py`
- Modify: `backend/app/services/workspace.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/core/config.py`

**Steps:**
- [ ] `repo_index.py` 实现 `RepoIndex`：`build(sandbox, workspace_path)` 递归列目录（复用 `SandboxService.list_files`），忽略与 v1 相同的目录集合，最多 10000 个文件；对 `.js/.jsx/.ts/.tsx/.py/.java/.go` 用正则提取顶层符号（`function/class/const/interface/type/def` 与组件声明），记录 `path`、`line`、`name`、`kind`。
- [ ] 提供 `search_files(fragment, limit, offset)`、`find_symbols(name, limit, offset)`、`search_text(query, path, limit, offset)`；文本检索复用现有逐文件读取，但解除深度 4 层限制，保留跳过目录与 1MB 单文件上限。
- [ ] 提供 `invalidate(path)` 与 `mark_stale()`：写入/删除后由文件 API 调用；索引未就绪时检索方法实时扫描并返回 `index_building=True`。
- [ ] `config.py` 增加 `repo_index_max_files=10000`、`repo_index_max_results=50`；`main.py` 创建 `app.state.repo_indexes: dict[str, RepoIndex]`；`workspace.ensure_container` 成功后按需构建，删除会话时移除。
- [ ] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 索引只保存工作区相对路径与代码首行，不保存文件全文；不跟随符号链接到工作区外。

---

## Task 2: 新增检索工具并接入 AgentLoop

**Goal:** agent 能在真实工程中按文件名、符号、文本快速定位，结果带路径+行号并支持分页。

**Files:**
- Modify: `backend/app/services/agent/tools.py`
- Modify: `backend/app/services/agent/loop.py`

**Steps:**
- [ ] `TOOL_SCHEMAS` 增加 `search_files(fragment, limit?, offset?)`、`find_symbols(name, limit?, offset?)`、`search_text(query, path, limit?, offset?)`；保留 v1 `search` 作为文本检索别名以兼容历史提示，但新系统提示只推荐 `search_text`。
- [ ] `ToolRegistry.call` 增加 `repo_index`、`session_id` 参数；三个检索工具返回 `details` 为每行 `path:line: content` 或 `path:line: signature`，`extra` 含 `matches/files_scanned/truncated/next_offset/index_building`。单页最多 50 条，按 `offset` 翻页。
- [ ] `loop.py` 的 `_build_messages` 在系统提示中加入项目地图摘要（文件数、主要目录、符号数），不注入全文；工具调用时传入索引实例。
- [ ] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。
- [ ] 浏览器验证：在 demo 仓库提问"登录/路由相关逻辑在哪"，期望工具卡片出现 `find_symbols` 或 `search_text`，回答中出现 `路径:行号`。

**Security/Boundary Notes:** 所有 `path` 参数过 `core.security.validate_workspace_path`；结果中不得出现工作区外绝对路径。

---

## Task 3: 计划状态、`update_plan` 工具与计划卡片

**Goal:** 多步任务先生成 3–8 项计划，执行中逐项更新并持久化；用户刷新后仍能看到。

**Files:**
- Create: `backend/app/services/agent/plan.py`
- Modify: `backend/app/services/agent/tools.py`
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/api/agent.py`
- Modify: `backend/app/api/sessions.py`
- Modify: `frontend/src/lib/sse.ts`
- Modify: `frontend/src/stores/useChatStore.ts`
- Create: `frontend/src/components/PlanCard.tsx`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/theme.ts`

**Steps:**
- [ ] `plan.py` 定义 `PlanItem(id, title, status in pending/in_progress/done/failed/skipped, note)` 与 `PlanState`；校验 3–8 项、最多 1 个 `in_progress`；序列化/反序列化 JSON。提供 `apply_update(current, incoming)`：保留 id，更新状态，拒绝空计划。
- [ ] `tools.py` 增加 `update_plan(items)` 工具：首次调用建立计划，后续调用为全量快照更新；非法计划返回 `ok=False` 并说明原因；返回 `summary="计划已更新：x/y 完成"`。
- [ ] `loop.py` 系统提示加入规划规则：满足"≥2 文件/新增/重构/修复/实现/迁移"时先调用 `update_plan` 再调其他工具；每完成一项必须基于工具结果调用 `update_plan` 标记 `done`；计划项失败标记 `failed` 并记录原因；同一时间仅一项 `in_progress`。
- [ ] 每次 `update_plan` 成功后，loop 发 `PlanUpdateEvent`；`api/agent.py` 持久化 `sessions.plan_json`；`api/sessions.py` 的详情响应返回 `plan`。
- [ ] 前端 `sse.ts` 分发 `plan.update` 到 `useChatStore.plan`；`PlanCard.tsx` 渲染顶部卡片（标题"本次计划"、项状态图标、进行中品牌绿、失败红色、完成灰勾）；`ChatPanel` 在消息列表上方常驻；`theme.ts` 仅补 `--danger`/`--success`（若已有则复用）。
- [ ] 浏览器验证 S1 前置：发送"给应用加一个 /healthz 接口并补一个检查脚本"，期望先出现 3–8 项计划，随后每完成一次文件操作计划项状态变化；刷新后计划仍在。
- [ ] 运行：`npm run typecheck`（cwd `frontend`)
  - Expected: 通过。

**Security/Boundary Notes:** 计划标题只展示用户可读文本；不把工具原始参数塞进计划项。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] `python -m compileall app` 与 `npm run typecheck` 通过
- [ ] 索引在 1 万文件内可用，检索结果带路径+行号
- [ ] 多步任务先出现计划卡片，执行中逐项更新
- [ ] 刷新后计划恢复
- [ ] v1 对话与审批无回归
- [ ] 可以进入 Phase 3
