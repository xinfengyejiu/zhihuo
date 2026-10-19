"""通用资源挂载 API。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import KnowledgeBase, ResourceMount, Skill, User
from ..security import get_current_user

router = APIRouter(prefix="/mounts", tags=["mounts"])


class MountIn(BaseModel):
    product_key: str
    workspace_type: str
    workspace_id: int
    resource_type: str  # skill | kb
    resource_id: int
    stage: str = ""


class MountOut(BaseModel):
    id: int
    product_key: str
    workspace_type: str
    workspace_id: int
    resource_type: str
    resource_id: int
    stage: str
    resource_name: str | None = None

    class Config:
        from_attributes = True


@router.get("", response_model=list[MountOut])
def list_mounts(
    product_key: str,
    workspace_type: str,
    workspace_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ResourceMount)
        .filter_by(
            user_id=user.id,
            product_key=product_key,
            workspace_type=workspace_type,
            workspace_id=workspace_id,
        )
        .all()
    )
    out: list[MountOut] = []
    for r in rows:
        name = None
        if r.resource_type == "skill":
            sk = db.get(Skill, r.resource_id)
            name = sk.name if sk else None
        elif r.resource_type == "kb":
            kb = db.get(KnowledgeBase, r.resource_id)
            name = kb.name if kb else None
        out.append(
            MountOut(
                id=r.id,
                product_key=r.product_key,
                workspace_type=r.workspace_type,
                workspace_id=r.workspace_id,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                stage=r.stage or "",
                resource_name=name,
            )
        )
    return out


@router.post("", response_model=MountOut)
def create_mount(body: MountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.resource_type not in ("skill", "kb"):
        raise HTTPException(400, "resource_type 无效")
    if body.resource_type == "skill":
        if not db.query(Skill).filter_by(id=body.resource_id, user_id=user.id).first():
            raise HTTPException(404, "Skill 不存在")
    else:
        if not db.query(KnowledgeBase).filter_by(id=body.resource_id, user_id=user.id).first():
            raise HTTPException(404, "知识库不存在")
    stage = body.stage or ""
    exists = (
        db.query(ResourceMount)
        .filter_by(
            user_id=user.id,
            product_key=body.product_key,
            workspace_type=body.workspace_type,
            workspace_id=body.workspace_id,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            stage=stage,
        )
        .first()
    )
    if exists:
        return MountOut(
            id=exists.id,
            product_key=exists.product_key,
            workspace_type=exists.workspace_type,
            workspace_id=exists.workspace_id,
            resource_type=exists.resource_type,
            resource_id=exists.resource_id,
            stage=exists.stage or "",
        )
    row = ResourceMount(
        user_id=user.id,
        product_key=body.product_key,
        workspace_type=body.workspace_type,
        workspace_id=body.workspace_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        stage=stage,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return MountOut(
        id=row.id,
        product_key=row.product_key,
        workspace_type=row.workspace_type,
        workspace_id=row.workspace_id,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        stage=row.stage or "",
    )


@router.delete("/{mount_id}")
def delete_mount(mount_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(ResourceMount).filter_by(id=mount_id, user_id=user.id).first()
    if not row:
        raise HTTPException(404, "挂载不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
