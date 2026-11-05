# DevFlow — 浏览器内 AI Coding Agent 设计文档

## Context

目标用户是全栈偏后端方向的实习生。简历上需要一个"有后端故事可讲"的项目：前端像 Trae 的界面（文件树 + Monaco + 对话面板 + 终端），后端负责真实的代码执行沙箱、agent 编排、流式事件分发、可观测性。

当前工作区是一张白纸，无 git 仓库、无历史文件、无技术约定。所有设计决策在这份文档中首次定义。

**简历一句话定位**：浏览器内 AI coding agent，后端 Docker 沙箱 + FastAPI + 流式 SSE/WebSocket 协议 + diff 审批，解决补丁可靠性问题。

## Goals

- 一个可以在浏览器打开即可使用的 AI coding agent 界面，视觉风格向 Trae 靠拢。
- 后端使用 Python 3.12 + FastAPI 实现：会话管理、agent 循环、4 个工具（list / read / search / write+patch）、流式对话、diff 审批、容器生命周期。
- Docker 沙箱隔离执行，容器有明确的资源限制（cpu / memory / pids / cap-drop）。
- Postgres 持久化会话、消息、工具调用、用量统计。
- 模型层做 Provider adapter，MVP 先接 DeepSeek（OpenAI 兼容协议），可切换。
- 本地 Docker Desktop 跑通闭环后，可一键部署到带 Docker 的 VPS。
- 能在简历上讲清楚：架构分层、安全边界、事件协议、容器隔离、流式通信、用量统计。

## MVP 第一版验收标准（以下 5 个场景必须全部能在浏览器内走通）

面试演示时按顺序跑完这 5 步，每步不超过 1 分钟。5 步全过 = MVP 完成，简历里"DevFlow"项目就算立住了。

**场景 1：创建会话 + 浏览工作区**
- 进入页面 → 从顶栏下拉选一个 demo repo → 点击"新建会话"
- 左侧文件树显示该 repo 的目录结构
- 容器状态指示器变绿色（表示容器已就绪）
- 数字化验收：容器首次启动 ≤ 5 秒（本地 Docker Desktop）

**场景 2：Agent 读取 + 搜索文件（SSE 流式）**
- 在对话面板输入："帮我看看 src/ 目录里都有哪些文件，路由是怎么定义的"
- 对话面板以 SSE 流式逐步出现：
  - `event: tool.call` → list 工具卡片
  - `event: tool.result` → list 结果摘要
  - `event: tool.call` → search 工具卡片
  - `event: tool.result` → search 结果摘要
  - `event: message.delta` → agent 总结文本（逐字流出）
  - `event: done`
-  Monaco 编辑器在点击文件树某文件后显示对应内容
- **数字化验收**：agent 完成整个回答 ≤ 15 步、SSE 首包延迟 ≤ 500ms

**场景 3：Agent 写代码 + Diff 审批**
- 接着上一句说："帮我在路由里新增一个 /healthz 端点"
- SSE 流中出现 `event: patch.proposed` 卡片，左右对比显示改动
- 用户点击"接受" → SSE 流继续，Monaco 编辑器自动刷新该文件内容
- 拒绝（作为测试）：点击"拒绝" → agent 重新规划、输出新的补丁方案
- **数字化验收**：补丁接受后，容器内 `/workspace/src/...` 实际文件被改动（用场景 4 的终端验证）

**场景 4：终端执行命令（WebSocket 双向）**
- 底部 xterm 终端输入：`cd src && node check.js` 或 `npm test`（demo repo 里自带）
- 终端输出对应结果（成功 / 失败都可以，能输出就行）
- 终端输入输出是实时双向的，能看到命令执行
- **数字化验收**：WebSocket 连接到就绪 ≤ 2 秒，输出延迟 ≤ 300ms

**场景 5：Postgres 持久化 + 刷新恢复**
- 浏览器按 F5 刷新页面
- 回到同一个会话 → 之前的用户消息、agent 回复、工具调用卡片、补丁状态全部还在
- 后端 Postgres 中 `sessions`、`messages`、`tool_calls` 表有对应记录
- **数字化验收**：刷新后重新渲染完整会话 ≤ 1 秒

**第一版的"绝对不做"清单**（宁可砍功能，也不碰这些）：
- 自动验证闭环（agent 改完自动跑测试）—— 加在 v2
- 多会话切换 + 历史列表 —— 砍，MVP 单会话
- 前端 Trace 面板 —— 砍，后端有日志
- 用户上传 zip / git clone —— 砍，只用预设 demo repo
- Eval 脚本 + 任务成功率统计 —— 砍，项目跑通后补
- 容器预热池 —— 砍

## Non-Goals（刻意不做）

- 多用户权限、登录注册、组织隔离。MVP 单用户，JWT 仅用于前端鉴权。
- 容器预热池和排队调度（资源紧张时可能需要，但 MVP 不做）。
- 自动验证闭环（agent 改完自动跑测试再判断结果），留到后续迭代。
- Trace 面板 / 可观测性 UI（后端 pino 结构化日志有了，前端面板先不做）。
- 用户上传 zip、git clone 进容器、File System Access API 读本地文件——工作区只有预设 demo repo。
- 插件系统、自定义命令、多 agent 协作、并发会话。

## Recommended Approach

**选一套即可，无 credible 替代方案。**

整体方案：浏览器 React SPA + FastAPI 后端 + Postgres + Docker SDK for Python 控制单 VPS 上的容器 daemon，每个会话一个容器，bind mount 工作区，资源限制明确。前端用 SSE 接收对话/工具流、WebSocket 连终端。

为什么没有替代方案——

| 看起来的替代 | 为什么不成立 |
|---|---|
| Node 后端 | 目标岗位是全栈偏后端，Python 后端生态面试官更熟；FastAPI 原生 SSE/WebSocket 比 Node 手写流更干净 |
| 纯前端 WebContainer | 只有 Node 生态，demo 依赖 Vercel 配 COOP/COEP，面试时讲不出"后端隔离"的后端故事 |
| Python 但用 FastAPI 以外的框架（Flask / Django） | Flask 原生没有 async 和 SSE；Django 太重且 async/SSE 支持不如 FastAPI 原生干净 |
| SQLite 代替 Postgres | 会话量极小确实够用，但面试时 Postgres + asyncpg + SQLAlchemy Async 的后端故事更扎实 |

## Alternatives Considered

本次无额外替代方案需要记录。

## Architecture

四层，三个进程（浏览器 + FastAPI + Postgres）+ 一个 Docker daemon：

```
┌──────────────────────────────────────────────────────┐
│ 浏览器（React SPA）                                   │
│ ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│ │ 文件树 │ │ Monaco   │ │ 对话面板 │ │ xterm   │   │
│ │        │ │ 编辑器   │ │ SSE ←    │ │ WS ↔    │   │
│ └──┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘   │
└───┼──────────────┼───────────┼────────────┼─────────┘
    │ HTTP         │           │ SSE        │ WebSocket
    └──────────────┴───────────┼────────────┘
                              ↓
┌──────────────────────────────────────────────────────┐
│ Nginx（反代 + HTTPS，仅在生产环境）                    │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ FastAPI 后端（Python 3.12）                           │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│ │ api/     │ │ services/│ │ models/  │ │ core/     │ │
│ │ 路由层   │ │ 业务层   │ │ ORM层    │ │ config/   │ │
│ └──────────┘ └────┬─────┘ └────┬─────┘ └───────────┘ │
└───────────────────┼────────────┼─────────────────────┘
                    │            │
     dockerode SDK  │            │ asyncpg
                    ↓            ↓
        ┌──────────────┐   ┌──────────┐
        │ Docker daemon│   │ Postgres │
        │ /var/run/    │   │ 16       │
        │ docker.sock  │   └──────────┘
        └──────┬───────┘
               ↓
        ┌─────────────────────────────────┐
        │ 每个会话一个容器                  │
        │ node:20-alpine + git + ripgrep  │
        │ /workspace（bind mount）          │
        │ 3 个预设 demo repo                │
        │ --cpus=1 --memory=1g             │
        │ --cap-drop=ALL                   │
        │ --security-opt=no-new-privileges  │
        │ --user=node                      │
        │ 空闲 30min stop + remove          │
        └─────────────────────────────────┘

外部：LLM Provider（DeepSeek OpenAI 兼容接口），FastAPI 通过 HTTP 调，API key 只存在服务端
```

**关键设计决策**

1. FastAPI 是唯一的 API gateway，不引入额外网关。HTTP/SSE/WebSocket 都从它出去。
2. 容器懒初始化——会话创建时不起容器，用户第一次发消息时才起。省资源。
3. Bind mount 工作区目录而非 `docker cp`，毫秒级文件读写。
4. 路径校验——后端拒绝任何跳出 `/workspace` 的相对路径请求。
5. 会话 idle 30min 自动 stop + remove 容器，Postgres 里的会话和消息不删。
6. Provider adapter——所有 LLM 调用走 `services/agent/provider.py`，实现一个 `LLMProvider` 接口（`chat_completion_stream`），DeepSeek 是实现之一。
7. Event 层统一——agent 的输出、工具调用结果、patch 提议、执行结果全部序列化为 SSE 事件，前端按 `event` 类型渲染不同卡片。

## Interface Design Contract

### Experience and Journeys

**目标用户**：实习生（你自己或面试官），技术熟悉但对 DevFlow 第一次使用。
**界面单一最重要的工作**：让用户在 30 秒内看到"浏览器里跑着一个真实的 Linux 环境 + 一个能改代码的 AI"。

**核心旅程**：

- 进入应用 → 选择 demo repo → 输入编程任务 → 看 agent 流式输出 + 工具卡片 → 接受 diff 补丁 → 看 xterm 终端输出 → 接受或拒绝 → 继续对话
- 失败恢复：容器启动慢/失败 → 界面显示"正在启动沙箱..."带倒计时 + 重试按钮；agent 调用超时 → 自动终止并提示
- 退出 → 会话在 Postgres 中保留，刷新后可恢复

**信息架构**：

```
┌─────────────────────────────────────────────────┐
│ 顶部栏：DevFlow Logo · Demo Repo 下拉 · 模型 · 新会话 │
├─────────┬───────────────────────┬──────────────┤
│ 左侧    │        中间            │     右侧     │
│ 文件树  │  Monaco 编辑器 + 标签页 │  Agent 对话  │
│         │                       │  (SSE ←)    │
│         │                       │  工具卡片    │
│         │                       │  Diff 审批   │
├─────────┴───────────────────────┴──────────────┤
│ xterm 终端                                      │
│ (WebSocket ↔)                                   │
└─────────────────────────────────────────────────┘
```

### Layout and Responsive Behavior

- 三栏布局：左侧固定宽度 260px，中间 Monaco 弹性，右侧对话面板固定 380px。
- 底部终端高度 200px 固定可拖拽。
- 宽屏（>1280px）三栏并列；窄屏（<1280px）右侧对话面板折叠成抽屉；移动端 MVP 不考虑（全栈后端岗位面试不会看手机）。
- 文件树 / Monaco / 对话面板各自独立滚动。

### Visual System

- **深色 IDE 风**：主背景 `#171717`（与 VS Code 默认 dark 主背景一致），Monaco 用 Dark+ 主题。
- **品牌绿锚点**：`#0FDC78`（品牌色）用于 agent 面板标题、工具调用成功徽章、接受补丁按钮边框和 hover 状态。
- **辅助灰**：消息文字 `#A1A1AA`，文件树次级文字 `#737373`，分割线 `#262626`。
- **签名元素**：右侧对话面板的"工具调用卡片"——每次 agent 执行工具后渲染一个可展开的卡片（显示工具名、入参、执行时长、结果摘要）。这个可验证的、非黑盒的过程视图是 DevFlow 和套壳 ChatGPT 的 IDE 的核心差异。
- 字体：Monaco 内 JetBrains Mono，UI 界面 PingFang SC / Inter。
- 半径默认 8px，卡片/面板 12px。无装饰性渐变、无背景图案。

### Components, Content, and States

| 组件 | 内容 | 状态 |
|---|---|---|
| 文件树 | 预设 demo repo 文件列表 | 默认 / hover / 选中 / loading（容器未就绪） |
| Monaco 编辑器 | 工作区文件内容 | 默认 / 已修改（unsaved 指示器）/ 保存中（autosave debounce） |
| 对话面板 | 用户消息、agent 流式文本、工具卡片、patch 提议 | streaming（光标闪烁）、tool_call（loading spinner）、patch_proposed（接受/拒绝按钮）、done |
| 工具卡片 | 工具名、入参（折叠）、执行时长、结果摘要 | 默认展开入参 / hover 显示详情 / error 结果高亮红色 |
| Diff 审批 | 左右对比视图 + 接受 / 拒绝按钮 | 默认 / hover / 接受中 / 已接受 / 已拒绝 |
| xterm 终端 | shell 提示符、命令、输出 | 连接中（loading spinner）/ 就绪 / 断开（重连按钮） |
| 顶栏 | DevFlow logo、Demo repo 下拉、模型下拉、新会话按钮 | 默认 / hover / repo 切换中（loading） |
| 沙箱状态指示器 | 顶部右侧小圆点 + 文字 | green=就绪、yellow=启动中（百分比）、red=失败（重试按钮） |

### Accessibility and Visual Verification

- 所有交互元素有语义化 HTML 标签和 aria 标签。
- Monaco 编辑器保留原有键盘快捷键。
- 顶栏按钮和侧边栏可通过 tab 顺序聚焦。
- 颜色对比度 ≥ 4.5:1（品牌绿 on dark 背景需验证）。
- 无 motion 偏好下禁用流式光标闪烁和卡片入场动画。
- 验收截图 / 路径：
  1. 默认会话创建后 5 秒内看到文件树内容
  2. 发一条"列出 src 目录"的指令 → 看到 SSE 工具卡片
  3. 发一条"重构路由"指令 → 看到 diff 审批卡片
  4. 终端输入 `pwd && ls` → 看到输出
  5. 沙箱失败 → 看到红 + 重试按钮

## Components（后端）

| Component | Responsibility | Depends On | Notes |
|---|---|---|---|
| `api/sessions.py` | 会话 CRUD + 触发容器懒初始化 | `services/workspace.py` + `models/session.py` | |
| `api/files.py` | 文件树 / 读取 / 保存 | `services/container.py` + `core/security.py` | 所有路径必须过 `core.security.validate_workspace_path` |
| `api/terminal.py` | WebSocket 端点，stdin/stdout 桥 | `services/container.py` + `services/workspace.py` | 首次连接时触发容器 init |
| `api/agent.py` | SSE 流式输出端点 + run 触发 | `services/agent/loop.py` + `models/*` | run 返回 stream，事件实时写入 DB |
| `services/container.py` | Docker SDK 封装：create / exec / stop / remove | `docker` Python 官方包 | 所有容器创建必须带资源限制和安全选项 |
| `services/workspace.py` | 预设 demo repo 加载、容器挂载、idle 回收 | `services/container.py` + `models/session.py` | 定时轮询会话活跃时间，超时 stop + remove |
| `services/agent/loop.py` | Agent 主循环 | `services/agent/tools.py` + `services/agent/provider.py` + `models/*` | 有最大步数限制（25 步）和总 token 限制（200k） |
| `services/agent/tools.py` | 4 个工具实现 | `services/container.py` + `core/security.py` | `list` / `read` / `search`（ripgrep）/ `write_patch`（search/replace 语法） |
| `services/agent/provider.py` | LLM Provider adapter | `httpx` 异步客户端 | 实现 `LLMProvider` 接口：`chat_completion_stream` |
| `services/agent/context.py` | 上下文压缩、token 预算 | `models/message.py` | 会话历史超限时间接压缩 |
| `services/auth.py` | JWT 签发、校验、限流 | `models/usage.py` + `services/agent/loop.py` | 每 run 记录 usage 到 Postgres |
| `core/config.py` | pydantic-settings，所有配置 | `.env` 文件 | 开发时可缺省部分值，生产必须全部提供 |
| `core/security.py` | 路径校验（必须在 `/workspace` 内） | — | 纯函数，不依赖其他模块 |
| `core/events.py` | SSE 事件类型和 JSON schema | `pydantic` | 枚举所有前端会处理的 event.type |

## Components（前端）

| Component | Responsibility | Depends On | Notes |
|---|---|---|---|
| `FileTree` | 文件树渲染 + 目录展开折叠 | Zustand `useWorkspaceStore` | 用 SSE 事件刷新，容器未就绪时显示 loading |
| `MonacoEditor` | 代码编辑 + autosave | Zustand `useEditorStore` | 保存 debounce 300ms |
| `ChatPanel` | 用户输入 + agent 流式渲染 | Zustand `useChatStore` + SSE 连接 | 工具卡片和 diff 审批是独立子组件 |
| `ToolCard` | 工具调用可视化 | ChatPanel | 可展开入参，显示执行时长 |
| `PatchDiff` | search/replace 补丁左右对比 | ChatPanel | 接受/拒绝按钮回调到后端 |
| `Terminal` | xterm.js + WebSocket 桥 | Zustand `useTerminalStore` | 连接状态有 indicator |
| `SandboxStatus` | 沙箱连接状态指示器 | Zustand `useSessionStore` | green/yellow/red + 重试按钮 |
| SSE Hook | `useSSE(sessionId)` | fetch + ReadableStream | 断线自动重连，带 `lastEventId` |
| WS Hook | `useTerminalWS(sessionId)` | WebSocket | 断线自动重连 |

## Data Flow

**核心端到端流程（用户发第一条消息到 agent 回答）**：

```
用户输入消息 → POST /sessions/{id}/run
                ↓
                FastAPI → services/agent/loop.run(sessionId, userMessage)
                ↓
                loop 检查容器是否存在，不存在 → workspace.ensure_container(sessionId)
                                                    ↓
                                                    container.create(limits + security options)
                                                    container.mount_workspace(sessionWorkspaceDir)
                ↓
                loop 构造 system prompt + 历史消息 + 可用工具描述
                ↓
                provider.chat_completion_stream(messages) → 流式接收 LLM 响应
                ↓
                如果 LLM 返回 tool_call → tools.call(tool, input) → container.exec(command)
                                        → 结果回灌给 LLM，继续循环
                ↓
                每个 event → 同时做两件事：
                    1) 写入 Postgres（messages/tool_calls/usage 表）
                    2) 推到 SSE stream，前端收到后渲染卡片
                ↓
                loop 达到 LLM 说 done / 超过最大步数 / 超过最大 token
                → 发 event: done，包含 totalTokens / totalSteps
```

**文件保存流程**：

```
Monaco 编辑 → 300ms debounce → POST /files/{sessionId}/{encodedPath}
                                    body: {"content": "..."}
                                ↓
                                core/security.validate_workspace_path(encodedPath)
                                ↓
                                services/container.write_file(sessionId, path, content)
                                → 容器内 /workspace/{path} 更新
                                ↓
                                返回 200 OK
```

**终端流程**：

```
前端 WebSocket /terminal/ws/{sessionId} 连接
                ↓
                FastAPI WebSocket endpoint accept
                ↓
                workspace.ensure_container(sessionId)
                ↓
                container.exec_shell() → 返回 exec stream id + hijack socket
                ↓
                前端 ↔ WebSocket ↔ FastAPI ↔ hijack socket ↔ 容器 shell
                ↓
                任一侧断开 → FastAPI 关闭 hijack，exec cleanup
```

## Error Handling

| 失败场景 | 响应 |
|---|---|
| 容器启动超时（>30s） | SSE 发 `event: error code=CONTAINER_START_TIMEOUT`，前端沙箱状态变 red + 重试按钮 |
| 容器资源超限被杀 | SSE 发 `event: error code=CONTAINER_OOM` + `event: done totalSteps=... totalTokens=...`（记录已产生的消息） |
| LLM API 超时/网络错误 | 前端重试 SSE 连接，后端 loop 捕获后发 `event: error` 并终止 loop |
| 路径跳出 /workspace | HTTP 403 + 错误信息，**不尝试执行** |
| Postgres 连接失败 | FastAPI lifespan 启动检查就 fail，服务整体不可用 |
| Agent 超过 25 步 | SSE 发 `event: done` + warning 提示"达到最大步数已停止" |
| Agent 超过 200k tokens | SSE 发 `event: done` + warning 提示"token 超限" |
| 用户拒绝补丁 | loop 拿到 tool.result 是 rejected，重新规划下一步 |

## Security

**信任边界**：

1. 容器是唯一不受信任的边界——用户通过 agent 让容器执行什么命令都可能，所以容器必须被严格限制。
2. 容器**绝不**访问 Docker socket（没有 `-v /var/run/docker.sock` 挂进去）。
3. 容器**绝不**挂宿主机任意路径（只挂一个专属 `/workspace` 目录）。
4. 后端路径校验是"最后一道防线"——即使 agent 返回了恶意路径，后端也会拒之门外。

**具体保护措施**：

- 容器资源限制：`--cpus=1 --memory=1g --pids-limit=256 --cap-drop=ALL --security-opt=no-new-privileges --user=node`
- 路径校验：`core/security.validate_workspace_path` 拒绝绝对路径、`..`、符号链接跳出、正则匹配 `/workspace/` 以外的任何字符组合
- JWT：前端请求所有 API 和 SSE/WS 端点必须带 `Authorization: Bearer {token}`，token 由后端在会话创建时签发，有效期 24h
- 限流：每个 run 记录 usage，单会话单次 run 限 200k tokens
- API key：LLM Provider 的 API key 只存在 FastAPI 的 `.env`，**绝不进入前端**，也绝不出现在 Postgres

## Boundaries and Dependencies

| 模块 | 拥有 | 不拥有 | 依赖 |
|---|---|---|---|
| `services/container.py` | 容器创建/执行/销毁的底层操作 | 容器什么时候被创建/销毁（workspace 决定） | `docker` Python 包 |
| `services/workspace.py` | 容器生命周期编排、预设 repo 加载、idle 回收 | 容器如何执行命令（container 决定） | `container.py` + `SQLAlchemy async` |
| `services/agent/loop.py` | agent 主循环（规划→工具→重试→done） | 容器操作、LLM 实现 | `tools.py` + `provider.py` + 所有 models |
| `services/agent/tools.py` | 4 个工具实现（list / read / search / write_patch） | agent 循环流程 | `container.py` + `core/security.py` |
| `services/agent/provider.py` | LLM API 调用、流式响应解析 | agent 循环、工具调用 | `httpx` + OpenAI 兼容接口 |
| `core/security.py` | 路径校验 | 其他任何东西 | 零依赖 |
| 前端 | 所有渲染、用户交互、SSE/WS 连接 | 任何业务逻辑（容器操作、LLM 调用） | 浏览器原生 fetch / WebSocket |

## Testing

**单元测试**（pytest）：
- `core/security.py` 的路径校验函数——覆盖合法路径、`..`、绝对路径、符号链接、空值
- `services/agent/provider.py` 的 `LLMProvider` 接口 mock + 返回值解析
- `services/agent/tools.py` 的 search/replace 逻辑（不需要真实容器，mock container.exec 返回）

**集成测试**（Docker）：
- FastAPI lifespan 启动时连接 Postgres 和 Docker daemon
- 真实容器创建 + 执行 `pwd`
- agent loop 调用 4 个工具的完整流程（mock LLM 返回固定 tool_call）

**E2E 手动验证**：
- 打开浏览器 → 选择 demo repo → 发消息 → 看到 SSE 流 → 接受 diff → 终端执行 → 所有路径可走通
- 沙箱失败时看到 red 状态 + 重试

## Rollout

**开发环境**：
- Docker Desktop（Windows）
- FastAPI `uvicorn run app.main:app --reload --port 8000`
- `docker compose up -d postgres`
- 前端 `npm run dev -- --port 5173`
- 浏览器打开 `http://localhost:5173`

**生产环境**（VPS）：
1. 安装 Docker + docker compose + Nginx + Certbot
2. `docker compose up -d` 启动 backend + postgres
3. 前端构建产物用 Nginx serve，Nginx 反代 `/api/*` → FastAPI 容器端口、反代 `/terminal/ws/*` → WebSocket
4. `docker pull devflow/sandbox:latest`（沙箱容器镜像，包含预设 demo repo）
5. Certbot 配 HTTPS
6. `systemctl enable docker`、容器 idle 回收由 `workspace.py` 定时任务处理（不是 cron）
7. GitHub Actions：push 到 main 后构建前后端镜像 + sandbox 镜像 + 自动部署

## Blocking Questions

（无）

## Deferred Questions（MVP 不阻塞，默认方案已给出）

| 问题 | 默认假设 | 备注 |
|---|---|---|
| 预设 demo repo 选哪 3 个？ | todo-app（React + Node）、express-demo（Node 后端）、python-flask-demo（Python 后端） | 可以在开发过程中换 |
| DeepSeek 还是通义千问？ | 先 DeepSeek，Provider adapter 切换成本低 | |
| Postgres 部署在哪？ | 生产和 backend 都在同一个 Docker Compose 里 | 面试 demo 阶段不拆 |
| 容器 idle 回收用什么？ | FastAPI lifespan 启动一个 `asyncio.create_task` 做定时轮询 | 不用 celery / apscheduler |
| 前端 SSE/WS 重连策略？ | SSE：每 2 秒重连 + `lastEventId`；WS：每 1 秒重连 + 指数退避 | MVP 足够，后续可升级 |
| 前端状态管理 Zustand vs Jotai？ | Zustand | 规模极小，哪个都行 |
| shadcn/ui 要不要？ | 要 | 能 30 分钟搭出 Trae 风的基础布局 |
| 用户上传 zip 或 git clone 进容器？ | MVP 不做，预设 demo repo 够用 | 加在 Roadmap |
| 自动验证闭环（改完自动跑测试）？ | MVP 不做，agent 改完直接 done | 加在 Roadmap |
| Trace 面板前端 UI？ | MVP 不做，后端有结构化日志够用 | 加在 Roadmap |
| Eval 数据集（任务成功率/平均步数）？ | MVP 不做，项目跑通后补 10 个任务仓库 | 加在 Roadmap |

---

## Appendix: Complete Project Framework

### 完整目录树

```
devflow/
│
├── backend/                          # ===== Python FastAPI 后端 =====
│   ├── pyproject.toml                # hatchling backend，所有依赖声明
│   ├── README.md                     # 本地运行说明（postgres + sandbox + uvicorn）
│   ├── .env.example                  # 所有环境变量模板
│   ├── Dockerfile                    # backend 自身镜像（生产部署用）
│   ├── docker-compose.yml            # postgres + sandbox-build 编排（开发环境）
│   │
│   ├── docker/                       # ===== 沙箱容器镜像 =====
│   │   ├── Dockerfile.sandbox        # node:20-alpine + git + ripgrep
│   │   └── .dockerignore
│   │
│   ├── alembic/                      # ===== 数据库迁移 =====
│   │   ├── env.py
│   │   └── versions/
│   │       └── 20261105_0001_init.py # init migration：4 张表
│   │
│   ├── scripts/                      # ===== 开发辅助脚本 =====
│   │   ├── mock_loop_demo.py         # mock LLM + mock container 跑 agent loop
│   │   └── e2e_curl_demo.md          # curl 完整验证清单
│   │
│   ├── app/                          # ===== 后端应用主包 =====
│   │   ├── __init__.py
│   │   ├── main.py                   # FastAPI 入口 + lifespan + CORS + 中间件
│   │   ├── db.py                     # async engine + async_sessionmaker + Base
│   │   │
│   │   ├── core/                     # ===== 零依赖基础设施 =====
│   │   │   ├── config.py             # pydantic-settings（所有 .env 变量集中加载）
│   │   │   ├── security.py           # 路径校验纯函数：validate_workspace_path
│   │   │   └── events.py             # SSE 事件类型枚举（Pydantic 定义 + to_sse_str）
│   │   │
│   │   ├── models/                   # ===== SQLAlchemy ORM（async） =====
│   │   │   ├── __init__.py
│   │   │   ├── session.py            # Session: id, demo_repo, container_id, created_at, last_active_at
│   │   │   ├── message.py            # Message: id, session_id, role, content, created_at
│   │   │   ├── tool_call.py          # ToolCall: id, session_id, tool_name, input_json, result_json, ok, duration_ms, created_at
│   │   │   └── usage.py              # Usage: id, session_id, run_id, total_tokens, total_steps, created_at
│   │   │
│   │   ├── schemas/                  # ===== Pydantic 请求/响应 =====
│   │   │   ├── __init__.py
│   │   │   ├── session.py            # SessionCreate / SessionResponse / SessionDetailResponse
│   │   │   ├── message.py            # MessageResponse
│   │   │   ├── agent.py              # AgentRunRequest / PatchRespondRequest
│   │   │   └── file.py               # FileWriteRequest
│   │   │
│   │   ├── services/                 # ===== 业务层 =====
│   │   │   ├── container.py          # Docker SDK 封装：create/exec/stop/remove/write_file/read_file/list_files
│   │   │   ├── workspace.py          # 容器懒初始化 ensure_container + idle 30min 回收 run_idle_reclaim_loop
│   │   │   ├── auth.py               # JWT create_session_token / verify_session_token + record_usage
│   │   │   └── agent/                # Agent 内核
│   │   │       ├── __init__.py
│   │   │       ├── provider.py       # LLMProvider 抽象接口 + DeepSeek 实现（httpx 流式 + 指数退避重试 × 3）
│   │   │       ├── tools.py          # 4 工具：list / read(2000行) / search(ripgrep, 100条) / write_patch(单匹配)
│   │   │       ├── loop.py           # AgentLoop：while 循环 + max_steps=25 + max_tokens=200k + RecentToolCallTracker(软检测死循环, window=3) + asyncio.Event patch 暂停
│   │   │       └── context.py        # 上下文间接压缩（token 超限时截断早期 tool.result）
│   │   │
│   │   └── api/                      # ===== FastAPI 路由层 =====
│   │       ├── __init__.py
│   │       ├── health.py             # GET /health
│   │       ├── sessions.py           # POST /sessions · GET /sessions/{id} · DELETE /sessions/{id}
│   │       ├── files.py              # GET /files/{session_id}/{path:path} · POST /files/{session_id}/{path:path}
│   │       ├── agent.py              # POST /sessions/{id}/run · GET /agent/stream/{session_id} · PATCH /agent/patch/{patch_id}
│   │       └── terminal.py           # WebSocket /terminal/ws/{session_id}?token=xxx
│   │
│   └── tests/                        # ===== pytest 测试 =====
│       ├── __init__.py
│       ├── conftest.py               # async_session fixture + httpx AsyncClient fixture
│       ├── test_security.py          # 路径校验：合法路径通过 / 绝对路径抛 ValueError / .. 抛 ValueError / 空值抛
│       ├── test_db.py                # Postgres INSERT/SELECT 往返
│       ├── test_container.py         # 真实容器 create + exec("pwd") → /workspace + stop + remove
│       ├── test_provider.py          # mock httpx → ChatCompletionChunk 解析正确
│       ├── test_tools.py             # mock container.exec → 4 工具路径校验 + 返回结构
│       ├── test_auth.py              # JWT create/verify/过期/tamper
│       ├── test_loop.py              # mock LLM → loop 正确驱动 tool.call → tool.result → done
│       └── test_api.py               # httpx AsyncClient → 完整 session→run→SSE→files 往返
│
├── frontend/                         # ===== React + Vite 前端 =====
│   ├── package.json                  # react, vite, monaco-editor, xterm, tailwind, zustand, shadcn/ui
│   ├── vite.config.ts                # @vitejs/plugin-react + server.proxy(/api → :8000)
│   ├── tsconfig.json
│   ├── tailwind.config.ts            # brand:#0FDC78 surface:#171717 muted:#A1A1AA tokens
│   ├── postcss.config.js
│   ├── index.html
│   ├── .env.local                    # VITE_API_BASE_URL=http://localhost:8000
│   │
│   └── src/
│       ├── main.tsx                  # ReactDOM.createRoot + StrictMode
│       ├── App.tsx                   # 三栏布局 Grid + 底部终端 Flex
│       ├── index.css                 # Tailwind directives + xterm import + Monaco 覆盖
│       │
│       ├── components/               # ===== 页面组件 =====
│       │   ├── TopBar.tsx            # DevFlow logo(绿色 "Dev") + Demo repo 下拉 + SandboxStatus + 新会话
│       │   ├── SandboxStatus.tsx     # green/yellow/red 小圆点 + 重试按钮
│       │   ├── FileTree.tsx          # 递归文件树 + loading spinner + hover/选中态
│       │   ├── MonacoEditor.tsx      # Monaco Dark+ + JetBrains Mono + autosave debounce 300ms
│       │   ├── ChatPanel.tsx         # 用户输入 + 消息列表 + SSE 事件路由
│       │   ├── ToolCard.tsx          # 【签名元素】品牌绿 border + ✓/✗ 徽章 + 可展开入参 + 时长 + 结果摘要
│       │   ├── PatchDiff.tsx         # 左右对比视图 + [品牌绿]接受按钮 + 拒绝按钮
│       │   ├── SseMessage.tsx        # 流式渲染 + 光标闪烁 CSS animation
│       │   ├── ErrorMessage.tsx      # SSE error 事件渲染（红色）
│       │   └── Terminal.tsx          # xterm.js + WebSocket 桥 + 连接状态 indicator
│       │
│       ├── stores/                   # ===== Zustand stores =====
│       │   ├── useSessionStore.ts    # id + token(sessionStorage) + demoRepo + containerStatus + createSession
│       │   ├── useWorkspaceStore.ts  # files: FileNode[] + loadFiles + selectFile
│       │   ├── useEditorStore.ts     # openFile + content + dirty + save(debounce 300ms)
│       │   ├── useChatStore.ts       # events: ChatEvent[] + sendMessage + pushEvent + initFromBackend
│       │   └── useTerminalStore.ts   # lines[] + append + clear + send
│       │
│       ├── hooks/                    # ===== React hooks =====
│       │   ├── useSSE.ts             # fetch + ReadableStream + 断线 2s 重连 + Last-Event-ID
│       │   └── useTerminalWS.ts      # WebSocket + 1s 重连 + 指数退避
│       │
│       └── lib/                      # ===== 工具层 =====
│           ├── api.ts                # apiFetch<T>() 统一 fetch wrapper + Authorization header
│           ├── sse.ts                # SSE parser（解析 event: xxx\ndata: {...}\n\n 格式）
│           ├── types.ts              # ChatEvent / ToolCall / PatchProposed / FileNode 类型定义
│           └── theme.ts              # 品牌色 tokens + 深色主题变量 + 8px 默认半径
│
├── docs/
│   ├── specs/
│   │   └── 2026-11-05-devflow-design.md    # 本文档（完整设计规格）
│   └── plans/
│       └── 2026-11-05-devflow/             # 5 阶段实现计划
│           ├── devflow.md
│           ├── phase-1-infra-backend-skeleton.md
│           ├── phase-2-agent-kernel.md
│           ├── phase-3-api-routes-and-streams.md
│           ├── phase-4-frontend-ui.md
│           └── phase-5-diff-recovery-e2e.md
│
└── README.md                          # 项目总览 + 开发快速启动
```

---

### 后端模块依赖方向（单向，无循环）

```
api/ (路由层)
 ├── services/workspace.py        → container.py + db (SQLAlchemy async)
 ├── services/agent/loop.py       → agent/tools.py + agent/provider.py + agent/context.py + models/*
 ├── services/agent/tools.py      → container.py + core/security.py
 ├── services/agent/provider.py   → httpx (外部)
 ├── services/container.py        → docker SDK (外部)
 ├── services/auth.py             → db + core/config
 └── models/ + schemas/ + core/   → 零相互依赖（models 依赖 db.py, schemas 依赖 Pydantic, core/ 零依赖）
```

**严禁的循环依赖**：container.py 不得 import workspace.py；loop.py 不得 import container.py（通过 tools.py 间接调）；api 层不得被 services 层 import。

---

### API 完整清单

| 方法 | 路径 | 类型 | 鉴权 | 返回 | 说明 |
|---|---|---|---|---|---|
| POST | `/sessions` | HTTP | 无 | `{id, token, demo_repo, container_ready}` | 创建会话，签发 JWT |
| GET | `/sessions/{id}` | HTTP | JWT | 会话详情 + 历史 messages[] | 刷新恢复时调用 |
| DELETE | `/sessions/{id}` | HTTP | JWT | `{ok: true}` | 触发容器 stop+remove，Postgres 保留记录 |
| POST | `/sessions/{id}/run` | HTTP | JWT | `{run_id}` | 触发 agent run（容器懒初始化），后台 asyncio.create_task 跑 loop |
| GET | `/agent/stream/{session_id}` | **SSE** | JWT | 事件流（见下表） | agent loop 实时输出，format=`event: <type>\ndata: <json>\n\n` |
| PATCH | `/agent/patch/{patch_id}` | HTTP | JWT | `{ok: true}` | 前端接受/拒绝补丁时调用，唤醒 loop 继续 |
| GET | `/files/{session_id}/{path:path}` | HTTP | JWT | 目录文件树 or 文件内容 | list 或 read（容器未就绪时懒初始化） |
| POST | `/files/{session_id}/{path:path}` | HTTP | JWT | `{ok: true}` | 写入文件（Monaco autosave 触发） |
| WebSocket | `/terminal/ws/{session_id}?token=xxx` | **WS** | query JWT | 双向 stdin/stdout | Docker exec + hijack socket |
| GET | `/health` | HTTP | 无 | `{ok, postgres, docker}` | 后端启动时检查用 |

**SSE 事件类型**（`core/events.py` 枚举）：

| event.type | data 字段 | 触发者 | 前端渲染 |
|---|---|---|---|
| `tool.call` | `{tool, input}` | agent loop | ToolCard（calling 状态 + spinner） |
| `tool.result` | `{ok, summary, details?, duration_ms}` | agent loop | ToolCard（result 状态 + ✓/✗ + 展开入参） |
| `message.delta` | `{content}` | agent loop | SseMessage（逐字流出 + 光标闪烁） |
| `patch.proposed` | `{files: [{path, before, after, diff}], patch_id}` | agent loop write_patch 后 | PatchDiff（左右对比 + 接受/拒绝） |
| `run.output` | `{command, stdout, exitCode}` | tools.exec 直接结果 | ToolCard 子区域 |
| `error` | `{code, message}` | agent loop 或 services | ErrorMessage（红色） |
| `done` | `{totalTokens, totalSteps, warning?}` | agent loop 终止 | 结束标记，停止 SSE 流 |

**所有 SSE 事件同时写入 Postgres**：tool.result → tool_calls 表；message.delta 累计 → messages 表；done → usage 表。

---

### Postgres 表结构

```sql
-- sessions
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    demo_repo TEXT NOT NULL,
    container_id TEXT,
    container_ready BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_active_at TIMESTAMPTZ DEFAULT now()
);

-- messages
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_messages_session ON messages(session_id, created_at);

-- tool_calls
CREATE TABLE tool_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    input_json JSONB,
    result_json JSONB,
    ok BOOLEAN,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_tool_calls_session ON tool_calls(session_id, created_at);

-- usage
CREATE TABLE usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    total_tokens INTEGER,
    total_steps INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

### Docker 沙箱安全参数清单（所有容器创建必须严格带）

| 参数 | 值 | 安全目的 |
|---|---|---|
| `mem_limit` | `1g` | 防 OOM 把宿主机打挂 |
| `nano_cpus` | `1e9` (= 1 core) | CPU 时间限制 |
| `pids_limit` | `256` | 防 fork bomb |
| `cap_drop` | `["ALL"]` | 丢弃所有 Linux capabilities，容器内用户无法提权 |
| `security_opt` | `["no-new-privileges"]` | 阻止 setuid/setgid 提权 |
| `user` | `node` | 非 root 运行 |
| `volumes` | 仅一个 `/workspace` bind mount | 不挂宿主机任意路径 |
| **绝对禁止** | `-v /var/run/docker.sock` | 防止容器内控制 Docker daemon（最高危） |

容器 idle 30min 未活跃 → FastAPI lifespan 内 `asyncio.create_task` 定时扫描 → 自动 `stop + remove` 容器，Postgres session 记录保留（软清除）。

---

### 前端 Zustand store 状态流

```
用户操作 → store action → apiFetch/SSE hook → 后端 API/Stream → 更新 store state → React re-render
                                                              ↑
                                                            SSE 事件 pushEvent()
```

| Store | 关键 action | 触发时机 |
|---|---|---|
| useSessionStore | createSession() | 顶栏点击"新会话" |
| | initFromSession() | App 启动（sessionStorage 有 token + sessionId 时） |
| | setContainerStatus() | SSE/WS 连接状态变化 |
| useWorkspaceStore | loadFiles() | 文件树组件 mount |
| | selectFile() | 点击文件树项 → MonacoEditor 显示内容 |
| useEditorStore | save(content) | Monaco onChange debounce 300ms → POST /files |
| useChatStore | sendMessage() | 用户发消息 → POST run + 连 SSE |
| | pushEvent(event) | useSSE hook 每收到一条 SSE event |
| | initFromBackend() | 刷新恢复时 → GET /sessions/{id} → 重建历史 |
| useTerminalStore | send(data) | xterm.onData → WebSocket.send |
| | append(data) | WebSocket.onmessage → term.write |

---

### 数字化验收指标（简历可写）

| 指标 | 目标值 | 测量方法 |
|---|---|---|
| 沙箱容器冷启动 p50 | ≤ 5s | 本地 Docker Desktop，创建到 exec("pwd") 返回 |
| SSE 首包延迟 | ≤ 500ms | POST run 到收到第一条 SSE 事件 |
| Agent 单回答最大步数 | ≤ 25 步（硬限制） | done 事件 totalSteps |
| WS 连接到就绪 | ≤ 2s | WebSocket connect 到 exec_shell hijack 就绪 |
| 文件保存 | ≤ 1s | Monaco autosave 到 container.write_file 返回 |
| 刷新恢复 | ≤ 1s | F5 到完整会话重新渲染 |
| 安全边界覆盖 | 100% 路径校验 | 所有 api/files 路由都过 validate_workspace_path |
| 单元测试覆盖率 | ≥ 50% 关键模块 | coverage.py 测 core/security, services/agent/* |
