"""会话相关请求/响应 schema。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.message import MessageResponse


class SessionCreate(BaseModel):
    # demo_repo 可选：指定 local_path 时忽略，默认 "todo-app"
    demo_repo: str = Field(default="todo-app", min_length=1)
    # 用户自己电脑上的真实目录；提供时创建本地目录会话
    local_path: str | None = None


class SessionResponse(BaseModel):
    id: UUID
    demo_repo: str
    container_ready: bool
    created_at: datetime
    permission_mode: Literal["readonly", "approval", "auto"] = "approval"
    plan: dict[str, Any] | None = None
    last_checkpoint_id: str | None = None


class CheckpointResponse(BaseModel):
    id: str
    session_id: str
    created_at: datetime
    user_message: str = ""
    file_count: int = 0


class ChangeRecordResponse(BaseModel):
    id: UUID
    path: str
    operation: Literal["create", "update", "delete"]
    added: int = 0
    deleted: int = 0
    status: Literal["applied", "rejected", "reverted", "failed"]
    created_at: datetime


class SessionPreferencesUpdate(BaseModel):
    permission_mode: Literal["readonly", "approval", "auto"]


class SessionCreatedResponse(SessionResponse):
    """POST /sessions 的响应：额外返回本次会话的 JWT。"""

    token: str


class SessionDetailResponse(SessionResponse):
    """GET /sessions/{id} 的响应：附带按时间升序的历史消息。"""

    # 当前会话绑定的用户真实目录；demo 会话为 None
    local_path: str | None = None
    # 最近一次上下文压缩生成的结构化摘要；未压缩过为 None
    summary_text: str | None = None
    messages: list[MessageResponse] = Field(default_factory=list)
