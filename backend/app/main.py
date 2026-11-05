"""FastAPI 入口。

lifespan 初始化数据库（建表）、SandboxService、WorkspaceService，
并启动后台 idle 回收任务；shutdown 时取消任务并释放数据库连接。
"""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.config import get_settings
from app.db import SessionLocal, engine, init_db
from app.services.sandbox import SandboxService
from app.services.workspace import WorkspaceService

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    sandbox = SandboxService(settings.workspace_root_path)
    workspace = WorkspaceService(
        sandbox,
        session_factory=SessionLocal,
        idle_timeout_min=settings.container_idle_timeout_min,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = SessionLocal
    app.state.sandbox = sandbox
    app.state.workspace = workspace

    idle_task = asyncio.create_task(workspace.run_idle_reclaim_loop(), name="devflow-idle-reclaim")
    app.state.idle_reclaim_task = idle_task

    try:
        yield
    finally:
        idle_task.cancel()
        with suppress(asyncio.CancelledError):
            await idle_task
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
