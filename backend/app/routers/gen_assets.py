"""生成的图片/视频静态资源(需登录;img/video 可用 ?token=)。

书城可见小说的封面允许匿名读取,供阅读器 :3001 直接 <img>。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..ai.media_gen import GEN_ROOT
from ..database import get_db
from ..models import Novel, User
from ..security import user_from_token

router = APIRouter(prefix="/gen-assets", tags=["gen-assets"])
_bearer = HTTPBearer(auto_error=False)

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
}


# 进程内正向缓存:同一封面文件只查库一次,避免书城网格 N 次重复鉴权查询
_PUBLIC_COVER_OK: set[tuple[int, str]] = set()


def _is_public_novel_cover(user_id: int, filename: str, db: Session) -> bool:
    """小说封面允许匿名读取(书城/书架 <img> 直链 + 浏览器缓存)。"""
    key = (user_id, filename)
    if key in _PUBLIC_COVER_OK:
        return True
    candidates = (
        f"/gen-assets/{user_id}/{filename}",
        f"/api/gen-assets/{user_id}/{filename}",
    )
    ok = (
        db.query(Novel.id)
        .filter(Novel.cover.isnot(None), or_(*[Novel.cover == c for c in candidates]))
        .first()
        is not None
    )
    if ok:
        _PUBLIC_COVER_OK.add(key)
    return ok


def _authorize_asset(
    user_id: int,
    filename: str,
    token: str | None,
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
) -> None:
    user = None
    if credentials is not None:
        user = user_from_token(credentials.credentials, db)
    if user is None and token:
        user = user_from_token(token, db)
    if user is not None:
        if user.id != user_id:
            raise HTTPException(403, "无权访问该资源")
        return
    if _is_public_novel_cover(user_id, filename, db):
        return
    raise HTTPException(401, "未登录或登录已过期")


@router.get("/{user_id}/{filename}")
def get_gen_asset(
    user_id: int,
    filename: str,
    token: str | None = Query(None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
):
    # 防止路径穿越
    name = Path(filename).name
    if name != filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "非法文件名")
    _authorize_asset(user_id, name, token, credentials, db)
    path = (GEN_ROOT / str(user_id) / name).resolve()
    root = (GEN_ROOT / str(user_id)).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(404, "文件不存在")
    ext = path.suffix.lstrip(".").lower()
    # 文件名多为内容哈希,可长期缓存,显著减轻书城/书架反复拉封面
    headers = {
        "Cache-Control": "public, max-age=2592000, immutable",
        "ETag": f'"{user_id}-{name}"',
    }
    return FileResponse(
        path,
        media_type=_MIME.get(ext, "application/octet-stream"),
        filename=name,
        headers=headers,
    )
