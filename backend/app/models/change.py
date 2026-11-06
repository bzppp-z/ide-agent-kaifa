from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class ChangeRecord(Base):
    """agent 对工作区文件的改动记录（变更清单数据源）。"""

    __tablename__ = "changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    operation: Mapped[str] = mapped_column(String(16))  # create | update | delete
    added: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    # applied | rejected | reverted | failed
    status: Mapped[str] = mapped_column(String(16), default="applied")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
