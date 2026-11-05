"""Provider 相关请求/响应 schema。

请求字段保持宽松（普通 ``str``），非空与 URL 校验在路由层完成并返回 400，
与项目其它路由的错误码风格保持一致。
"""

from datetime import datetime

from pydantic import BaseModel


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str = ""


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    # 空字符串表示「不修改」
    api_key: str | None = None


class ProviderResponse(BaseModel):
    """对外响应：只暴露掩码后的 key。"""

    id: str
    name: str
    base_url: str
    model: str
    api_key_masked: str
    is_active: bool
    created_at: datetime
