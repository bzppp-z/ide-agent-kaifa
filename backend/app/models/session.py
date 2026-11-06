from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    demo_repo: Mapped[str]
    # 本地模式下存会话 workspace 的绝对路径；Docker 模式恢复后存 container id
    container_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 用户自己电脑上的真实目录（规范化后的绝对路径）；demo 会话为 NULL
    local_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 三档权限模式：readonly | approval | auto
    permission_mode: Mapped[str] = mapped_column(String(16), default="approval")
    # 最新任务计划快照（PlanState 的 JSON）
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 最近一次 run 对应的检查点 id（用于检查点入口展示）
    last_checkpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 历史对话的结构化摘要（超限压缩后写入）
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
