"""三档权限模式与操作判定。

服务端是所有写操作与命令的唯一门控；前端下拉只是输入。
未知模式一律按 ``approval`` 处理，避免配置错误导致意外放行。
"""

from typing import Literal

PermissionMode = Literal["readonly", "approval", "auto"]

DEFAULT_PERMISSION_MODE: PermissionMode = "approval"
VALID_PERMISSION_MODES: tuple[str, ...] = ("readonly", "approval", "auto")


def normalize_permission_mode(mode: str | None) -> PermissionMode:
    if mode in VALID_PERMISSION_MODES:
        return mode  # type: ignore[return-value]
    return DEFAULT_PERMISSION_MODE


def can_write(mode: str | None) -> bool:
    """新建、写入、精确替换。只读模式全部拒绝。"""
    return normalize_permission_mode(mode) != "readonly"


def can_delete(mode: str | None) -> bool:
    """删除文件。只读拒绝；其余模式都必须二次确认。"""
    return normalize_permission_mode(mode) != "readonly"


def can_run_command(mode: str | None) -> bool:
    """执行命令。只读拒绝。"""
    return normalize_permission_mode(mode) != "readonly"


def can_auto_apply(mode: str | None) -> bool:
    """编辑类操作是否可自动生效（删除与命令永远不自动）。"""
    return normalize_permission_mode(mode) == "auto"


def need_command_approval(mode: str | None) -> bool:
    """命令是否需要用户审批。只读模式直接拒绝，不进入审批。"""
    return normalize_permission_mode(mode) != "readonly"
