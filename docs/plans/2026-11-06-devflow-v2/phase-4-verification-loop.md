# Phase 4: 验证闭环

**Goal:** agent 能发起受控命令、流式读取输出、失败后最多自动修复 2 轮；用户终端与 agent 命令互斥。

**Prerequisites:** Phase 3 编辑工具与检查点；Phase 1 权限与停止。

**Deliverables:** `run_command` 工具与命令审批；`run.output` 实时流；自动修复轮次控制；命令黑名单与交互式命令拦截；终端互斥。

**Interface Decisions Implemented:** 命令卡片实时输出、审批按钮、失败红色（PRD FR-04 / 组件表）；终态在计划项与变更清单中体现。

---

## Task 1: 命令服务与流式执行

**Goal:** 安全地在工作区内执行命令，支持超时、取消与输出截断。

**Files:**
- Create: `backend/app/services/commands.py`
- Modify: `backend/app/services/sandbox.py`
- Modify: `backend/app/core/config.py`

**Steps:**
- [x] `commands.py` 提供：
  - `DANGEROUS_PATTERNS`：删除根目录/工作区外路径、格式化、提权、关机重启、修改系统配置的正则；
  - `INTERACTIVE_PATTERNS`：`npm init`、`yarn init`、`vim/nano/less`、`read`、无参数的 `python`/`node`、`git rebase -i`、`ssh`；
  - `is_readonly_command(command)`：`ls/dir/pwd/git status/git diff/cat/type/head/tail`；
  - `truncate_output(text, head=200, tail=200)`；
  - `split_exit_code` 无需实现，直接使用进程返回码。
- [x] `sandbox.py` 增加 `async def exec_stream(container_id, command, timeout, on_output)`：使用 `asyncio.create_subprocess_exec`，stdout/stderr 合并，逐块读取并 `await on_output(chunk)`；超时 kill 并返回 124；取消（`asyncio.CancelledError`）时 `finally` kill 进程后重新抛出。
- [x] `config.py` 增加 `command_timeout_default=30`、`command_timeout_max=120`、`max_commands_per_run=10`、`max_auto_fix_rounds=2`。
- [x] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 命令 cwd 固定为工作区；空命令拒绝；`timeout` 取 `min(requested, command_timeout_max)`；黑名单命中直接拒绝，不进入审批。

---

## Task 2: `run_command` 工具、审批与自动修复

**Goal:** agent 改完能自己验证，失败能自己修，最多 2 轮后转人工。

**Files:**
- Modify: `backend/app/services/agent/tools.py`
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/services/agent/pending.py`
- Modify: `backend/app/core/events.py`
- Modify: `backend/app/schemas/agent.py`
- Modify: `backend/app/api/agent.py`
- Modify: `frontend/src/components/ToolCard.tsx`
- Modify: `frontend/src/components/ChangeSetCard.tsx`
- Modify: `frontend/src/stores/useChatStore.ts`

**Steps:**
- [x] `tools.py` 增加 `run_command(command, reason, timeout_seconds?)` schema；执行前检查：权限（只读拒绝；需审批/自动接受都需审批，除非只读命令）、黑名单、交互式命令；返回 `extra={exit_code, timed_out, truncated, command}`。
- [x] `pending.py` 增加 `PendingCommandRegistry`（`command_id/session_id/command/cwd/timeout/reason`），`wait` 默认超时 120 秒；`api/agent.py` 增加 `PATCH /agent/command/{id}` 接受/拒绝。
- [x] `loop.py` 对 `run_command` 特殊处理：发 `CommandProposedEvent` → 等审批 → 执行时把 `RunOutputEvent` 逐块 yield（通过 `exec_stream` 回调写入事件队列）→ 发 `ToolResultEvent`。
- [x] 自动修复策略写入系统提示：编辑完成后主动运行项目可识别的验证命令（优先 `npm test` / `npm run build` / `pytest` 等存在的入口，不存在则运行用户指定命令）；退出码非 0 时提取报错、修改后重跑；同一 run 内自动修复 ≤ 2 轮，第 3 次失败停止并输出"失败原因/已尝试修复/建议"。
- [x] `loop.py` 维护 `commands_executed`、`fix_rounds`；超过 10 条命令或 2 轮修复时向模型注入系统提示并停止自动重试；相同命令 + 相同补丁重复时复用现有 `RecentToolCallTracker` 提示。
- [x] 前端 `ToolCard` 对 `run_command` 渲染命令、审批按钮、实时输出、退出码与耗时；运行中显示停止按钮（调用会话停止）；`ChangeSetCard` 在对应文件上显示"已验证/验证失败"。（命令卡片以新增 `CommandCard` 承载，`ToolCard` 不再重复渲染；停止按钮复用 ChatPanel 既有停止入口）
- [x] 浏览器验证 S1：让 agent 跑脚本 → 终端/卡片出现逐行输出 → 成功时计划项勾选；故意提交一个会失败的脚本，观察 agent 自动修复 ≤ 2 轮后停止并说明。（以脚本化 Loop + SSE 事件断言替代：审批→`run.output` 逐行→exit 0；连续失败 3 次后第 4 条命令被"自动修复已达上限"拦截。浏览器自动化输入仍持续失焦，视觉交互留待 Phase 6）
- [x] 运行：`npm run typecheck`（cwd `frontend`）
  - Expected: 通过。

**Security/Boundary Notes:** 审批通过后才执行；拒绝后 agent 不得自动重试同一命令；命令输出只回灌头尾各 200 行。

---

## Task 3: 终端与 agent 命令互斥

**Goal:** agent 命令运行时用户终端输入排队，不产生交错输出；停止时命令进程被终止。

**Files:**
- Modify: `backend/app/services/sandbox.py`
- Modify: `backend/app/api/terminal.py`
- Modify: `backend/app/services/agent/tools.py`

**Steps:**
- [x] `SandboxService` 增加按 workspace 的 `asyncio.Lock` 字典 `command_locks`；`exec_stream` 与 `open_shell` 的 stdin 写入都必须持有同一把锁。
- [x] `terminal.py` 的 `pump_stdin` 在写入前 `async with lock`，等待期间向 WebSocket 发送一行提示"agent 正在执行命令，你的输入将在其结束后发送"；等待超过 120 秒放弃并提示。
- [x] 停止生成时取消 `run_command` 的 task，`exec_stream` 的 `finally` kill 子进程；终端锁随后释放。
- [x] 浏览器验证：agent 运行一个 5 秒命令时，在 xterm 输入 `echo hi`，期望看到排队提示，命令结束后 `hi` 出现在终端；点停止后命令立即终止。（以锁级脚本替代：命令持锁期间第二个获取方阻塞约 4.7s，命令结束后立即获得；停止后子进程被 kill、锁立即释放。xterm 排队提示文案仅静态走查，留待 Phase 6）
- [x] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 互斥只作用于同一会话 workspace，不阻塞其他会话。

---

## Phase Completion Checklist

- [x] 所有任务完成
- [x] `python -m compileall app` 与 `npm run typecheck` 通过（另 `npm run build` 通过）
- [x] 命令审批、黑名单、交互式拦截生效
- [x] 命令输出流式可见，超时与停止可终止进程
- [x] 失败自动修复 ≤ 2 轮，超限后转人工
- [x] 终端与 agent 命令互斥
- [x] 只读模式下无命令执行
- [x] 可以进入 Phase 5

## Verification Summary

- `python -m compileall app` 退出码 0；`npm run typecheck`、`npm run build` 通过（1140 modules）。
- 脚本化 Loop 冒烟 24 项全通过：审批提案与 `call_id`、`run.output` 逐行回传、exit 0、拒绝后不重复提交、只读命令免审批、连续失败 3 次后第 4 条命令被上限拦截、超时返回 124 且耗时约 1 秒、停止后任务取消且命令锁释放、只读权限模式拒绝命令。
- HTTP 冒烟：`/health` 200；`PATCH /agent/command/{unknown}` 404；无 Token 401；会话创建与清理正常。
- 遗留：`CommandCard` 按钮/输出的真实浏览器视觉交互与 xterm 排队提示文案未做自动化走查，Phase 6 补。
