"""本地目录浏览路由：供前端选择用户电脑上的真实目录作为会话 workspace。

- ``GET /local/home``：返回用户主目录。
- ``GET /local/browse?path=...``：列出指定目录下的子目录（只列目录）。
  浏览允许进入系统保护目录，严格的拒绝规则在创建会话时执行。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter(prefix="/local", tags=["local"])


def _resolve_browse_path(raw_path: str) -> Path:
    if not raw_path.strip():
        return Path.home().resolve()

    candidate = Path(raw_path.strip()).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"path must be an absolute path: {raw_path!r}",
        )

    resolved = candidate.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"directory not found: {raw_path!r}",
        )
    return resolved


@router.get("/home")
async def local_home() -> dict:
    return {"path": str(Path.home().resolve())}


@router.get("/browse")
async def browse(path: str = Query(default="")) -> dict:
    resolved = _resolve_browse_path(path)

    directories = sorted(
        (entry for entry in resolved.iterdir() if entry.is_dir()),
        key=lambda entry: entry.name.lower(),
    )
    return {
        "path": str(resolved),
        "parent": str(resolved.parent) if resolved.parent != resolved else None,
        "entries": [
            {"name": entry.name, "path": str(entry), "type": "dir"} for entry in directories
        ],
    }
