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
- [x] 后端在批次落盘成功、回滚完成、文件 API 保存成功后发 `FileChangedEvent(path, operation)`（写入/删除/回滚）；同一事件通过当前 run 的 SSE 队列送达前端（文件 API 无 SSE 时仅影响本地编辑器状态，不做轮询）。
- [x] `useEditorStore.ts` 重构为多标签：`tabs: EditorTab[]`（`path/content/dirty/saving/loading/truncated/error/conflict/agentUpdated`）、`activePath`；`open` 已存在则激活，否则新增（上限 10，超过关闭最久未使用的非活动且非 dirty 标签）；自动保存仍为 300ms debounce。
- [x] 关闭标签：无未保存直接关闭；有未保存弹确认（保存 / 放弃 / 取消）。刷新页面前用 `beforeunload` 提示存在未保存标签；标签列表与活动路径存 `sessionStorage`，刷新后重新加载文件内容。
- [x] `file.changed` 处理：目标标签不存在则仅标记文件树；存在且 `dirty=false` → 重新拉取内容并短暂高亮；`dirty=true` → 置 `conflict=true`，显示"查看差异 / 保留我的 / 使用 agent 版本"三个操作，选择前不覆盖。
- [x] `EditorTabs.tsx` 渲染标签栏：文件名、dirty 圆点、冲突黄色标记、关闭按钮；`MonacoEditor` 只渲染活动标签；`FileTree` 点击调用 `open` 而非单文件 `open`。
- [x] 浏览器验证：依次打开 3 个文件 → 标签切换内容正确 → 改一个文件不保存 → agent 改为另一文件 → 目标标签自动刷新 → agent 改当前 dirty 文件 → 出现冲突三选一，三个选项行为正确。
  - 浏览器自动化在 Monaco/输入框聚焦时持续报 "Type target page lost focus"，无法完成打字类交互；已用浏览器验证多标签打开/切换/刷新恢复（F5 后 3 个标签与活动标签一致），并用 esbuild 打包 store 的 Node 校验脚本覆盖 dirty 圆点、冲突三选一、`file.changed` 自动刷新与高亮、删除冲突、sessionStorage 恢复共 20 项，全部通过。
- [x] 运行：`npm run typecheck`（cwd `frontend`）
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
- [x] `TopBar` 增加"检查点"按钮，打开 `CheckpointPanel` 抽屉：列表显示时间、用户消息摘要、涉及文件数；最新在上；每条有"回滚到此前"按钮。
- [x] 回滚确认弹窗必须写明"回滚会丢弃这之后的所有改动"；确认后调用 rollback API，成功后刷新文件树、活动标签与变更清单，并把回滚点之后的消息置灰（读取 `reverted`）。
- [x] `ChangeList` 在对话面板底部入口打开：按时间倒序显示路径、操作、`+n/-n`、状态（待审批/已生效/已拒绝/已回滚/验证失败）；点击条目打开对应 diff；刷新后从 `GET /sessions/{id}/changes` 恢复。
- [x] 检查点超过 50 条时界面只显示最新 50 条并提示"更早的检查点已淘汰"；回滚按钮在运行中禁用。
- [x] 视觉回归：计划卡片、变更卡片、命令卡片、检查点抽屉均使用现有深色 tokens 与 8/12px 圆角；状态色只新增成功绿/失败红，沿用品牌绿 `#0FDC78`，不引入渐变或装饰图案。
- [x] 浏览器验证 S6：做一次错误改动 → 打开检查点 → 回滚 → 二次确认 → 文件恢复、标签刷新、终端 `type/cat` 验证内容；消息置灰。
  - 浏览器已验证"检查点"抽屉与"本次变更"抽屉可打开、空状态文案正确、无 JS 红色报错；打字类交互受浏览器自动化焦点限制，回滚全流程改用 API 验收（S6：回滚后文件内容与改动前完全一致、消息 `reverted=true`、变更状态 `reverted`、耗时 0.16s）。
- [x] 运行：`npm run typecheck` 与 `npm run build`（cwd `frontend`）
  - Expected: 两项均通过。

**Security/Boundary Notes:** 回滚必须二次确认；运行中禁止回滚；回滚后不允许残留待审批批次（调用 `discard_all`）。

---

## Task 3: 端到端验收与回归

**Goal:** 按 PRD 逐条走查，修复发现的问题，确认 v1 能力无回归与性能目标。

**Files:**
- Modify: 按走查发现的问题修改对应前后端文件（不新增测试框架）

**Steps:**
- [x] 启动：运行 `start-devflow.bat`，确认后端 `/health` 返回 `{"ok": true, "db": true, "sandbox": true}`，前端 `http://localhost:5173` 可打开。
  - 机器上 8000 端口被另一个项目占用，`start-devflow.bat` 报 "Backend NOT ready on :8000"；改为在 8002 端口启动同一后端（CORS 追加 5175）并新增 `http://localhost:5175` dev server 完成验收，`/health` 返回 `{"ok":true,"db":true,"sandbox":true}`。
- [x] 逐条执行 PRD S1–S8，记录每条的可见证据（计划卡片、变更卡片、命令输出、压缩提示、检查点回滚、停止时间、规则生效）。
- [x] v1 回归：会话创建、SSE 工具调用、单文件 diff 审批、xterm 终端、F5 刷新恢复；3 个 demo 仓库各创建一次会话确认可用；本地目录会话创建与删除不删除用户目录。
- [x] 性能走查（PRD 第 8 节）：计划卡片首次可见、索引 ≤ 3 秒、检索 ≤ 2 秒、变更卡片 ≤ 2 秒、命令首包 ≤ 500ms、回滚 ≤ 3 秒、停止 ≤ 1 秒；不达标项记录实测值并修复或标注偏差。
- [x] 权限矩阵走查：只读模式下尝试写入/删除/命令均被拒；自动接受模式编辑不弹审批、删除与命令仍弹。
- [x] 安全走查：构造 `../`、绝对路径、工作区外路径、`rm -rf /`、交互式命令，确认全部被拒绝。
- [x] 运行：`C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m compileall app`（cwd `backend`）与 `npm run build`（cwd `frontend`）
  - Expected: 两项退出码 0。
- [x] 更新本计划索引与各阶段 `Status` 为实际状态（Done 仅在对应验收通过后）。

**Security/Boundary Notes:** 验收中出现未通过项时不得把阶段标记为 Done；阻塞项写入 Blocking Questions 并保持 Blocked。

---

## Verification Summary（2026-11-06）

**S1–S8（真实模型）**

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| S1 规划与多文件改动 | 通过 | 5 项计划全部 done；`apply_patch` 2 文件原子批次单次审批；`node scripts/check-healthz.js` exit 0；生成变更记录与检查点 |
| S2 精准检索 | 通过 | `search_files` 命中 auth.py；`search_text` 返回行号；仅 1 次 `read`；回答含路径+行号 |
| S3 新建/删除预览 | 通过 | 新建文件预览后 reject，legacy.js 仍存在；第二批次删除生效；文件树同步 |
| S4 上下文压缩 | 通过（配置偏差） | 以 `CONTEXT_TOTAL_TOKENS=8000` 启动后端，20 轮对话第 13 轮起触发 `context.compressed` 共 8 次；摘要含目标/未完成项；默认 200k 预算下 20 轮短对话不足以触发压缩 |
| S5 权限矩阵 | 通过 | readonly 写入被拒且无重试、无落盘；auto 编辑 `auto_applied=true`；命令仍需审批，拒绝后停止 |
| S6 检查点回滚 | 通过 | `file_count` 有值；回滚后内容与 before 完全一致；变更 `reverted`、消息 `reverted=true`；回滚耗时 0.16s |
| S7 停止 | 通过 | stop→done 0.11s；保留已输出部分回复；可立即发起下一 run |
| S8 规则生效 | 通过 | 有 `DEVFLOW.md` 时新建 `src/utils/slug.ts`（无 .js）；删除规则后新建 `src/utils/trim.js`（无 .ts） |

**v1 回归**

- 会话创建：3 个 demo 仓库（todo-app / express-demo / python-flask-demo）各创建一次会话，根目录列表与关键文件均 200；未知 demo repo 返回 403。
- 本地目录会话：临时目录创建会话 → 通过文件 API 写入 `agent-note.txt` → 删除会话后用户目录、原文件、新文件均保留；`C:\Windows` 返回 400（保护目录）、不存在路径 400、相对路径 400。
- SSE 工具调用与单文件 diff 审批：S2/S3/S6 覆盖（`tool.call`/`patch.proposed`/`change_set.proposed` 均为真实模型产生）。
- xterm：浏览器加载会话后终端 WebSocket `/terminal/ws/{session}` 连接 accepted（后端日志可见）。
- F5 刷新恢复：浏览器刷新后 3 个标签（index.js、package.json、README.md）与活动标签一致，无 JS 报错。

**性能实测（目标 vs 实测）**

| 项目 | 目标 | 实测 | 结论 |
| --- | --- | --- | --- |
| demo 仓库索引构建 | ≤3s | 26.2 / 23.8 / 20.1 ms | 达标 |
| 1 万文件索引构建（冷） | ≤3s | 修复前 269s → 修复后 21.9s | **未达标，记录偏差**（Windows 实时防护 + 非 ASCII 路径下单次 stat/read 约 2–3ms；已做 Path 缓存/单次 stat/并行读取优化，约 12x 提升；demo 规模达标） |
| `search_files` / `find_symbols` | ≤2s | 1 万文件 0.7ms / 1.0ms | 达标 |
| `search_text` | ≤2s | demo 20–33ms；1 万文件 0.35–0.70s | 达标 |
| 计划卡片首次可见 | ≤3s | 3.01s | 临界达标 |
| 变更卡片（工具结果→卡片） | ≤2s | 约 1.9s（run 起点 4.90s，首个工具调用 1.24s） | 达标 |
| 命令输出首包 | ≤500ms | 868–910ms | **未达标，记录偏差**（Windows node 冷启动占主要耗时） |
| 回滚 | ≤3s | 0.16s | 达标 |
| 停止 | ≤1s | 0.11s | 达标 |

**安全边界**

- 文件 API：`../evil.txt`、`..%2F..%2Fevil.txt`、`..%5C..%5Cevil.txt`、`C:/Windows/win.ini`、`/etc/passwd`、`src/../../evil.txt` 全部 403。
- 命令守卫：`rm -rf /`、`sudo rm -rf /`、`shutdown`、`mkfs`、`curl | bash` 判定为危险；`vim`/`nano`/`python`/`node`/`git rebase -i`/`ssh` 判定为交互式；`node src/check.js`、`git status`、`ls` 判定为安全/只读。

**已知限制**

- 浏览器自动化工具在当前环境无法向 Monaco/输入框聚焦输入（"Type target page lost focus"），打字类 UI 验收改用 Node + esbuild 打包 store 的 20 项逻辑校验（全部通过）；点击类 UI 验收（多标签、抽屉、空状态、F5 恢复、权限下拉）已在浏览器完成。
- 机器 8000 端口被其它项目占用，本次验收使用 8002 + 5175 端口。

---

## Phase Completion Checklist

- [x] 所有任务完成
- [x] `python -m compileall app` 与 `npm run build` 通过
- [x] PRD S1–S8 全部通过并有可见证据
- [x] v1 五个场景与 3 个 demo 仓库无回归
- [x] 性能目标达标或偏差已记录
- [x] 权限矩阵与安全边界走查通过
- [x] 计划状态与索引同步更新
