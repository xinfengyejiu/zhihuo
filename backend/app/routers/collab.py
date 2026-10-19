"""M4 团队协作:按书邀请协作者(editor / reviewer)。

权限模型(v1,粗粒度):
- owner:全部权限(删除书、管理协作者)
- editor / reviewer:通过 get_owned_novel() 获得读写访问
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Novel, NovelCollaborator, User
from ..routers.novels import get_owned_novel
from ..schemas import CollabAddIn, CollabOut, CollabUpdateIn
from ..security import get_current_user

router = APIRouter(prefix="/novels/{novel_id}/collaborators", tags=["collab"])

COLLAB_ROLES = ["editor", "reviewer"]


def _require_owner(novel_id: int, user: User, db: Session) -> Novel:
    novel = get_owned_novel(novel_id, user, db)
    if novel.owner_id != user.id:
        raise HTTPException(403, "只有书的所有者可以管理协作")
    return novel


@router.get("", response_model=list[CollabOut])
def list_collaborators(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    rows = db.query(NovelCollaborator).filter_by(novel_id=novel_id).all()
    out = []
    for row in rows:
        member = db.get(User, row.user_id)
        out.append(
            CollabOut(
                id=row.id,
                user_id=row.user_id,
                username=member.username if member else "?",
                nickname=member.nickname if member else None,
                role=row.role,
            )
        )
    return out


@router.post("", response_model=CollabOut)
def add_collaborator(
    novel_id: int, body: CollabAddIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    novel = _require_owner(novel_id, user, db)
    if body.role not in COLLAB_ROLES:
        raise HTTPException(400, f"角色必须是 {COLLAB_ROLES}")
    member = db.query(User).filter_by(username=body.username).first()
    if member is None:
        raise HTTPException(400, f"用户 {body.username} 不存在")
    if member.id == novel.owner_id:
        raise HTTPException(400, "不能邀请书的所有者")
    if db.query(NovelCollaborator).filter_by(novel_id=novel_id, user_id=member.id).first():
        raise HTTPException(400, "该用户已是协作者")
    row = NovelCollaborator(novel_id=novel_id, user_id=member.id, role=body.role)
    db.add(row)
    db.commit()
    db.refresh(row)
    return CollabOut(
        id=row.id,
        user_id=member.id,
        username=member.username,
        nickname=member.nickname,
        role=row.role,
    )


@router.put("/{collab_id}", response_model=CollabOut)
def update_collaborator(
    novel_id: int,
    collab_id: int,
    body: CollabUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_owner(novel_id, user, db)
    if body.role not in COLLAB_ROLES:
        raise HTTPException(400, f"角色必须是 {COLLAB_ROLES}")
    row = db.query(NovelCollaborator).filter_by(id=collab_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "协作者不存在")
    row.role = body.role
    db.commit()
    db.refresh(row)
    member = db.get(User, row.user_id)
    return CollabOut(
        id=row.id, user_id=row.user_id, username=member.username, nickname=member.nickname, role=row.role
    )


@router.delete("/{collab_id}")
def remove_collaborator(
    novel_id: int, collab_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _require_owner(novel_id, user, db)
    row = db.query(NovelCollaborator).filter_by(id=collab_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "协作者不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
