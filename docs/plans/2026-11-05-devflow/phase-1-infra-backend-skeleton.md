# Phase 1: Infrastructure + Backend Skeleton（本地模式）

**Goal:** 本地模式下后端骨架跑起来。SQLite 能连、4 张表建好、沙箱工作目录能建、`exec` 能执行命令、FastAPI `/health` 返回 3 个 ok、路径校验单测全部通过。
**Prerequisites:** Python 3.12（`C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe`）、Node v24.15.0。**不需要 Docker / Postgres。**
**Deliverables:** 能跑起来的 FastAPI app、SQLite 连接成功且 4 张表存在、沙箱能创建 + `exec("pwd")` 输出指向会话 workspace 目录。

**Interface Decisions Implemented:** 无界面改动。

> **本地模式说明：** Docker Desktop 当前无法启动（backend 进程反复退出）。本阶段用 `SandboxService`（本地子进程）替代 Docker `ContainerService`，用 SQLite（aiosqlite）替代 Postgres。`SandboxService` 必须保持与 Docker 版**完全一致的公开方法签名**，Phase 5 之后再做 Docker 封装时可直接替换实现。

---

## Task 1.1: 后端目录结构 + pyproject.toml + 环境配置

**Goal:** 创建 backend 目录结构，声明所有依赖，写 `.env.example`、`.gitignore`。

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/README.md`
- Create: `backend/.env.example`
- Create: `backend/.gitignore`
- Create: `backend/app/__init__.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/agent/__init__.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`

**Steps:**
- [x] 写 `pyproject.toml`（用 hatchling backend）：
  ```toml
  [project]
  name = "devflow-backend"
  version = "0.1.0"
  requires-python = ">=3.12"
  dependencies = [
      "fastapi>=0.115",
      "uvicorn[standard]>=0.30",
      "aiosqlite>=0.20",
      "sqlalchemy[asyncio]>=2.0",
      "pydantic>=2.8",
      "pydantic-settings>=2.4",
      "httpx>=0.27",
      "python-jose[cryptography]>=3.3",
      "pytest>=8.0",
      "pytest-asyncio>=0.24",
  ]

  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [tool.hatch.build.targets.wheel]
  packages = ["app"]

  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  testpaths = ["tests"]
  ```
- [x] 写 `.env.example`：
  ```
  # 数据库（本地模式：SQLite；生产切 Postgres 时改成 postgresql+asyncpg://...）
  DATABASE_URL=sqlite+aiosqlite:///./devflow.db
  # 沙箱工作区根目录（本地模式）
  WORKSPACE_ROOT_DIR=./workspaces
  # LLM
  DEEPSEEK_API_KEY=your-key-here
  DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
  DEEPSEEK_MODEL=deepseek-chat
  # 会话/资源限制（Docker 模式恢复后使用）
  CONTAINER_IMAGE=devflow/sandbox:latest
  CONTAINER_LIMITS_CPUS=1.0
  CONTAINER_LIMITS_MEM=1g
  CONTAINER_LIMITS_PIDS=256
  CONTAINER_IDLE_TIMEOUT_MIN=30
  MAX_STEPS_PER_RUN=25
  MAX_TOKENS_PER_RUN=200000
  # 认证
  JWT_SECRET=change-me-in-production
  JWT_EXPIRE_HOURS=24
  BACKEND_PORT=8000
  CORS_ORIGINS=http://localhost:5173
  ```
- [x] 写 `.gitignore`：`devflow.db`, `workspaces/`, `.env`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/`
- [x] 写 `README.md`：如何创建 venv 外的依赖安装、启动 uvicorn、跑 pytest
- [x] 创建所有 `__init__.py` 和 `tests/conftest.py`（提供临时 SQLite fixture 与 WORKSPACE_ROOT_DIR 覆盖）
- [x] 安装依赖：
  ```
  C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m pip install -e backend
  ```
  Expected: 安装无报错

---

## Task 1.2: core/config.py + core/security.py（含单测）+ core/events.py

**Goal:** 配置加载 + 路径校验 + SSE 事件定义，三个纯模块，零外部服务依赖。

**Files:**
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/security.py`
- Create: `backend/app/core/events.py`
- Create: `backend/tests/test_security.py`

**Steps:**
- [x] 先写 `tests/test_security.py`（RED）覆盖：
  ```
  ""            → ValueError
  "/etc/passwd" → ValueError（绝对路径）
  "../../etc"   → ValueError
  "../workspace"→ ValueError
  "src/.."      → ValueError（规范化成 "."）
  "src"                → OK
  "src/router.ts"      → OK
  "todo-app/src/check.js" → OK
  ```
- [x] 写 `security.py`（纯函数，无第三方依赖）：`validate_workspace_path(relative_path) -> str`，拒绝空值、绝对路径、`..`、规范化后跳出的路径。
- [x] 写 `config.py`（pydantic-settings）：所有 env 变量带默认值；缺省 `DEEPSEEK_API_KEY` 允许启动，但调用 agent 时明确报错。
- [x] 写 `events.py`：SSE 事件类型用 Pydantic 定义（`message.delta`, `tool.call`, `tool.result`, `patch.proposed`, `run.output`, `error`, `done`），统一 `to_sse_str()` 输出 `event: <type>\ndata: <json>\n\n`。
- [x] 运行单测：
  ```
  C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m pytest backend/tests/test_security.py -v
  ```
  Expected: 全部 pass（GREEN）

**Security/Boundary Notes:** `validate_workspace_path` 是后端唯一的路径安全防线。**严禁**任何绕过此函数的文件读写操作。

---

## Task 1.3: db.py + 4 个 ORM models + 建表

**Goal:** SQLite 能连，4 张表（sessions, messages, tool_calls, usage）建起来，能用 pytest-asyncio 跑 INSERT/SELECT。

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/session.py`
- Create: `backend/app/models/message.py`
- Create: `backend/app/models/tool_call.py`
- Create: `backend/app/models/usage.py`
- Create: `backend/tests/test_db.py`

**Steps:**
- [x] 先写 `tests/test_db.py`（RED）：建临时 SQLite → 建表 → INSERT session → SELECT 回来 → 字段一致。
- [x] 写 `db.py`：async engine + `async_sessionmaker`，`get_db_session()` 依赖注入，`init_db()` 用 `Base.metadata.create_all` 建表（本地模式不引入 Alembic）。
- [x] 写 4 个 ORM model（SQLAlchemy 2.0 declarative，类型用 `Mapped`）：
  ```python
  class Session(Base):
      __tablename__ = "sessions"
      id: Mapped[str] = mapped_column(String(36), primary_key=True)
      demo_repo: Mapped[str]
      container_id: Mapped[str | None]      # 本地模式下存 workspace 目录路径
      created_at: Mapped[datetime]
      last_active_at: Mapped[datetime]

  class Message(Base):
      __tablename__ = "messages"
      id: Mapped[str] = mapped_column(String(36), primary_key=True)
      session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
      role: Mapped[str]                     # user | assistant | system
      content: Mapped[str] = mapped_column(Text)
      created_at: Mapped[datetime]

  class ToolCall(Base):
      __tablename__ = "tool_calls"
      id: Mapped[str] = mapped_column(String(36), primary_key=True)
      session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
      tool_name: Mapped[str]
      input_json: Mapped[dict] = mapped_column(JSON)
      result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
      ok: Mapped[bool]
      duration_ms: Mapped[int]
      created_at: Mapped[datetime]

  class Usage(Base):
      __tablename__ = "usage"
      id: Mapped[str] = mapped_column(String(36), primary_key=True)
      session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
      run_id: Mapped[str]
      total_tokens: Mapped[int]
      total_steps: Mapped[int]
      created_at: Mapped[datetime]
  ```
- [x] 运行 `pytest backend/tests/test_db.py -v`，Expected: pass（GREEN）

> 用 `str` UUID 而非 `UUID` 类型列，保证 SQLite 与 Postgres 都能直接用，切换 DSN 零改动。

---

## Task 1.4: services/sandbox.py + services/workspace.py + 单测

**Goal:** `SandboxService` 能创建会话工作区、`exec` 能执行命令并返回 `(exit_code, output)`、能读写文件、能清理；`WorkspaceService.ensure_container(session_id)` 幂等。

**Files:**
- Create: `backend/app/services/sandbox.py`
- Create: `backend/app/services/workspace.py`
- Create: `backend/app/services/demo_repos.py`
- Create: `backend/tests/test_sandbox.py`

**Steps:**
- [x] 先写 `tests/test_sandbox.py`（RED）：
  - 创建会话 → `exec("pwd")` 输出落在该会话 workspace 目录
  - `exec("echo hello")` → exit_code 0，output 含 `hello`
  - `exec("exit 3")` → exit_code 3
  - `write_file("a.txt", "x")` → `read_file("a.txt")` 返回 `x`
  - `list_files("")` 返回 `a.txt`
  - `exec` 传入越界路径（绝对路径）→ 拒绝
  - `stop_and_remove` 后 workspace 目录被清理
- [x] 写 `sandbox.py`：`SandboxService`，公开方法与 Docker 版一一对应：
  ```python
  class SandboxService:
      def __init__(self, root_dir: Path): ...
      def create_session_container(self, container_name: str, workspace_path: str | None = None) -> str:
          """返回 workspace 绝对路径（等价 Docker 返回 container id）"""
      async def exec(self, container_id: str, command: str, timeout: float = 30) -> tuple[int, str]:
          """在 workspace 目录内执行 shell 命令，返回 (exit_code, combined_output)"""
      async def stop_and_remove(self, container_id: str) -> None: ...
      def write_file(self, container_id: str, relative_path: str, content: str) -> None: ...
      def read_file(self, container_id: str, relative_path: str) -> str: ...
      def list_files(self, container_id: str, relative_path: str = "") -> list[dict]: ...
  ```
  实现要点：`asyncio.create_subprocess_shell` + `cwd=workspace`，Windows 用 `powershell -NoProfile -Command`（或直接 `cmd /c`），超时 kill；所有相对路径过 `core.security.validate_workspace_path`。
  Docker 模式的资源限制参数（`--cpus=1 --memory=1g --pids-limit=256 --cap-drop=ALL --security-opt=no-new-privileges --user=node`）保留在 config 和模块注释里，供 Phase 5 之后封装使用。
- [x] 写 `demo_repos.py`：3 个预设 demo repo 的最小可用内容（`todo-app`、`express-demo`、`python-flask-demo`），每个含几个文件，供会话初始化时写入 workspace。
- [x] 写 `workspace.py`：`ensure_container(session_id, demo_repo)` 幂等创建 + 写入 demo repo；`run_idle_reclaim_loop()` 定时扫描 `last_active_at` 超时的会话并清理。
- [x] 运行 `pytest backend/tests/test_sandbox.py -v`，Expected: pass（GREEN）

**Security/Boundary Notes:**
- 本地模式 `exec` 直接在宿主机执行 → **必须在 workspace 目录内**，且拒绝任何越界路径。
- 命令执行超时上限 30s，防止挂死。
- 生产部署必须切回 Docker 模式。

---

## Task 1.5: app/main.py + api/health.py + lifespan + 整体验证

**Goal:** FastAPI app 启动，`/health` 返回 3 个 ok，pytest 全过。

**Files:**
- Create: `backend/app/main.py`
- Create: `backend/app/api/health.py`
- Modify: `backend/tests/conftest.py`

**Steps:**
- [x] 先写 `tests/test_health.py`（RED）：用 `httpx.AsyncClient` + `ASGITransport` 请求 `/health`，断言返回 `{"ok": true, "db": true, "sandbox": true}`。
- [x] 写 `main.py`：lifespan 初始化 db（`init_db()`）、`SandboxService`、`WorkspaceService`，启动 idle 回收任务；CORS 允许前端 5173。
- [x] 写 `api/health.py`：检查 db 可执行 `SELECT 1`、sandbox 根目录可写。
- [x] 启动 backend 验证：
  ```
  C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m uvicorn app.main:app --port 8000
  ```
  在 `backend` 目录下运行。Expected: lifespan 无报错
- [x] curl / PowerShell `Invoke-RestMethod http://localhost:8000/health` → `{"ok": true, "db": true, "sandbox": true}`
- [x] 运行全部 pytest：`pytest backend/tests/ -v`。Expected: 全部 pass

---

## Phase Completion Checklist

- [x] 后端目录结构完整，依赖安装成功
- [x] SQLite 4 张表创建成功
- [x] `core.security.validate_workspace_path` 单测全部通过（RED→GREEN 证据）
- [x] `SandboxService` 能创建 workspace，`exec("pwd")` 指向会话目录
- [x] FastAPI `/health` 返回 3 个 ok
- [x] 全部 pytest 通过
- [x] 下一 phase 可以直接导入 `SandboxService`、`WorkspaceService`、`settings` 开始写 agent kernel
