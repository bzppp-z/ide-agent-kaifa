# DevFlow v2 Implementation Plan

**Created:** 2026-11-06
**Status:** In Progress
**Source Spec:** `docs/specs/2026-11-06-devflow-v2-prd.md`

## Overview

**Goal:** 把 DevFlow 从"能演示的 4 工具 agent"升级为能独立完成中小型开发任务的 agent：可规划、可检索、可多文件编辑、可跑命令自修复、长会话不失忆、改错可回滚。

**Architecture:** 在 v1 的 FastAPI + React + SSE + 本地沙箱子进程结构上增量扩展，不替换基础设施。后端新增三类能力层：权限门控（所有写/命令的唯一入口）、项目索引（文件+符号，会话内缓存）、检查点（按 run 惰性快照）。AgentLoop 通过新增的 `update_plan`、`search_files`、`find_symbols`、`write_file`、`delete_file`、`apply_patch`、`run_command` 工具获得新能力；前端新增计划卡片、变更批次卡片、多标签编辑器、权限下拉、检查点面板、变更清单、停止按钮。

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy Async + SQLite（本地模式）+ pydantic v2；React 19 + TypeScript + Vite + Zustand + Monaco + xterm。沿用 v1 的 `#171717` 深色底、`#0FDC78` 品牌绿、8/12px 圆角。

**验证方式说明：** 用户已在 v1 结束后移除全部自动化测试与测试依赖（pytest / vitest / e2e 脚本），本计划**不重新引入测试框架**。每个阶段以 `python -m compileall`、`npm run typecheck` / `npm run build`、浏览器手工走查 PRD 验收场景作为验证手段。这是刻意选择，不是遗漏。

## Files and Responsibilities

| File/Module | Action | Responsibility | Notes |
|---|---|---|---|
| `backend/app/core/events.py` | Modify | 新增 `plan.update`、`change_set.proposed`、`command.proposed`、`context.compressed`、`file.changed`；`done` 增加 `stopped` | 所有事件继承 `BaseSSEEvent`，前端按 `type` 分发 |
| `backend/app/core/config.py` | Modify | 新增批次上限、检查点上限、命令超时、token 预算比例 | 默认值写进 Settings，可被 `.env` 覆盖 |
| `backend/app/models/session.py` | Modify | `permission_mode`、`plan_json`、`summary_text` 字段 | 默认 `approval`；plan/summary 为 JSON/Text |
| `backend/app/models/message.py` | Modify | 增加 `reverted` 布尔字段 | 回滚后把该时间点后的消息置灰 |
| `backend/app/models/change.py` | Create | 变更记录：会话、路径、操作、增删行数、状态、时间 | 变更清单数据源 |
| `backend/app/schemas/session.py` | Modify | 返回 `permission_mode`、`plan`；新增偏好更新请求体 | |
| `backend/app/schemas/agent.py` | Modify | 变更批次决议、命令决议请求体 | |
| `backend/app/services/permissions.py` | Create | 三档权限矩阵与操作判定 | 只读/需审批/自动接受 |
| `backend/app/services/repo_index.py` | Create | 会话内文件地图 + 符号索引 + 增量失效 | 正则提取符号，不引第三方 parser |
| `backend/app/services/checkpoints.py` | Create | run 级检查点元数据、惰性文件快照、回滚、50 条淘汰 | 快照存 `workspaces/.checkpoints/<session>/` |
| `backend/app/services/commands.py` | Create | 命令黑名单、交互式命令识别、输出头尾截断 | 纯函数，便于复用 |
| `backend/app/services/agent/plan.py` | Create | 计划状态模型与校验（3–8 项、单进行中） | 序列化到 `session.plan_json` |
| `backend/app/services/agent/tools.py` | Modify | 新增 7 个工具；检索分页；权限门控；批次原子落盘 | 所有路径过 `core.security` |
| `backend/app/services/agent/loop.py` | Modify | 规划流程、批次审批、命令流式事件、自动修复上限、上下文装配 | 保留现有死循环软检测 |
| `backend/app/services/agent/context.py` | Modify | 分层 token 预算与装配 | 保留 `estimate_tokens` |
| `backend/app/services/agent/summary.py` | Create | 结构化历史摘要生成与失败降级 | 复用 provider 流式接口 |
| `backend/app/services/agent/pending.py` | Modify | 新增 `PendingChangeSetRegistry`、`PendingCommandRegistry` | 与 patch 注册表并存 |
| `backend/app/services/sandbox.py` | Modify | `exec_stream` 流式执行；会话级命令锁 | 支持取消时 kill 进程 |
| `backend/app/services/workspace.py` | Modify | 会话索引/检查点/锁的创建与清理入口 | 删除会话时清理快照 |
| `backend/app/api/agent.py` | Modify | 停止、变更批次决议、命令决议、checkpoint 创建 | 沿用 `loop_states` |
| `backend/app/api/sessions.py` | Modify | 返回计划/权限/变更清单；PATCH 偏好；GET checkpoints；POST rollback | 校验会话归属 |
| `backend/app/api/files.py` | Modify | 写入/删除后使索引失效并广播 `file.changed` | |
| `backend/app/api/terminal.py` | Modify | 与 agent 命令共用会话锁 | 命令运行期间用户输入排队 |
| `backend/app/main.py` | Modify | 装配权限、索引、检查点注册表；shutdown 清理 | |
| `frontend/src/lib/types.ts` | Modify | 新增 SSE 事件与 REST 类型 | 与 pydantic 对齐 |
| `frontend/src/lib/api.ts` | Modify | stop / permission / change-set / command / checkpoints / changes | |
| `frontend/src/lib/sse.ts` | Modify | 新事件分发到对应 store | |
| `frontend/src/stores/useSessionStore.ts` | Modify | 权限模式、计划、停止状态 | |
| `frontend/src/stores/useChatStore.ts` | Modify | 计划、变更批次、命令卡片、压缩提示、变更清单 | |
| `frontend/src/stores/useEditorStore.ts` | Modify | 单文件 → 多标签（tabs + activePath + 冲突） | 自动保存 debounce 沿用 300ms |
| `frontend/src/components/PlanCard.tsx` | Create | 计划清单与逐项状态 | 对话面板顶部 |
| `frontend/src/components/ChangeSetCard.tsx` | Create | 多文件 diff、逐文件/整批接受、删除二次确认 | 复用 `PatchDiff` 渲染 |
| `frontend/src/components/ToolCard.tsx` | Modify | 命令卡片实时输出、退出码、停止 | |
| `frontend/src/components/ChatPanel.tsx` | Modify | 计划卡、压缩提示、变更清单入口、停止按钮 | |
| `frontend/src/components/TopBar.tsx` | Modify | 权限模式下拉、检查点入口 | |
| `frontend/src/components/EditorTabs.tsx` | Create | 标签栏、未保存/冲突标记、关闭确认 | |
| `frontend/src/components/MonacoEditor.tsx` | Modify | 渲染当前活动标签 | |
| `frontend/src/components/CheckpointPanel.tsx` | Create | 检查点列表与回滚二次确认 | |
| `frontend/src/components/ChangeList.tsx` | Create | 本次会话变更清单 | |
| `frontend/src/theme.ts` | Modify | 仅补计划/命令状态色，不改品牌色 | |

## Interface Implementation Map

| Design Decision | Source | Implementation Surface | Verification |
|---|---|---|---|
| 计划卡片常驻对话面板顶部、逐项状态 | PRD FR-01 / 3.3 | `PlanCard.tsx` + `loop.py` 的 `update_plan` | S1：看到计划并逐项勾选 |
| 多文件 diff、逐文件/整批审批、删除二次确认 | PRD FR-03 | `ChangeSetCard.tsx` + 新增 `change_set.proposed`；保留 v1 单文件 `patch.proposed` | S3：新建+删除走通 |
| 权限模式三档下拉，按会话记忆 | PRD FR-06.2 / 5.1 | `TopBar.tsx` + `permissions.py` | S5：只读拒绝、自动接受仅编辑 |
| 多标签编辑器：未保存圆点、agent 修改标记、冲突三选一 | PRD FR-06.1 | `EditorTabs.tsx` + `useEditorStore.ts` + `file.changed` | S1/S3：编辑后自动刷新或提示冲突 |
| 停止按钮：1 秒内结束、保留已输出 | PRD FR-06.4 | `ChatPanel.tsx` + `POST /sessions/{id}/stop` | S7 |
| 检查点入口与回滚二次确认 | PRD FR-06.3 | `CheckpointPanel.tsx` + `checkpoints.py` | S6 |
| 上下文压缩提示可展开 | PRD FR-05 | `ChatPanel.tsx` + `context.compressed` | S4 |
| 命令卡片实时输出与审批 | PRD FR-04 | `ToolCard.tsx` + `run.output` + `command.proposed` | S1 |
| 深色底/品牌绿/圆角不变 | v1 设计文档 | `theme.ts`、Tailwind tokens | 截图比对，无新增装饰性渐变 |
| 变更清单入口 | PRD FR-06.5 | `ChangeList.tsx` + `changes` 表 | S1：列出改动文件 |

## Phases

### Phase 1: 基础契约与权限模式
**File:** [phase-1-contracts-and-permissions.md](phase-1-contracts-and-permissions.md)
**Goal:** 扩展 SSE/REST 契约，落地三档权限门控与会话偏好持久化，实现"停止生成"，为后续阶段提供统一入口。
**Tasks:** 3
**Status:** Done
**Completion:** √

### Phase 2: 任务规划与代码检索
**File:** [phase-2-planning-and-search.md](phase-2-planning-and-search.md)
**Goal:** agent 能先给计划再执行，并靠文件/符号/文本三种检索定位代码；前端渲染计划卡片。
**Tasks:** 3
**Status:** Done
**Completion:** √

### Phase 3: 多文件编辑与检查点
**File:** [phase-3-multifile-edits-and-checkpoints.md](phase-3-multifile-edits-and-checkpoints.md)
**Goal:** 支持新建/整文件写入/删除/批量补丁，批次原子落盘并按文件审批；run 级检查点可回滚；变更清单可查。
**Tasks:** 4
**Status:** Done
**Completion:** √

### Phase 4: 验证闭环
**File:** [phase-4-verification-loop.md](phase-4-verification-loop.md)
**Goal:** agent 可发起受控命令、流式读取输出、失败后最多自动修复 2 轮；终端与命令互斥。
**Tasks:** 3
**Status:** Done
**Completion:** √

### Phase 5: 上下文管理
**File:** [phase-5-context-management.md](phase-5-context-management.md)
**Goal:** 分层 token 预算、结构化历史摘要、`DEVFLOW.md` 项目规则注入与压缩提示。
**Tasks:** 3
**Status:** Done
**Completion:** √

### Phase 6: 前端体验与端到端验收
**File:** [phase-6-editor-and-e2e.md](phase-6-editor-and-e2e.md)
**Goal:** 多标签编辑器、检查点/变更清单面板收敛，跑通 PRD 的 S1–S8 与 v1 回归。
**Tasks:** 3
**Status:** Done
**Completion:** √

## Dependencies

- 阶段顺序：1 → 2 → 3 → 4 → 5 → 6；Phase 3 的写入工具依赖 Phase 1 的权限门控；Phase 4 的命令审批依赖 Phase 1 的注册表模式；Phase 6 依赖前五阶段事件契约稳定。
- 运行时：后端 `C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe`；前端 Node 20+；启动脚本 `start-devflow.bat` / `stop-devflow.bat`。
- 模型：DeepSeek（`backend/.env` 已配置）；Provider 不可用时 MockProvider 仍可验证事件链路，但规划/修复行为需真实模型。
- 数据库：SQLite 现有库；新增字段通过 `init_db()` 的 `create_all` 不覆盖旧表，需在启动时执行轻量 `ALTER TABLE` 迁移（见 Phase 1 Task 1）。
- 无自动化测试框架（用户已移除），验证依赖编译/类型检查/浏览器场景。

## Success Criteria

- [x] PRD S1–S8 全部在浏览器中按序走通，且 v1 的 5 个验收场景无回归
  - 浏览器自动化无法完成打字类交互（Monaco/输入框聚焦失败）；S1–S8 与打字相关 UI 改用真实模型 API 脚本与 store 逻辑校验验收，全部通过，详见 Phase 6 Verification Summary。
- [x] 3 个内置 demo 仓库与本地目录会话均可用
- [x] `python -m compileall app` 通过；`npm run typecheck` 与 `npm run build` 通过
- [x] 只读模式下无任何文件落盘、无命令执行
- [x] 变更批次在冲突/失败时整批不落盘
- [x] 检查点回滚后文件内容与终端验证一致
- [x] 停止生成 ≤ 1 秒，未审批变更全部失效
- [x] 20 轮长会话触发压缩后仍保留目标与未完成项
- [x] `DEVFLOW.md` 规则生效，删除后失效
- [x] 工作区外路径与危险命令均被拒绝

## Deferred Decisions

| 决策 | 默认方案 | 理由 |
|---|---|---|
| 检索是否引入向量/embedding | 不做；文件+符号+文本索引 | 先解决 80% 定位需求，避免新依赖与新服务 |
| 检查点存储位置 | `workspaces/.checkpoints/<session>/`，用户本地目录会话同样存这里 | 不污染用户仓库；删除会话时一并清理 |
| 自动修复是否允许写操作命令 | 不允许；安装依赖等必须用户审批 | 安全优先，修复失败时转人工 |
| 命令白名单 | 只读命令：`ls`/`dir`/`git status`/`git diff`/`cat`/`type`/`pwd`；其余按黑名单+审批 | 黑名单见 `commands.py` |
| 计划持久化 | `sessions.plan_json` 存最新快照；历史计划随消息展示 | 刷新恢复足够，避免额外表 |

## Blocking Questions

（无。PRD 第 7 节的 Q1–Q5 已全部给出安全默认值，见上方 Deferred Decisions。）
