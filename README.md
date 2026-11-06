# DevFlow

[![CI](https://github.com/bzppp-z/ide-agent-kaifa/actions/workflows/ci.yml/badge.svg)](https://github.com/bzppp-z/ide-agent-kaifa/actions/workflows/ci.yml)

**技术栈**：Python 3.12 · FastAPI · React 19 · TypeScript · SQLite · Monaco / xterm

> 一个跑在本地的 AI Coding Agent：用自然语言让模型读写你的代码、执行命令，每一步改动都能审批、回滚、复盘。

DevFlow 不是聊天界面加一个"生成代码"按钮。它把模型放进一个受控的 Agent Loop 里：模型只负责决策，真正的文件读写、检索和命令执行由后端工具完成，并通过 SSE 把整个过程实时推到浏览器。

## 功能

### Agent 能力

- **流式 Agent Loop**：`message.delta → tool.call → tool.result → ... → done`，支持多轮工具调用
- **工具集**：`list` / `read` / `search_text` / `search_files` / `find_symbols` / `write_file` / `apply_patch` / `write_patch` / `delete_file` / `run_command` / `update_plan`
- **任务规划**：多文件任务先产出计划，执行中持续更新状态
- **上下文管理**：按 token 预算压缩历史，保留目标与未完成项；超限时降级截断
- **仓库检索**：符号索引 + 文本检索，1 万文件仓库冷构建约 21.9s，检索 0.7–1.0ms
- **双 Provider**：DeepSeek（OpenAI 兼容）或自定义模型；没有可用 key 时自动降级到 MockProvider

### 安全与可控

- **三级权限**：`readonly`（只读）/ `approval`（逐次审批）/ `auto`（自动应用编辑，命令仍需确认）
- **路径防线**：所有读写都经过 workspace 相对路径校验，拒绝绝对路径与 `..` 穿越
- **变更审批**：单文件 patch 先 dry-run 生成 diff，用户接受后才落盘；多文件任务走 change set 逐文件决议
- **检查点回滚**：修改前惰性快照，可回滚到任意检查点（实测 0.16s），之后的改动标记为 reverted
- **命令管控**：只读命令直接执行，其余需审批；被拒绝的调用不会重复提交

### 工作区与编辑器

- **两种工作区**：内置 3 套 demo 仓库（`todo-app` / `express-demo` / `python-flask-demo`），或直接接入本地目录
- **Monaco 多标签编辑器**：300ms 自动保存、未保存提示、F5 刷新恢复
- **变更面板 / 检查点面板**：查看本次会话的全部改动与历史快照
- **内置终端**：xterm + WebSocket，按会话隔离

## 架构

```text
浏览器（React + Monaco 编辑器 + xterm 终端 + Zustand）
   │  HTTP / SSE / WebSocket
   ▼
FastAPI
   ├─ REST 路由 ───────────────► SQLite（会话 / 消息 / 事件 / 变更记录）
   ├─ AgentLoop ───► LLM Provider（DeepSeek / MockProvider）
   │      │
   │      └────────► Tool Registry ──► Workspace / Sandbox（本地目录）
   ├─ Checkpoint Service ──────► 本地 Workspace 快照
   └─ Repo Index ──────────────► 符号索引 / 文本检索
```

一次请求的完整链路：浏览器 `POST /sessions/{id}/run` → 后端启动 AgentLoop → 模型返回工具调用 → 工具真实操作 workspace → 结果回灌模型 → SSE 把事件推回浏览器 → 需要审批时挂起等待 → `done` 结束。刷新页面后从 SQLite 重建历史。

## 快速开始

**前置条件**：Python 3.12、Node.js 18+、Windows（一键脚本面向 PowerShell；其它平台可按下方手动步骤运行）。

### 一键启动（Windows）

双击根目录的 `start-devflow.bat`：

- 自动检查并启动后端（`http://localhost:8010`）
- 自动安装前端依赖并启动 dev server（`http://localhost:5173`）
- 等待健康检查通过后自动打开浏览器

停止：双击 `stop-devflow.bat`（释放 8010 / 5173）。日志在 `%LOCALAPPDATA%\DevFlow\logs`。

### 手动启动

后端（终端 A）：

```powershell
cd backend
pip install -e .
python -m uvicorn app.main:app --port 8010
```

健康检查：

```powershell
Invoke-RestMethod http://localhost:8010/health
# ok db sandbox
# True True True
```

前端（终端 B）：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。后端端口变更时，同步修改前端 `.env.local` 中的 `VITE_API_BASE`。

### 使用真实模型（可选）

默认使用 MockProvider，无需 key 即可演示完整链路。要接真实模型：

```powershell
Copy-Item backend\.env.example backend\.env
```

在 `backend/.env` 中设置：

```ini
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

也可以在前端顶栏添加任意 OpenAI 兼容的自定义 Provider。

## 关键配置

配置见 `backend/app/core/config.py`，字段与 `backend/.env.example` 一一对应。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./devflow.db` | 数据库连接 |
| `WORKSPACE_ROOT_DIR` | `./workspaces` | 会话工作区根目录 |
| `LLM_PROVIDER` | `mock` | `mock` / `deepseek` |
| `BACKEND_PORT` | `8010` | 后端监听端口 |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:5174` | 允许的前端来源 |
| `MAX_STEPS_PER_RUN` | `25` | 单次运行最大步数 |
| `MAX_TOKENS_PER_RUN` | `200000` | 单次运行 token 预算 |
| `CONTAINER_IDLE_TIMEOUT_MIN` | `30` | 工作区 idle 回收时间 |
| `REPO_INDEX_MAX_FILES` | `10000` | 索引文件数上限 |
| `MAX_FILES_PER_CHANGE_SET` | `10` | 单批次多文件变更上限 |

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查（db / sandbox） |
| `POST` | `/sessions` | 创建会话（demo 仓库或本地目录） |
| `GET` | `/sessions/{id}` | 会话详情与历史事件 |
| `DELETE` | `/sessions/{id}` | 删除会话 |
| `PATCH` | `/sessions/{id}/preferences` | 修改权限模式 |
| `POST` | `/sessions/{id}/run` | 发起一次 Agent 运行 |
| `GET` | `/agent/stream/{id}` | SSE 事件流 |
| `POST` | `/sessions/{id}/stop` | 停止当前运行 |
| `PATCH` | `/agent/patch/{id}` | 接受 / 拒绝单个 patch |
| `PATCH` | `/agent/change-set/{id}` | 多文件变更逐文件决议 |
| `PATCH` | `/agent/command/{id}` | 接受 / 拒绝命令 |
| `GET/POST` | `/files/{id}/{path}` | 读取 / 写入文件 |
| `GET` | `/sessions/{id}/changes` | 变更清单 |
| `GET` | `/sessions/{id}/checkpoints` | 检查点列表 |
| `POST` | `/sessions/{id}/checkpoints/{cid}/rollback` | 回滚到检查点 |
| `GET/POST/PATCH/DELETE` | `/providers...` | 模型 Provider 管理 |
| `GET` | `/local/home`、`/local/browse` | 本地目录浏览 |
| `WS` | `/terminal/ws/{id}` | 会话终端 |

## 目录结构

```text
.
├── backend/
│   ├── app/
│   │   ├── api/            # REST / SSE / WebSocket 路由
│   │   ├── core/           # 配置、安全、SSE 事件、路径校验
│   │   ├── models/         # SQLAlchemy 模型
│   │   ├── schemas/        # Pydantic 请求/响应模型
│   │   └── services/       # agent loop、工具、沙箱、索引、检查点
│   ├── scripts/            # 冒烟脚本与快速上手文档
│   └── pyproject.toml
├── frontend/
│   └── src/
│       ├── components/     # 编辑器、文件树、对话、变更/检查点面板
│       ├── hooks/          # SSE、终端
│       ├── lib/            # API 客户端与类型
│       └── stores/         # Zustand 会话/编辑器状态
├── scripts/                # 一键启动 / 停止脚本
├── docs/                   # 设计文档与分阶段计划
└── start-devflow.bat
```

## 测试与质量

目前 CI 覆盖：

- 前端 `npm run typecheck` + `npm run build`
- 后端依赖安装、字节码编译与 `app.main` 导入冒烟

手工冒烟脚本：

```powershell
python backend/scripts/mock_loop_demo.py
```

它用 MockProvider + 真实本地沙箱跑一次完整 Agent Loop，打印全部 SSE 事件（`tool.call → tool.result → message.delta → done`）。

自动化回归（pytest + Playwright）尚在补充中，见下方 Roadmap。项目当前**没有**单元测试套件，请勿把上述脚本当成自动化测试。

## 已知限制

- **默认 MockProvider**：无 key 时只按固定剧本演示，不代表真实模型能力；评估效果需切换 DeepSeek。
- **本地执行隔离较弱**：`run_command` 直接在宿主机执行，隔离性弱于容器；生产部署应切回容器模式。
- **单用户 SQLite**：无多租户、无并发写入隔离，不适合直接对外多用户部署。
- **索引性能**：1 万文件冷构建约 21.9s（Windows Defender 扫描 + 非 ASCII 路径的 stat/read 开销），检索本身 0.7–1.0ms。
- **命令首包延迟**：约 0.87–0.91s。
- **启动脚本面向 Windows**：其它平台按手动步骤运行。
- **无自动化测试**：回归依赖冒烟脚本与阶段验收清单。

## Roadmap

- [ ] pytest 最小回归集（AgentLoop、路径校验、权限矩阵、检查点、SSE 集成）
- [ ] Playwright 前端冒烟（建会话 → 发消息 → 等待 `done`）
- [ ] Docker Compose 一键部署（后端 + 前端）
- [ ] 增量仓库索引与缓存
- [ ] Postgres 切换与多用户支持

## 更多文档

- [后端说明](backend/README.md)
- [快速上手与 5 场景演示](backend/scripts/QUICKSTART.md)
- [设计文档](docs/specs/2026-11-05-devflow-design.md)
- [开发计划](docs/plans/2026-11-06-devflow-v2/devflow-v2.md)
