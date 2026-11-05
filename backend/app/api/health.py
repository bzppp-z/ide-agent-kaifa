from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, bool]:
    """本地模式健康检查：数据库可查询 + 沙箱根目录可写。"""
    db_ok = await _check_db(request.app)
    sandbox_ok = _check_sandbox(request.app)
    return {"ok": db_ok and sandbox_ok, "db": db_ok, "sandbox": sandbox_ok}


async def _check_db(app) -> bool:
    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        return False
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _check_sandbox(app) -> bool:
    sandbox = getattr(app.state, "sandbox", None)
    if sandbox is None:
        return False

    probe = sandbox.root_dir / ".health-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
