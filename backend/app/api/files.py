"""文件树 / 文件读写路由。

所有 path 先过 ``core.security.validate_workspace_path``，再用
``WorkspaceService.ensure_container`` 懒初始化会话 workspace。
不使用任何 shell 命令（test -d / cat / heredoc），只调用 SandboxService。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_session
from app.api.sessions import ensure_session_ownership, load_session_or_404
from app.core.security import validate_workspace_path
from app.db import get_db_session
from app.schemas.file import FileContentResponse, FileWriteRequest

router = APIRouter(prefix="/files", tags=["files"])

MAX_READ_LINES = 2000


def _sandbox(request: Request):
    return request.app.state.workspace.sandbox


def _normalize_dir_path(path: str) -> str:
    """根目录（空字符串 / "."）合法；其余交给路径安全校验。"""
    stripped = path.strip()
    if stripped in ("", "."):
        return ""
    return validate_workspace_path(path)


@router.get("/{session_id}/{path:path}", response_model=FileContentResponse)
async def get_file_or_dir(
    session_id: str,
    path: str,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> FileContentResponse:
    ensure_session_ownership(session_id, current_session)
    await load_session_or_404(db, session_id)

    relative = _normalize_dir_path(path)
    container_id = await request.app.state.workspace.ensure_container(session_id)
    sandbox = _sandbox(request)

    try:
        entries = sandbox.list_files(container_id, relative)
        return FileContentResponse(type="dir", entries=entries)
    except (FileNotFoundError, ValueError):
        # 不存在或不是目录 → 继续尝试按文件读取
        pass

    try:
        content = sandbox.read_file(container_id, relative)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"path not found: {path!r}"
        ) from None

    lines = content.splitlines()
    truncated = len(lines) > MAX_READ_LINES
    if truncated:
        content = "\n".join(lines[:MAX_READ_LINES])

    return FileContentResponse(type="file", content=content, truncated=truncated)


@router.post("/{session_id}/{path:path}")
async def write_file(
    session_id: str,
    path: str,
    body: FileWriteRequest,
    request: Request,
    current_session: str = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    ensure_session_ownership(session_id, current_session)
    await load_session_or_404(db, session_id)

    relative = validate_workspace_path(path)
    container_id = await request.app.state.workspace.ensure_container(session_id)
    _sandbox(request).write_file(container_id, relative, body.content)

    # 写入成功后增量失效该路径，保证下一次检索读到新内容
    index = getattr(request.app.state, "repo_indexes", {}).get(session_id)
    if index is not None:
        index.invalidate(relative)

    return {"ok": True, "path": relative}
