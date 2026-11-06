"""FastAPI 入口。

lifespan 初始化数据库（建表）、SandboxService、WorkspaceService，
并启动后台 idle 回收任务；shutdown 时取消任务并释放数据库连接。
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api.agent import router as agent_router
from app.api.files import router as files_router
from app.api.health import router as health_router
from app.api.local import router as local_router
from app.api.providers import router as providers_router
from app.api.sessions import router as sessions_router
from app.api.terminal import router as terminal_router
from app.core.config import Settings, get_settings
from app.db import SessionLocal, engine, init_db
from app.services.agent.mock_provider import MockProvider
from app.services.agent.pending import PendingPatchRegistry
from app.services.agent.provider import DeepSeekProvider
from app.services.repo_index import RepoIndex
from app.services.sandbox import SandboxService
from app.services.workspace import WorkspaceService

logger = logging.getLogger(__name__)
settings = get_settings()


def build_provider(settings: Settings):
    """按配置选择 LLM provider；没有可用 API key 时降级到 MockProvider。"""
    name = settings.llm_provider.strip().lower()
    if name == "deepseek":
        try:
            settings.require_deepseek_api_key()
        except RuntimeError as exc:
            logger.warning("DeepSeek provider unavailable (%s); falling back to MockProvider", exc)
            return MockProvider()
        return DeepSeekProvider()
    return MockProvider()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    sandbox = SandboxService(settings.workspace_root_path)
    repo_indexes: dict[str, RepoIndex] = {}
    workspace = WorkspaceService(
        sandbox,
        session_factory=SessionLocal,
        idle_timeout_min=settings.container_idle_timeout_min,
        repo_indexes=repo_indexes,
        repo_index_max_files=settings.repo_index_max_files,
        repo_index_max_results=settings.repo_index_max_results,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = SessionLocal
    app.state.sandbox = sandbox
    app.state.workspace = workspace
    app.state.repo_indexes = repo_indexes
    app.state.provider = build_provider(settings)
    app.state.loop_states = {}
    app.state.pending_patches = PendingPatchRegistry()

    idle_task = asyncio.create_task(workspace.run_idle_reclaim_loop(), name="devflow-idle-reclaim")
    app.state.idle_reclaim_task = idle_task

    try:
        yield
    finally:
        idle_task.cancel()
        with suppress(asyncio.CancelledError):
            await idle_task

        # 取消仍在跑的 agent loop task，避免 shutdown 后残留协程
        for state in list(app.state.loop_states.values()):
            task = state.get("task")
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.gather(task, return_exceptions=True)
        app.state.loop_states.clear()

        await engine.dispose()


app = FastAPI(title="DevFlow API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(local_router)
app.include_router(providers_router)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(agent_router)
app.include_router(terminal_router)


# ------------------------------------------------------------- 全局异常映射
# 路由层只抛语义化异常，由这里统一映射为 HTTP 状态码：
# 路径/参数非法 → 403，资源不存在 → 404，容器/沙箱不可用 → 503。


@app.exception_handler(ValueError)
async def _handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc) or "invalid request"})


@app.exception_handler(FileNotFoundError)
async def _handle_file_not_found(request: Request, exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc) or "not found"})


@app.exception_handler(RuntimeError)
async def _handle_runtime_error(request: Request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc) or "service unavailable"})
