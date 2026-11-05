"""运行时 Provider 解析。

agent 每次 run 开始时调用 ``resolve_provider``：优先使用用户启用的
DB Provider（任意 OpenAI 兼容接口），没有时回退到 ``settings.llm_provider``，
与 ``app.main.build_provider`` 的既有行为一致。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.provider import Provider
from app.services.agent.mock_provider import MockProvider
from app.services.agent.provider import DeepSeekProvider, LLMProvider

logger = logging.getLogger(__name__)


async def resolve_provider(
    db: AsyncSession, fallback: LLMProvider | None = None
) -> LLMProvider:
    row = (
        (
            await db.execute(
                select(Provider)
                .where(Provider.is_active.is_(True))
                .order_by(Provider.created_at.asc(), Provider.id.asc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if row is not None:
        return DeepSeekProvider(
            api_key=row.api_key, base_url=row.base_url, model=row.model
        )

    if fallback is not None:
        return fallback
    return _settings_provider()


def _settings_provider() -> LLMProvider:
    name = settings.llm_provider.strip().lower()
    if name == "deepseek":
        try:
            settings.require_deepseek_api_key()
        except RuntimeError as exc:
            logger.warning(
                "DeepSeek provider unavailable (%s); falling back to MockProvider", exc
            )
            return MockProvider()
        return DeepSeekProvider()
    return MockProvider()
