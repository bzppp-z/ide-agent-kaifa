"""FastAPI 通用依赖：会话 JWT 校验、数据库 session。

token 来源：
- HTTP 请求：``Authorization: Bearer {token}``
- SSE：header 或 ``?token=`` query param
- WebSocket：query param（WebSocket 协议无法带自定义 header）
"""

from fastapi import Header, HTTPException, Query, Request, status

from app.core.config import get_settings
from app.services.auth import verify_session_token

_UNAUTHORIZED = status.HTTP_401_UNAUTHORIZED


def _settings_for(request: Request):
    return getattr(request.app.state, "settings", None) or get_settings()


def extract_bearer(authorization: str | None) -> str | None:
    """从 Authorization header 提取 Bearer token；格式不合法时返回 None。"""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def authenticate_token(token: str | None, request: Request) -> str:
    """校验 token 并返回 session_id；缺失或非法直接抛 401。"""
    if not token:
        raise HTTPException(status_code=_UNAUTHORIZED, detail="missing bearer token")
    session_id = verify_session_token(token, _settings_for(request).jwt_secret)
    if session_id is None:
        raise HTTPException(status_code=_UNAUTHORIZED, detail="invalid or expired token")
    return session_id


def get_current_session(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """HTTP 路由依赖：只接受 Authorization Bearer token。"""
    return authenticate_token(extract_bearer(authorization), request)


def get_stream_session(
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """SSE 路由依赖：header 优先，其次 ?token= query param。"""
    return authenticate_token(token or extract_bearer(authorization), request)
