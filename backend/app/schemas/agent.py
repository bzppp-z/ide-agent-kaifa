"""Agent run 请求/响应 schema。"""

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1)


class AgentRunResponse(BaseModel):
    run_id: str


class PatchDecisionRequest(BaseModel):
    """用户对待审批 patch 的决议。"""

    accepted: bool


class ChangeSetDecisionRequest(BaseModel):
    """用户对多文件批次的逐文件决议；未出现的路径按拒绝处理。"""

    decisions: dict[str, bool] = Field(default_factory=dict)
