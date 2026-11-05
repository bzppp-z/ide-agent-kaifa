"""Agent run + SSE 流式端点。

流程：

1. ``POST /sessions/{id}/run``：校验 JWT → 懒初始化 workspace → 写 user message
   → 创建 ``asyncio.Queue`` 和后台 AgentLoop task，立即返回 ``run_id``。
2. ``GET /agent/stream/{id}``：从 queue 读事件，按 ``core.events`` 的 SSE 格式
   yield，并同步持久化（tool_calls / messages / usage）。

``app.state.loop_states`` 保存每个 session 的 ``{task, queue, run_id}``，
流结束或客户端断开时清理。客户端断开（``request.is_disconnected()``，100ms 粒度）
会取消后台 task，避免继续占用资源。
"""

import asyncio
import contextlib
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_session, get_stream_session
from app.api.sessions import ensure_session_ownership, load_session_or_404
from app.core.events import (
    DoneEvent,
    ErrorEvent,
    MessageDeltaEvent,
    PatchProposedEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.db import get_db_session, utcnow
from app.models.message import Message as MessageModel
from app.models.tool_call import ToolCall as ToolCallModel
from app.schemas.agent import AgentRunRequest, AgentRunResponse, PatchDecisionRequest
from app.services.agent.loop import AgentLoop
from app.services.agent.provider import LLMProvider
from app.services.agent.tools import ToolRegistry
from app.services.auth import record_usage
from app.services.provider_registry import resolve_provider

router = APIRouter(tags=["agent"])

DISCONNECT_POLL_SECONDS = 0.1


@router.post("/sessions/{session_id}/run", response_model=AgentRunResponse)
async def run_agent(
    session_id: str,
    body: AgentRunRequest,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> AgentRunResponse:
    ensure_session_ownership(session_id, current_session)
    await load_session_or_404(db, session_id)

    workspace = request.app.state.workspace
    workspace_path = await workspace.ensure_container(session_id)

    history = await _load_history(db, session_id)
    db.add(
        MessageModel(
            id=str(uuid4()),
            session_id=session_id,
            role="user",
            content=body.message,
            created_at=utcnow(),
        )
    )
    await db.commit()

    run_id = str(uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    loop_states: dict[str, dict] = request.app.state.loop_states

    # 每次 run 开始时解析 provider：用户启用的 DB Provider 优先，
    # 没有时回退到 app.state.provider / settings，保持既有行为。
    provider = await resolve_provider(db, fallback=getattr(request.app.state, "provider", None))

    previous = loop_states.get(session_id)
    if previous is not None and previous.get("task") is not None:
        previous["task"].cancel()

    task = asyncio.create_task(
        _drive_agent_loop(
            request.app,
            session_id=session_id,
            run_id=run_id,
            queue=queue,
            user_message=body.message,
            workspace_path=workspace_path,
            history=history,
            provider=provider,
        ),
        name=f"devflow-agent-{session_id}",
    )
    loop_states[session_id] = {"task": task, "queue": queue, "run_id": run_id}

    return AgentRunResponse(run_id=run_id)


@router.get("/agent/stream/{session_id}")
async def stream_agent(
    session_id: str,
    request: Request,
    current_session: str = Depends(get_stream_session),
) -> StreamingResponse:
    ensure_session_ownership(session_id, current_session)

    loop_states: dict[str, dict] = request.app.state.loop_states
    state = loop_states.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no active run for this session"
        )

    return StreamingResponse(
        sse_event_generator(
            request,
            session_id,
            state,
            loop_states,
            request.app.state.session_factory,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.patch("/agent/patch/{patch_id}")
async def respond_patch(
    patch_id: str,
    body: PatchDecisionRequest,
    request: Request,
    current_session: str = Depends(get_current_session),
) -> dict:
    """登记用户对 patch 的决议；未知 patch_id 400/404，非本会话 403。"""
    registry = request.app.state.pending_patches
    pending = registry.get(patch_id)
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="patch not found or already resolved"
        )
    if pending.session_id != current_session:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="patch does not belong to this session",
        )

    registry.resolve(patch_id, body.accepted)
    return {"ok": True, "patch_id": patch_id, "accepted": body.accepted}


# ---------------------------------------------------------------- background loop


async def _drive_agent_loop(
    app,
    *,
    session_id: str,
    run_id: str,
    queue: asyncio.Queue,
    user_message: str,
    workspace_path: str,
    history: list[dict],
    provider: LLMProvider | None = None,
) -> None:
    """后台跑 AgentLoop，把每个事件放进 queue；结束时放哨兵 None。"""
    try:
        loop = AgentLoop(
            provider=provider or app.state.provider,
            tools=ToolRegistry(),
            sandbox=app.state.sandbox,
            history=history,
            pending_patches=app.state.pending_patches,
        )
        settings = app.state.settings
        async for event in loop.run_stream(
            session_id,
            user_message,
            session_workspace_path=workspace_path,
            max_steps=settings.max_steps_per_run,
            max_tokens=settings.max_tokens_per_run,
            run_id=run_id,
        ):
            queue.put_nowait(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - 防御性：任何 loop 异常都推给前端
        queue.put_nowait(ErrorEvent(message=str(exc), code="agent_error"))
    finally:
        queue.put_nowait(None)


async def _load_history(db: AsyncSession, session_id: str) -> list[dict]:
    rows = (
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
    return [
        {"role": row.role, "content": row.content}
        for row in rows
        if row.role in ("user", "assistant")
    ]


# ------------------------------------------------------------------- SSE stream


async def sse_event_generator(
    request,
    session_id: str,
    state: dict,
    loop_states: dict,
    session_factory,
):
    """读取 queue 并 yield SSE 字符串，同时把事件写入 SQLite。"""
    assistant_parts: list[str] = []
    pending_calls: dict[str, dict] = {}
    task: asyncio.Task | None = state.get("task")
    finished = False

    try:
        while True:
            if await request.is_disconnected():
                return
            try:
                item = await asyncio.wait_for(
                    state["queue"].get(), timeout=DISCONNECT_POLL_SECONDS
                )
            except asyncio.TimeoutError:
                continue

            if item is None:
                break

            event = item
            await _persist_event(
                session_factory, session_id, state["run_id"], event, assistant_parts, pending_calls
            )
            yield event.to_sse_str()

            if isinstance(event, DoneEvent):
                finished = True
                break
    finally:
        loop_states.pop(session_id, None)
        if task is not None and not finished:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(task, return_exceptions=True)


async def _persist_event(
    session_factory,
    session_id: str,
    run_id: str,
    event,
    assistant_parts: list[str],
    pending_calls: dict[str, dict],
) -> None:
    async with session_factory() as db:
        if isinstance(event, ToolCallEvent):
            pending_calls[event.call_id] = {
                "tool_name": event.tool_name,
                "arguments": event.arguments,
            }
            return

        if isinstance(event, ToolResultEvent):
            info = pending_calls.pop(event.call_id, {})
            result = event.result if isinstance(event.result, dict) else {"value": event.result}
            db.add(
                ToolCallModel(
                    id=str(uuid4()),
                    session_id=session_id,
                    tool_name=event.tool_name,
                    input_json=info.get("arguments") or {},
                    result_json=result,
                    ok=event.ok,
                    duration_ms=event.duration_ms,
                    created_at=utcnow(),
                )
            )
            await db.commit()
            return

        if isinstance(event, MessageDeltaEvent):
            assistant_parts.append(event.content)
            return

        if isinstance(event, PatchProposedEvent):
            db.add(
                MessageModel(
                    id=str(uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content=json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                    created_at=utcnow(),
                )
            )
            await db.commit()
            return

        if isinstance(event, DoneEvent):
            if assistant_parts:
                db.add(
                    MessageModel(
                        id=str(uuid4()),
                        session_id=session_id,
                        role="assistant",
                        content="".join(assistant_parts),
                        created_at=utcnow(),
                    )
                )
            await record_usage(
                db, session_id, run_id, event.total_tokens, event.total_steps
            )
            return
