"""Agent run 请求/响应 schema。"""

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1)


class AgentRunResponse(BaseModel):
    run_id: str
