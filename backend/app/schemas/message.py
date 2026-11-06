"""消息相关响应 schema。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime
    reverted: bool = False
