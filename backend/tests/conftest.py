"""pytest 全局配置：把数据库与 workspace 根目录隔离到临时目录中。

这里必须在导入任何 app 模块之前设置环境变量，因为 app.db 的 engine 在模块导入时
就根据 settings 创建，app.core.config 使用 lru_cache 缓存 Settings。
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="devflow-tests-")).resolve()

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (_TEST_ROOT / "test.db").as_posix()
os.environ["WORKSPACE_ROOT_DIR"] = str(_TEST_ROOT / "workspaces")
os.environ["DEEPSEEK_API_KEY"] = "test-key"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def settings():
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def workspace_root(settings) -> Path:
    return settings.workspace_root_path


@pytest_asyncio.fixture
async def db_setup():
    """建表并在测试结束后清空表结构，保证每个测试的数据库状态隔离。"""
    from app.db import Base, engine, init_db

    await init_db()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(db_setup):
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
def sandbox(workspace_root):
    from app.services.sandbox import SandboxService

    return SandboxService(workspace_root)
