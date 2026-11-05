"""数据库层：async engine + sessionmaker + 建表。

本地模式使用 SQLite（aiosqlite）。SQLAlchemy 模型与 DSN 抽象保持一致，
切回 Postgres 时只需改 ``DATABASE_URL``，不需要改动模型。
"""

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def utcnow() -> datetime:
    """统一的 naive UTC 时间戳（SQLite 与 Postgres 的比较行为一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _create_engine(url: str) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": False}
    if url.startswith("sqlite"):
        # 测试会在不同事件循环中复用同一个 engine，NullPool 避免跨 loop 连接复用。
        kwargs["poolclass"] = NullPool
    return create_async_engine(url, **kwargs)


engine: AsyncEngine = _create_engine(get_settings().database_url)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """建表（本地模式不用 Alembic，直接 create_all）。

    ``create_all`` 不会给已存在的表补新列，因此对 SQLite 做一次轻量迁移：
    检测 ``sessions`` 缺失的列并 ``ALTER TABLE`` 补上。重复启动幂等。
    """
    from app import models  # noqa: F401  确保所有 ORM 模型注册到 Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "sqlite":
            await _migrate_sqlite_sessions(conn)


async def _migrate_sqlite_sessions(conn: AsyncConnection) -> None:
    """给旧的 sessions 表补 ``local_path`` 列（幂等）。"""
    rows = (await conn.exec_driver_sql("PRAGMA table_info(sessions)")).fetchall()
    columns = {row[1] for row in rows}
    if "local_path" not in columns:
        await conn.exec_driver_sql("ALTER TABLE sessions ADD COLUMN local_path VARCHAR")


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用的数据库 session。"""
    async with SessionLocal() as session:
        yield session
