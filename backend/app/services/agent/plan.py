"""Agent 任务计划（单次 run 内状态 + 会话持久化）。

计划是一份整快照：3–8 项、最多 1 项 ``in_progress``、空计划拒绝。
``apply_update`` 保留已有 id，让前端可以稳定地逐项更新。
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

PlanStatus = Literal["pending", "in_progress", "done", "failed", "skipped"]
PLAN_STATUSES: tuple[str, ...] = ("pending", "in_progress", "done", "failed", "skipped")

MIN_PLAN_ITEMS = 3
MAX_PLAN_ITEMS = 8
MAX_IN_PROGRESS = 1


class PlanItem(BaseModel):
    id: str
    title: str
    status: PlanStatus = "pending"
    note: str | None = None


class PlanState(BaseModel):
    items: list[PlanItem] = Field(default_factory=list)

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.status == "done")

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)

    def render(self) -> str:
        """给模型回看的纯文本计划，避免把完整 JSON 再塞回上下文。"""
        lines = []
        for item in self.items:
            lines.append(f"[{item.id}] {item.title} — {item.status}")
            if item.note:
                lines.append(f"    note: {item.note}")
        return "\n".join(lines)


def validate_plan(items: Any) -> PlanState:
    """校验并规范化一份完整计划，非法时抛 ValueError（带中文原因）。"""
    if not isinstance(items, list) or not items:
        raise ValueError("计划不能为空，至少需要 3 项")

    if len(items) < MIN_PLAN_ITEMS:
        raise ValueError(f"计划至少需要 {MIN_PLAN_ITEMS} 项，当前只有 {len(items)} 项")
    if len(items) > MAX_PLAN_ITEMS:
        raise ValueError(f"计划最多 {MAX_PLAN_ITEMS} 项，当前有 {len(items)} 项")

    normalized: list[PlanItem] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index + 1} 项必须是对象")

        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"第 {index + 1} 项缺少非空 title")
        title = title.strip()

        status = raw.get("status", "pending")
        if status not in PLAN_STATUSES:
            raise ValueError(
                f"第 {index + 1} 项 status 非法：{status!r}（只能是 {'/'.join(PLAN_STATUSES)}）"
            )

        raw_id = raw.get("id")
        item_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else f"p{index + 1}"
        if item_id in seen_ids:
            raise ValueError(f"计划项 id 重复：{item_id}")
        seen_ids.add(item_id)

        note = raw.get("note")
        if note is not None and not isinstance(note, str):
            raise ValueError(f"第 {index + 1} 项 note 必须是字符串")

        normalized.append(
            PlanItem(id=item_id, title=title, status=status, note=note or None)  # type: ignore[arg-type]
        )

    return validate_state(PlanState(items=normalized))


def validate_state(state: PlanState) -> PlanState:
    if not state.items:
        raise ValueError("计划不能为空，至少需要 3 项")
    if not (MIN_PLAN_ITEMS <= len(state.items) <= MAX_PLAN_ITEMS):
        raise ValueError(
            f"计划需要 {MIN_PLAN_ITEMS}–{MAX_PLAN_ITEMS} 项，当前有 {len(state.items)} 项"
        )
    in_progress = sum(1 for item in state.items if item.status == "in_progress")
    if in_progress > MAX_IN_PROGRESS:
        raise ValueError("同一时刻最多只能有 1 项处于 in_progress")
    return state


def apply_update(current: PlanState | None, incoming: Any) -> PlanState:
    """应用一次全量快照。

    入参缺少 id 时优先复用当前位置的旧 id，其次生成 ``p{n}``；
    显式传入的 id 一律保留。
    """
    if isinstance(incoming, PlanState):
        raw_items = [item.model_dump() for item in incoming.items]
    elif isinstance(incoming, list):
        raw_items = incoming
    else:
        raise ValueError("items 必须是计划项数组")

    if not raw_items:
        raise ValueError("计划不能为空，至少需要 3 项")

    merged: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            merged.append(raw)
            continue
        item = dict(raw)
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            previous = current.items[index] if current and index < len(current.items) else None
            item["id"] = previous.id if previous else f"p{index + 1}"
        merged.append(item)

    return validate_plan(merged)


def plan_from_json(raw: str | dict[str, Any] | PlanState | None) -> PlanState | None:
    """反序列化会话里保存的计划；脏数据返回 None，不阻塞本轮 run。"""
    if raw is None:
        return None
    if isinstance(raw, PlanState):
        return raw if raw.items else None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            payload: Any = json.loads(raw)
        except (TypeError, ValueError):
            return None
    else:
        payload = raw

    if not isinstance(payload, dict):
        return None
    try:
        state = PlanState.model_validate(payload)
        validate_state(state)
    except (ValueError, TypeError):
        return None
    return state
