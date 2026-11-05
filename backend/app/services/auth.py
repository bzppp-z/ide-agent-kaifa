"""会话 JWT 签发/校验 + 每次 run 的用量记录。

JWT 使用 python-jose HS256。secret 由调用方传入（通常来自
``app.core.config.settings.jwt_secret``），不写日志、不进数据库。
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage import Usage

ALGORITHM = "HS256"


def create_session_token(session_id: str | UUID, secret: str, expire_hours: int = 24) -> str:
    """签发会话 token，payload 内含 session_id 与过期时间。"""
    issued_at = datetime.now(timezone.utc)
    payload = {
        "session_id": str(session_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(hours=expire_hours),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_session_token(token: str, secret: str) -> str | None:
    """校验 token，成功返回 session_id；过期、篡改或格式错误返回 None。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        return str(UUID(str(payload["session_id"])))
    except (JWTError, KeyError, TypeError, ValueError):
        return None


async def record_usage(
    db_session: AsyncSession,
    session_id: str | UUID,
    run_id: str,
    total_tokens: int,
    total_steps: int,
) -> Usage:
    """把一次 run 的 token / step 用量写入 usage 表。"""
    usage = Usage(
        id=str(uuid4()),
        session_id=str(session_id),
        run_id=run_id,
        total_tokens=total_tokens,
        total_steps=total_steps,
    )
    db_session.add(usage)
    await db_session.commit()
    return usage
