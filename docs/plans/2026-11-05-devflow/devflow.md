# DevFlow MVP Implementation Plan

**Created:** 2026-11-05
**Status:** Done
**Source Spec:** `docs/specs/2026-11-05-devflow-design.md`

## Overview

**Goal:** 一个浏览器内的 AI coding agent（仿 Trae 界面），后端 FastAPI + Python 控制 Docker 沙箱，MVP 阶段必须能在面试演示时依次跑通 spec 里定义的 5 个验收场景。

**Approach:** 5 个阶段顺序推进，每阶段末尾有独立验证和 review point，不会做到一半再返工架构。Phase 1 起基础设施和后端骨架，Phase 2 起 Agent 内核（Provider + 4 工具 + loop），Phase 3 起后端 API 路由（SSE + WebSocket + 持久化），Phase 4 起前端（三栏布局 + Monaco + xterm + shadcn + 品牌绿视觉），Phase 5 联调所有验收场景 + 补 idle 回收。

> **⚠ 本地模式调整（2026-11-05，用户确认）：** Docker Desktop 无法启动，改为**本地模式**开发：
> - **沙箱**：`services/sandbox.py` 的 `SandboxService` 用本地子进程实现（每会话一个工作目录），**接口签名与 Docker 版 ContainerService 完全一致**（create_session_container / exec / stop_and_remove / write_file / read_file / list_files），Docker 恢复后可无改动换实现。Docker 版资源限制参数保留在代码注释和配置中。
> - **数据库**：SQLite（aiosqlite）替代 Postgres，SQLAlchemy 模型 / Alembic 不变，切 Postgres 只改 DSN。
> - **/health** 返回 `{"ok": true, "db": true, "sandbox": true}`。
> - 安全边界变化：本地模式 exec 在宿主机执行，隔离性弱于容器——生产部署必须切回 Docker 模式。

**Tech Stack:**

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 + FastAPI + asyncpg + SQLAlchemy Async + Pydantic v2 + docker SDK + httpx |
| 数据库 | Postgres 16（Docker Compose） |
| 前端 | React 19 + TypeScript + Vite + Monaco + xterm.js + Tailwind + shadcn/ui + Zustand |
| 沙箱镜像 | node:20-alpine + git + ripgrep + 3 个预设 demo repo |
| 部署 | Docker Compose（backend + postgres + sandbox build） |

## Files and Responsibilities

| File/Module | Action | Responsibility | Notes |
|---|---|---|---|
| `backend/pyproject.toml` | Create | 后端依赖声明 | fastapi, uvicorn[standard], asyncpg, sqlalchemy[asyncio], pydantic-settings, docker, httpx, python-jose, passlib[bcrypt] |
| `backend/app/main.py` | Create | FastAPI app 入口 | lifespan 初始化 Postgres、Docker daemon、idle 回收定时任务 |
| `backend/app/core/config.py` | Create | pydantic-settings 配置 | `DOCKER_HOST`, `PG_DSN`, `DEEPSEEK_API_KEY`, `CONTAINER_LIMITS_*`, `MAX_STEPS_PER_RUN=25`, `MAX_TOKENS_PER_RUN=200000` |
| `backend/app/core/security.py` | Create | 路径校验纯函数 | 拒绝绝对路径、`..`、跳出 `/workspace` 的任何路径。零依赖。单测覆盖 |
| `backend/app/core/events.py` | Create | SSE 事件类型（Pydantic） | `message.delta`, `tool.call`, `tool.result`, `patch.proposed`, `run.output`, `error`, `done` |
| `backend/app/db.py` | Create | async engine + sessionmaker | `async_session()` 依赖注入，Base metadata |
| `backend/app/models/*.py` | Create | 4 个 ORM 模型 | Session, Message, ToolCall, Usage |
| `backend/app/schemas/*.py` | Create | Pydantic 请求/响应 schemas | |
| `backend/app/services/container.py` | Create | Docker SDK 封装 | `create`, `exec`, `stop`, `remove`, `write_file`, `read_file`, `list_files`。所有 `create` 必须带资源限制和 `--cap-drop=ALL --security-opt=no-new-privileges --user=node` |
| `backend/app/services/workspace.py` | Create | 容器懒初始化 + idle 回收 | `ensure_container(session_id)`, 启动时挂载 `/workspace` 并从镜像复制预设 demo repo；idle 回收用 `asyncio.create_task` 定时扫描 |
| `backend/app/services/auth.py` | Create | JWT + 用量记录 | 会话创建时签发 token（有效期 24h）；SSE/WS 端点校验；每 run 结束写 usage |
| `backend/app/services/agent/provider.py` | Create | LLM Provider adapter | 接口 `LLMProvider.chat_completion_stream(messages, tools) → AsyncIterator[ChatCompletionChunk]`；DeepSeek 实现；httpx async client |
| `backend/app/services/agent/tools.py` | Create | 4 个工具 | `list(path)`, `read(path)`, `search(query, path)`, `write_patch(path, search, replace)`。**所有入参必须过 `core.security.validate_workspace_path`** |
| `backend/app/services/agent/loop.py` | Create | Agent 主循环 | 系统 prompt + 历史消息 + tools → Provider → tool_call → tools.call → 回灌 → 循环；`max_steps=25`, `max_tokens=200000`；RecentToolCallTracker 软检测死循环（连续 3 次相同 hash → 注入提示词） |
| `backend/app/services/agent/context.py` | Create | 上下文压缩 | 间接压缩：token 超限时只保留最近 N 轮 + system prompt，截断早期消息 |
| `backend/app/api/sessions.py` | Create | POST/GET/DELETE /sessions | 创建时签发 JWT；GET 返回会话详情 + 历史消息；DELETE 触发容器 stop+remove |
| `backend/app/api/files.py` | Create | GET/POST /files/{session_id}/{encoded_path} | list（目录）/ read（文件）/ write；**所有 path 必须过 `core.security` 校验** |
| `backend/app/api/agent.py` | Create | POST /sessions/{id}/run + SSE /agent/stream/{session_id} | run 返回 task_id；SSE 连接触发 loop 启动，事件实时推 |
| `backend/app/api/terminal.py` | Create | WebSocket /terminal/ws/{session_id} | accept → ensure_container → exec_shell hijack → 双向 stdin/stdout |
| `backend/app/api/health.py` | Create | GET /health | 返回 `{"ok": true, "postgres": true, "docker": true}` |
| `backend/docker/Dockerfile.sandbox` | Create | 沙箱基础镜像 | `node:20-alpine` + `git` + `ripgrep`，build 时 `COPY demo_repos/` 三个预设 repo |
| `backend/docker/docker-compose.yml` | Create | dev 编排 | backend（mount `./backend:/app`）+ postgres（volume 持久化） |
| `backend/tests/` | Create | pytest 测试 | test_security.py, test_provider.py（mock）, test_loop.py（mock LLM） |
| `frontend/package.json` | Create | 前端依赖 | react, vite, monaco-editor, @monaco-editor/react, xterm, @xterm/xterm, tailwindcss, zustand, react-router-dom |
| `frontend/src/main.tsx` | Create | React 入口 | |
| `frontend/src/App.tsx` | Create | 三栏布局外壳 | 左 FileTree 260px + 中 Monaco flex + 右 ChatPanel 380px；底部 Terminal 200px 可拖拽 |
| `frontend/src/components/FileTree.tsx` | Create | 文件树 | Zustand useWorkspaceStore；容器未就绪时 loading |
| `frontend/src/components/MonacoEditor.tsx` | Create | Monaco + autosave | debounce 300ms；Dark+ 主题 |
| `frontend/src/components/ChatPanel.tsx` | Create | 对话面板 + SSE | 流式渲染 + 工具卡片 + Diff 审批；签名元素 |
| `frontend/src/components/ToolCard.tsx` | Create | 工具调用卡片 | 可展开入参、执行时长、结果摘要 |
| `frontend/src/components/PatchDiff.tsx` | Create | Diff 审批 | 左右对比 + 接受/拒绝按钮 |
| `frontend/src/components/Terminal.tsx` | Create | xterm + WS 桥 | 连接状态 indicator |
| `frontend/src/components/SandboxStatus.tsx` | Create | 沙箱状态指示器 | green/yellow/red + 重试按钮 |
| `frontend/src/components/TopBar.tsx` | Create | 顶栏 | Logo + demo repo 下拉 + 新会话按钮 |
| `frontend/src/stores/*.ts` | Create | Zustand stores | useSessionStore, useWorkspaceStore, useEditorStore, useChatStore, useTerminalStore |
| `frontend/src/hooks/useSSE.ts` | Create | SSE Hook | fetch + ReadableStream；断线每 2s 重连 + lastEventId |
| `frontend/src/hooks/useTerminalWS.ts` | Create | WS Hook | WebSocket；每 1s 重连 + 指数退避 |
| `frontend/src/theme.ts` | Create | 品牌色 tokens | `--brand: #0FDC78`, `--bg: #171717`, `--text-muted: #A1A1AA`, 半径 8/12px |
| `frontend/src/index.css` | Create | Tailwind + 全局 tokens | |

## Interface Implementation Map

| 设计决策 | 来源 Spec 章节 | 实施位置 | 验收方式 |
|---|---|---|---|
| 三栏布局：左 260px + 中 flex + 右 380px + 底 200px | Layout | `App.tsx` + Tailwind | 宽屏打开后 3 栏并列、各独立滚动 |
| 深色 IDE 主背景 #171717 + Monaco Dark+ | Visual System | `theme.ts` + Monaco options | 打开页面看到暗色界面 |
| 品牌绿 #0FDC78 作为 agent 面板标题/接受按钮 | Visual System | `theme.ts` + ToolCard + PatchDiff | 工具卡片标题、接受按钮边框为绿色 |
| 签名元素：可展开工具调用卡片 | Visual System / 组件表 | `ToolCard.tsx` | agent 执行工具后出现可展开卡片 |
| 沙箱状态指示器 green/yellow/red + 重试 | 组件表 | `SandboxStatus.tsx` | 容器启动时 yellow、就绪 green、失败 red+重试 |
| Diff 审批接受/拒绝按钮 | 组件表 / 场景 3 | `PatchDiff.tsx` | SSE patch.proposed 事件后渲染 |
| SSE 流式渲染（光标闪烁） | 组件表 | `ChatPanel.tsx` | agent 输出逐字流出、光标闪烁 |
| Monaco autosave debounce 300ms | 组件表 | `MonacoEditor.tsx` | 停止输入 300ms 后触发保存 |
| xterm 连接中/就绪/断开状态 | 组件表 | `Terminal.tsx` | WS 连接状态 indicator |
| F5 刷新后会话恢复 | 场景 5 | Zustand 初始化 fetch + Postgres | 刷新后消息、工具卡片、状态都在 |

## Phases

### Phase 1: Infrastructure + Backend Skeleton
**File:** `phase-1-infra-backend-skeleton.md`
**Goal:** 本地模式下后端骨架跑起来。SQLite 能连、sandbox 工作目录能建、`exec("node -e ...")` 能执行、FastAPI `/health` 返回 ok、路径校验单测全部通过。
**Tasks:** 5
**Status:** Done
**Completion:** √

### Phase 2: Agent Kernel
**File:** `phase-2-agent-kernel.md`
**Goal:** Provider adapter + 4 工具 + loop + RecentToolCallTracker 软检测死循环，能用 Python 脚本 mock LLM 完整跑一次 loop
**Tasks:** 4
**Status:** Done
**Completion:** √

### Phase 3: API Routes + SSE + WS + Persistence
**File:** `phase-3-api-routes-and-streams.md`
**Goal:** 所有 FastAPI 路由 + SSE 流式 agent + WebSocket 终端 + 4 表 SQLite，能用脚本触发完整 session → run → SSE 流
**Tasks:** 5
**Status:** Done
**Completion:** √

### Phase 4: Frontend UI + Three-Column Layout
**File:** `phase-4-frontend-ui.md`
**Goal:** React SPA 三栏布局 + Monaco + xterm + shadcn + Zustand stores + SSE/WS hooks + 品牌绿视觉，浏览器打开能看到完整界面 + 能连后端 + SSE/WS 跑通
**Tasks:** 6
**Status:** Done
**Completion:** √

### Phase 5: Diff Approval + Refresh Recovery + Full E2E
**File:** `phase-5-diff-recovery-e2e.md`
**Goal:** Diff 审批卡片 + F5 刷新恢复 + sandbox idle 回收定时任务 + 5 个验收场景全部走通
**Tasks:** 4
**Status:** Done
**Completion:** √

## Dependencies

- **本地模式（当前）**：无需 Docker；SQLite 文件库随仓库生成（gitignore）
- **Docker 模式（延后）**：Windows Docker Desktop 修复后，将 `SandboxService` 换回 Docker 实现、DSN 换 Postgres
- DeepSeek API key（开发时放 `backend/.env`，生产放 systemd/env）
- Python 3.12（`C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe`）
- Node.js 20+ + npm（本机 v24.15.0）

## Success Criteria

- [x] 5 个验收场景全部能在浏览器内按序走通
- [x] 后端 `/health` 返回 `{"ok": true, "db": true, "sandbox": true}`（本地模式键名）
- [x] pytest `tests/` 下所有测试通过（141 passed；前端 35 passed）
- [x] `core.security` 路径校验单测全部通过（覆盖合法路径、`..`、绝对路径、空值）
- [x] `SandboxService` 接口与 Docker 版签名一致；本地实现所有命令仅在会话工作目录内执行；Docker 实现的资源限制参数（--cpus=1 --memory=1g --pids-limit=256 --cap-drop=ALL --security-opt=no-new-privileges --user=node）保留在配置与注释中
- [x] API key 不出现在前端、不写入数据库
- [x] SSE 事件类型与 spec `core/events.py` 定义一致
- [x] 5 个验收场景数字化指标可达到（workspace 创建 ≤ 5s、SSE 首包 ≤ 500ms、WS 就绪 ≤ 2s、刷新恢复实测 65–101ms）

## Blocking Questions

（无）

## Deferred Decisions

| 决策 | 默认方案 | 备注 |
|---|---|---|
| 预设 demo repo 选哪 3 个 | todo-app（React + Node）、express-demo、python-flask-demo | 开发过程中可换 |
| LLM 选 DeepSeek 还是通义千问 | DeepSeek，Provider adapter 可切 | adapter 层写完切换零成本 |
| Postgres 部署 | MVP 和 backend 同 Docker Compose | 生产拆分到独立实例 |
| SSE/WS 断线重连策略 | SSE 2s + lastEventId；WS 1s + 指数退避 | MVP 够用 |
| 前端状态库 | Zustand | |
| shadcn/ui 要不要 | 要 | |
| idle 回收实现 | FastAPI lifespan 内 `asyncio.create_task` | |
