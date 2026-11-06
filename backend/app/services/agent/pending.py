"""待审批 patch 注册表。

AgentLoop 在 write_patch 落盘前先 dry_run 计算 before/after，然后注册一个
``PendingPatch`` 并等待用户通过 ``PATCH /agent/patch/{patch_id}`` 决议。
注册表实例由 ``main.py`` 创建并注入 ``app.state.pending_patches``，
loop（后台 task）与 API 路由共用同一个实例。
"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class PendingPatch:
    patch_id: str
    session_id: str
    path: str
    search: str
    replace: str
    before: str = ""
    after: str = ""
    diff: str = ""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: bool | None = None


class PendingPatchRegistry:
    def __init__(self) -> None:
        self._patches: dict[str, PendingPatch] = {}

    def register(
        self,
        patch_id: str,
        *,
        session_id: str,
        path: str,
        search: str,
        replace: str,
        before: str = "",
        after: str = "",
        diff: str = "",
    ) -> PendingPatch:
        pending = PendingPatch(
            patch_id=patch_id,
            session_id=session_id,
            path=path,
            search=search,
            replace=replace,
            before=before,
            after=after,
            diff=diff,
        )
        self._patches[patch_id] = pending
        return pending

    def get(self, patch_id: str) -> PendingPatch | None:
        return self._patches.get(patch_id)

    def resolve(self, patch_id: str, accepted: bool) -> bool:
        """登记用户决议并唤醒等待方；未知 patch_id 返回 False。"""
        pending = self._patches.get(patch_id)
        if pending is None:
            return False
        pending.decision = bool(accepted)
        pending.event.set()
        return True

    async def wait(self, patch_id: str, timeout: float) -> bool:
        """等待决议，返回是否接受；超时视为拒绝，两种情况都会清理条目。"""
        pending = self._patches.get(patch_id)
        if pending is None:
            return False

        accepted = False
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout)
            accepted = bool(pending.decision)
        except asyncio.TimeoutError:
            accepted = False
        finally:
            self._patches.pop(patch_id, None)
        return accepted

    def discard(self, patch_id: str) -> None:
        self._patches.pop(patch_id, None)

    def discard_all(self, session_id: str) -> None:
        """停止 run 时让该会话所有待审批条目以“拒绝”返回。"""
        for pending in self._patches.values():
            if pending.session_id == session_id:
                pending.decision = False
                pending.event.set()
