# Phase 5: 上下文管理

**Goal:** 长会话按分层预算装配上下文，历史超限生成结构化摘要，项目规则 `DEVFLOW.md` 自动生效，压缩对用户可见。

**Prerequisites:** Phase 2 项目索引摘要；Phase 4 run 流程稳定。

**Deliverables:** 分层 token 预算；结构化摘要与降级；`DEVFLOW.md` 注入与打码；`context.compressed` 事件与可展开提示。

**Interface Decisions Implemented:** 对话面板中的压缩提示（PRD FR-05）；规则文件在现有编辑器中可编辑（复用 `MonacoEditor`）。

---

## Task 1: 分层预算与上下文装配

**Goal:** 按 PRD 的 10/10/20/60 分区控制每次发给模型的内容。

**Files:**
- Modify: `backend/app/services/agent/context.py`
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/core/config.py`

**Steps:**
- [ ] `context.py` 定义 `ContextBudget(total)` 与四个分区比例（固定 10%、地图 10%、摘要 20%、工作 60%）；保留 `estimate_tokens`（字符/4）。
- [ ] 实现 `build_context(system_prompt, rules, map_summary, summary, messages, total_tokens)`：各分区超限时按优先级裁剪——先裁工具结果（保留摘要行），再裁早期对话，最后裁地图摘要；system 与规则永不裁；保留最近 3 条用户消息与最近 1 轮工具结果。
- [ ] `loop.py` 每轮调用 `build_context` 替代现有 `truncate_messages`；单条工具结果超过工作区 30% 时先截断（读文件 2000 行、命令头尾 200 行）。
- [ ] `config.py` 增加 `context_total_tokens=200000` 与四个比例字段，允许 `.env` 覆盖。
- [ ] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 预算不足时优先保留规则与用户最后消息，不允许静默丢弃安全规则。

---

## Task 2: 结构化历史摘要与降级

**Goal:** 超限时生成包含目标、已改文件、决策、失败、未完成项的摘要，失败则降级截断。

**Files:**
- Create: `backend/app/services/agent/summary.py`
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/models/session.py`
- Modify: `backend/app/api/agent.py`

**Steps:**
- [ ] `summary.py` 实现 `summarize_history(provider, messages, previous_summary, plan)`：用非工具调用的流式请求拼接结果，要求输出六段固定结构（目标 / 已改文件 / 关键决策 / 失败与尝试 / 未完成事项 / 最近验证）；温度 0.2；总超时 20 秒。
- [ ] 触发条件：工作层历史估算超过摘要分区预算时触发；摘要结果写入 `sessions.summary_text`，下一次 run 优先使用已有摘要 + 新增对话，避免重复全量摘要。
- [ ] 摘要成功后发 `ContextCompressedEvent(summary, kept_messages, dropped_messages)`；摘要失败（超时/异常/空内容）时降级为 `truncate_messages`，并发 `ContextCompressedEvent(summary="", warning="压缩失败，已截断早期消息")`。
- [ ] 摘要必须保留文件路径与未完成项；若模型输出缺少"未完成事项"段，重试一次，仍缺则该段写"未提取到"。
- [ ] 运行：`python -m compileall app`（cwd `backend`）
  - Expected: 退出码 0。

**Security/Boundary Notes:** 摘要不包含 API key、完整密钥文件内容；摘要请求与正常对话使用同一个 provider。

---

## Task 3: `DEVFLOW.md` 规则注入与前端压缩提示

**Goal:** 工作区根目录的项目规则自动进入系统提示，优先于通用提示（安全规则除外）；用户能看到压缩发生。

**Files:**
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/services/workspace.py`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/sse.ts`
- Modify: `frontend/src/stores/useChatStore.ts`
- Modify: `frontend/src/components/ChatPanel.tsx`

**Steps:**
- [ ] run 开始时读取工作区根 `DEVFLOW.md`（不存在则跳过）；超过 2000 行只取前 2000 行并记录提示；用正则对 `sk-...`、`api_key=...`、`Bearer ...` 打码。
- [ ] 注入顺序：系统安全规则 → `DEVFLOW.md` 规则（标注"项目规则优先"）→ 当前模型 → 工具说明；冲突时规则生效，但"不得越权访问工作区/不得执行危险命令"始终保留。
- [ ] 规则文件缺失、读取失败、编码错误时不影响 run，仅在工具卡片或日志中标注。
- [ ] 前端 `ContextCompressedEvent` 渲染为对话中的系统提示条："已压缩早期上下文（保留目标与未完成项）"；点击展开显示摘要；摘要为空时显示"压缩失败，已截断早期消息"。
- [ ] 浏览器验证 S4：连续对话 20 轮以上，确认出现压缩提示、摘要含早期目标与已改文件，且后续回答仍能引用；在工作区创建 `DEVFLOW.md` 写"新增文件一律用 TypeScript"，下一次任务遵守（S8）。
- [ ] 运行：`npm run typecheck`（cwd `frontend`）
  - Expected: 通过。

**Security/Boundary Notes:** 规则文件不得覆盖"路径必须留在工作区""危险命令拒绝"两条硬规则；打码只影响注入内容，不修改磁盘文件。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] `python -m compileall app` 与 `npm run typecheck` 通过
- [ ] 20 轮会话不超限、不丢目标
- [ ] 压缩提示可见且可展开
- [ ] 摘要失败时降级不中断对话
- [ ] `DEVFLOW.md` 生效，删除后失效
- [ ] 规则文件不覆盖安全硬规则
- [ ] 可以进入 Phase 6
