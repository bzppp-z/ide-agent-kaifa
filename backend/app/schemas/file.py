"""文件路由请求/响应 schema。"""

from typing import Any, Literal

from pydantic import BaseModel


class FileWriteRequest(BaseModel):
    content: str


class FileContentResponse(BaseModel):
    """目录返回 entries，文件返回 content（>2000 行时截断并置 truncated=True）。"""

    type: Literal["dir", "file"]
    content: str | None = None
    entries: list[dict[str, Any]] | None = None
    truncated: bool = False
