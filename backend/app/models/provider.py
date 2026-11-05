"""用户自主添加的 OpenAI 兼容模型 Provider。

``api_key`` 以明文存放在本地 SQLite 中（本地单用户工具），只允许后端读取；
任何响应、日志都不得输出明文 key（路由层统一返回掩码）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    model: Mapped[str] = mapped_column(String(128))
    api_key: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
