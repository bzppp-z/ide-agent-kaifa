"""会话相关请求/响应 schema。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.message import MessageResponse


class SessionCreate(BaseModel):
    demo_repo: str = Field(min_length=1)


class SessionResponse(BaseModel):
    id: UUID
    demo_repo: str
    container_ready: bool
    created_at: datetime


class SessionCreatedResponse(SessionResponse):
    """POST /sessions 的响应：额外返回本次会话的 JWT。"""

    token: str


class SessionDetailResponse(SessionResponse):
    """GET /sessions/{id} 的响应：附带按时间升序的历史消息。"""

    messages: list[MessageResponse] = Field(default_factory=list)
