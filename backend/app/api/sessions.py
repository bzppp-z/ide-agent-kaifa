"""会话 CRUD 路由。

- POST /sessions：建会话记录并签发 JWT，容器延迟到首次文件/agent 访问时初始化。
- GET /sessions/{id}：校验 token 归属，返回会话详情 + 按时间升序的历史消息。
- DELETE /sessions/{id}：清理 workspace 并清空 container_id（会话与消息保留）。
"""

import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_session
from app.core.security import validate_local_workspace_path
from app.db import get_db_session, utcnow
from app.models.change import ChangeRecord
from app.models.message import Message as MessageModel
from app.models.session import Session as SessionModel
from app.schemas.message import MessageResponse
from app.schemas.session import (
    ChangeRecordResponse,
    CheckpointResponse,
    SessionCreate,
    SessionCreatedResponse,
    SessionDetailResponse,
    SessionPreferencesUpdate,
    SessionResponse,
)
from app.services.auth import create_session_token
from app.services.checkpoints import checkpoint_datetime
from app.services.demo_repos import get_demo_repo

router = APIRouter(prefix="/sessions", tags=["sessions"])


def ensure_session_ownership(session_id: str, current_session: str) -> None:
    """path 里的 session_id 必须与 token 里的一致。"""
    if session_id != current_session:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token does not belong to this session",
        )


def _parse_plan(row: SessionModel) -> dict | None:
    """plan_json 解析失败时返回 None，不让脏数据阻塞会话恢复。"""
    if not row.plan_json:
        return None
    try:
        parsed = json.loads(row.plan_json)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def session_to_response(row: SessionModel) -> SessionResponse:
    return SessionResponse(
        id=row.id,
        demo_repo=row.demo_repo,
        container_ready=row.container_id is not None,
        created_at=row.created_at,
        permission_mode=row.permission_mode or "approval",  # type: ignore[arg-type]
        plan=_parse_plan(row),
        last_checkpoint_id=row.last_checkpoint_id,
    )


async def load_session_or_404(db: AsyncSession, session_id: str) -> SessionModel:
    row = await db.get(SessionModel, session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    return row


@router.post("", response_model=SessionCreatedResponse)
async def create_session(
    body: SessionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> SessionCreatedResponse:
    local_path: str | None = None
    if body.local_path is not None and body.local_path.strip():
        try:
            local_path = validate_local_workspace_path(body.local_path)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        demo_repo = "local"
    else:
        get_demo_repo(body.demo_repo)  # 未知 demo repo → ValueError → 403
        demo_repo = body.demo_repo

    session_id = str(uuid4())
    now = utcnow()
    row = SessionModel(
        id=session_id,
        demo_repo=demo_repo,
        container_id=None,
        local_path=local_path,
        created_at=now,
        last_active_at=now,
    )
    db.add(row)
    await db.commit()

    settings = request.app.state.settings
    token = create_session_token(session_id, settings.jwt_secret, settings.jwt_expire_hours)
    return SessionCreatedResponse(
        id=row.id,
        demo_repo=row.demo_repo,
        container_ready=False,
        created_at=row.created_at,
        permission_mode=row.permission_mode,  # type: ignore[arg-type]
        token=token,
    )


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> SessionDetailResponse:
    ensure_session_ownership(session_id, current_session)
    row = await load_session_or_404(db, session_id)

    # 刷新恢复：workspace 可能已被 idle 回收，container_id 失效时清空并写回。
    if row.container_id and not Path(row.container_id).is_dir():
        row.container_id = None
        await db.commit()

    messages = (
        (
            await db.execute(
                select(MessageModel)
                .where(MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
            )
        )
        .scalars()
        .all()
    )

    base = session_to_response(row)
    return SessionDetailResponse(
        **base.model_dump(),
        local_path=row.local_path,
        messages=[
            MessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                reverted=bool(message.reverted),
            )
            for message in messages
        ],
    )


@router.patch("/{session_id}/preferences", response_model=SessionResponse)
async def update_session_preferences(
    session_id: str,
    body: SessionPreferencesUpdate,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    """更新会话级偏好（当前仅权限模式）。"""
    ensure_session_ownership(session_id, current_session)
    row = await load_session_or_404(db, session_id)
    row.permission_mode = body.permission_mode
    row.last_active_at = utcnow()
    await db.commit()
    await db.refresh(row)
    return session_to_response(row)


@router.get("/{session_id}/checkpoints", response_model=list[CheckpointResponse])
async def list_checkpoints(
    session_id: str,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> list[CheckpointResponse]:
    ensure_session_ownership(session_id, current_session)
    await load_session_or_404(db, session_id)
    rows = request.app.state.checkpoints.list(session_id)
    return [CheckpointResponse(**row) for row in rows]


@router.post("/{session_id}/checkpoints/{checkpoint_id}/rollback")
async def rollback_checkpoint(
    session_id: str,
    checkpoint_id: str,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """恢复检查点快照；该时间点之后的消息与变更记录标记为 reverted。"""
    ensure_session_ownership(session_id, current_session)
    await load_session_or_404(db, session_id)

    checkpoints = request.app.state.checkpoints
    checkpoint = checkpoints.get(session_id, checkpoint_id)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="checkpoint not found"
        )

    workspace_path = await request.app.state.workspace.ensure_container(session_id)
    restored = checkpoints.rollback(
        session_id,
        checkpoint_id,
        request.app.state.workspace.sandbox,
        container_id=workspace_path,
    )

    cutoff = checkpoint_datetime(checkpoint)
    await db.execute(
        update(MessageModel)
        .where(MessageModel.session_id == session_id, MessageModel.created_at > cutoff)
        .values(reverted=True)
    )
    await db.execute(
        update(ChangeRecord)
        .where(ChangeRecord.session_id == session_id, ChangeRecord.created_at > cutoff)
        .values(status="reverted")
    )
    await db.commit()

    return {
        "ok": True,
        "session_id": session_id,
        "checkpoint_id": checkpoint_id,
        "restored": restored,
    }


@router.get("/{session_id}/changes", response_model=list[ChangeRecordResponse])
async def list_changes(
    session_id: str,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> list[ChangeRecordResponse]:
    ensure_session_ownership(session_id, current_session)
    await load_session_or_404(db, session_id)
    rows = (
        (
            await db.execute(
                select(ChangeRecord)
                .where(ChangeRecord.session_id == session_id)
                .order_by(ChangeRecord.created_at.desc(), ChangeRecord.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        ChangeRecordResponse(
            id=row.id,
            path=row.path,
            operation=row.operation,  # type: ignore[arg-type]
            added=row.added,
            deleted=row.deleted,
            status=row.status,  # type: ignore[arg-type]
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    ensure_session_ownership(session_id, current_session)
    row = await load_session_or_404(db, session_id)

    if row.local_path:
        # 用户真实目录会话：只清 DB 状态，绝不删除用户目录
        if row.container_id is not None:
            row.container_id = None
            await db.commit()
    elif row.container_id:
        await request.app.state.workspace.sandbox.stop_and_remove(row.container_id)
        row.container_id = None
        await db.commit()

    index = request.app.state.repo_indexes.pop(session_id, None)
    if index is not None:
        index.cancel_build()
    request.app.state.checkpoints.delete_session(session_id)

    return {"ok": True, "session_id": session_id}
