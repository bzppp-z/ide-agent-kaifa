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


@dataclass
class PendingChangeSet:
    change_set_id: str
    session_id: str
    files: list[dict]
    decisions: dict[str, bool] = field(default_factory=dict)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: dict | None = None
    result_event: asyncio.Event = field(default_factory=asyncio.Event)


class PendingChangeSetRegistry:
    """多文件变更批次注册表：一次提案、逐文件决议、整批原子生效。

    生命周期：AgentLoop 预览后 register → yield change_set.proposed → 等待
    ``PATCH /agent/change-set/{id}`` 的 decisions → 落盘 → complete(result)。
    API 侧 resolve 后等待 result，以便前端立即拿到每个文件的最终状态。
    """

    def __init__(self) -> None:
        self._change_sets: dict[str, PendingChangeSet] = {}

    def register(
        self, change_set_id: str, *, session_id: str, files: list[dict]
    ) -> PendingChangeSet:
        pending = PendingChangeSet(
            change_set_id=change_set_id,
            session_id=session_id,
            files=files,
        )
        self._change_sets[change_set_id] = pending
        return pending

    def get(self, change_set_id: str) -> PendingChangeSet | None:
        return self._change_sets.get(change_set_id)

    def resolve(
        self, change_set_id: str, decisions: dict[str, bool]
    ) -> PendingChangeSet | None:
        pending = self._change_sets.get(change_set_id)
        if pending is None:
            return None
        known = {str(item.get("path")) for item in pending.files}
        pending.decisions = {
            path: bool(value)
            for path, value in decisions.items()
            if path in known
        }
        pending.event.set()
        return pending

    async def wait(self, change_set_id: str, timeout: float = 120.0) -> dict[str, bool]:
        """等待逐文件决议；未知条目或超时都视为全部拒绝。"""
        pending = self._change_sets.get(change_set_id)
        if pending is None:
            return {}
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return {
            str(item.get("path")): bool(pending.decisions.get(str(item.get("path")), False))
            for item in pending.files
        }

    async def wait_result(
        self,
        change_set_id: str,
        timeout: float = 5.0,
        pending: PendingChangeSet | None = None,
    ) -> dict | None:
        """等待 loop 完成落盘后的逐文件结果（API 用于返回 applied/rejected/failed）。

        ``pending`` 允许调用方在 resolve 前捕获对象，避免 loop 提前 discard 造成竞态。
        """
        entry = pending if pending is not None else self._change_sets.get(change_set_id)
        if entry is None:
            return None
        try:
            await asyncio.wait_for(entry.result_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return entry.result

    def complete(self, change_set_id: str, result: dict) -> None:
        pending = self._change_sets.get(change_set_id)
        if pending is None:
            return
        pending.result = result
        pending.result_event.set()

    def discard(self, change_set_id: str) -> None:
        self._change_sets.pop(change_set_id, None)

    def discard_all(self, session_id: str) -> None:
        """停止 run 时让该会话所有待审批批次以“全部拒绝”返回。"""
        for pending in self._change_sets.values():
            if pending.session_id == session_id:
                pending.decisions = {}
                pending.event.set()
                if not pending.result_event.is_set():
                    pending.result = {"applied": [], "rejected": [], "failed": []}
                    pending.result_event.set()
