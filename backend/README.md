# DevFlow Backend

浏览器内 AI coding agent 的后端服务（FastAPI + SQLAlchemy Async）。

当前为**本地模式**：SQLite 存储、`SandboxService` 用宿主机子进程执行命令（每会话一个工作目录）。
Docker Desktop 恢复后可把 `SandboxService` 换回容器实现、SQLite DSN 换成 Postgres，公开方法签名保持一致。

## 环境

- Python 3.12：`C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe`
- 无需 Docker / Postgres

## 安装

```powershell
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m pip install -e backend
```

## 配置

```powershell
Copy-Item backend\.env.example backend\.env
# 按需填写 DEEPSEEK_API_KEY
```

所有配置项见 `app/core/config.py`，字段名与 `.env.example` 一一对应。

## 启动

```powershell
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m uvicorn app.main:app --port 8000
```

在 `backend` 目录下运行。健康检查：

```powershell
Invoke-RestMethod http://localhost:8000/health
# ok db     sandbox
# -- --     -------
# True True True
```

## 安全说明

- `app/core/security.py:validate_workspace_path` 是唯一的路径安全防线，所有文件读写都必须经过它。
- 本地模式下 `exec` 直接在宿主机执行，隔离性弱于容器；生产部署必须切回 Docker 模式（资源限制参数已保留在配置与 `sandbox.py` 注释中）。
