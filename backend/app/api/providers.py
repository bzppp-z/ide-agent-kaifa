"""用户自主添加模型（任意 OpenAI 兼容接口）路由。

本地单用户工具，不要求 JWT。接口：

- ``GET /providers``：列表（key 只返回掩码）
- ``POST /providers``：创建；首个 provider 自动启用
- ``PATCH /providers/{id}``：局部更新（api_key 传空字符串视为不修改）
- ``DELETE /providers/{id}``：删除；删掉 active 时把最早创建的剩余项设为 active
- ``POST /providers/{id}/activate``：单事务切换唯一 active
- ``POST /providers/{id}/test``：发一条最短请求验证连通性（20s 超时）
"""

import asyncio
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session, utcnow
from app.models.provider import Provider
from app.schemas.provider import ProviderCreate, ProviderResponse, ProviderUpdate
from app.services.agent.provider import DeepSeekProvider

router = APIRouter(prefix="/providers", tags=["providers"])

TEST_TIMEOUT_SECONDS = 20.0
_ERROR_MESSAGE_MAX_CHARS = 300

_BEARER_PATTERN = re.compile(r"Bearer\s+\S+")


def mask_api_key(api_key: str) -> str:
    """空 key → ""；长度 ≤8 → 全部 ``*`` 保留长度；否则前 4 + "..." + 后 4。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


def _require_text(value: str, field: str) -> str:
    stripped = (value or "").strip()
    if not stripped:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field} must not be empty"
        )
    return stripped


def _normalize_base_url(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="base_url must be a valid http(s) URL",
        )
    return value


def _to_response(row: Provider) -> ProviderResponse:
    return ProviderResponse(
        id=row.id,
        name=row.name,
        base_url=row.base_url,
        model=row.model,
        api_key_masked=mask_api_key(row.api_key),
        is_active=row.is_active,
        created_at=row.created_at,
    )


async def _load_or_404(db: AsyncSession, provider_id: str) -> Provider:
    row = await db.get(Provider, provider_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="provider not found"
        )
    return row


async def _first_provider(db: AsyncSession) -> Provider | None:
    return (
        (
            await db.execute(
                select(Provider)
                .order_by(Provider.created_at.asc(), Provider.id.asc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


@router.get("", response_model=list[ProviderResponse])
async def list_providers(db: AsyncSession = Depends(get_db_session)) -> list[ProviderResponse]:
    rows = (
        (
            await db.execute(
                select(Provider).order_by(Provider.created_at.asc(), Provider.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_response(row) for row in rows]


@router.post("", response_model=ProviderResponse)
async def create_provider(
    body: ProviderCreate, db: AsyncSession = Depends(get_db_session)
) -> ProviderResponse:
    name = _require_text(body.name, "name")
    base_url = _normalize_base_url(body.base_url)
    model = _require_text(body.model, "model")

    has_any = await _first_provider(db) is not None

    row = Provider(
        id=uuid4().hex,
        name=name,
        base_url=base_url,
        model=model,
        api_key=body.api_key or "",
        is_active=not has_any,
        created_at=utcnow(),
    )
    db.add(row)
    await db.commit()
    return _to_response(row)


@router.patch("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    db: AsyncSession = Depends(get_db_session),
) -> ProviderResponse:
    row = await _load_or_404(db, provider_id)

    if body.name is not None:
        row.name = _require_text(body.name, "name")
    if body.base_url is not None:
        row.base_url = _normalize_base_url(body.base_url)
    if body.model is not None:
        row.model = _require_text(body.model, "model")
    if body.api_key:
        # 空字符串 = 不修改
        row.api_key = body.api_key

    await db.commit()
    return _to_response(row)


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: str, db: AsyncSession = Depends(get_db_session)
) -> dict:
    row = await _load_or_404(db, provider_id)
    was_active = row.is_active

    await db.delete(row)
    await db.flush()

    if was_active:
        first = await _first_provider(db)
        if first is not None:
            first.is_active = True

    await db.commit()
    return {"ok": True, "id": provider_id}


@router.post("/{provider_id}/activate", response_model=ProviderResponse)
async def activate_provider(
    provider_id: str, db: AsyncSession = Depends(get_db_session)
) -> ProviderResponse:
    row = await _load_or_404(db, provider_id)

    # 同一事务内先全部置 False 再置 True，保证并发切换后只有一条 active。
    await db.execute(update(Provider).values(is_active=False))
    await db.execute(
        update(Provider).where(Provider.id == provider_id).values(is_active=True)
    )
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.post("/{provider_id}/test")
async def test_provider(
    provider_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await _load_or_404(db, provider_id)
    transport = getattr(request.app.state, "provider_test_transport", None)

    started = time.perf_counter()
    try:
        provider = DeepSeekProvider(
            api_key=row.api_key,
            base_url=row.base_url,
            model=row.model,
            transport=transport,
            max_retries=0,
            timeout=TEST_TIMEOUT_SECONDS,
        )
        async with asyncio.timeout(TEST_TIMEOUT_SECONDS):
            stream = provider.chat_completion_stream(
                [{"role": "user", "content": "ping"}]
            )
            try:
                await anext(stream)
            finally:
                await stream.aclose()
    except TimeoutError:
        return {
            "ok": False,
            "message": f"timeout after {int(TEST_TIMEOUT_SECONDS)}s",
            "latency_ms": _latency_ms(started),
        }
    except Exception as exc:  # 连通性失败统一返回 ok=False，不抛 5xx
        return {
            "ok": False,
            "message": _safe_error_message(exc, row.api_key),
            "latency_ms": _latency_ms(started),
        }

    return {
        "ok": True,
        "message": "provider reachable",
        "latency_ms": _latency_ms(started),
    }


def _latency_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _safe_error_message(exc: Exception, api_key: str) -> str:
    """异常摘要：去掉 api_key / Bearer 凭证并截断，避免泄露或刷屏。"""
    message = f"{type(exc).__name__}: {exc}"
    if api_key:
        message = message.replace(api_key, "***")
    message = _BEARER_PATTERN.sub("Bearer ***", message)
    return message[:_ERROR_MESSAGE_MAX_CHARS]
