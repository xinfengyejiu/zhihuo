"""Token 用量统计与调用失败日志。"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AiCallError, UsageLog, User
from ..security import get_current_user

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/summary")
def summary(
    days: int = 30,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            func.date(UsageLog.created_at).label("date"),
            UsageLog.purpose,
            UsageLog.model_name,
            UsageLog.product_key,
            func.sum(UsageLog.prompt_tokens),
            func.sum(UsageLog.completion_tokens),
        )
        .filter(UsageLog.user_id == user.id, UsageLog.created_at >= since)
        .group_by(
            func.date(UsageLog.created_at),
            UsageLog.purpose,
            UsageLog.model_name,
            UsageLog.product_key,
        )
        .all()
    )
    points = [
        {
            "date": str(r[0]),
            "purpose": r[1],
            "model_name": r[2],
            "product_key": r[3] or "shared",
            "prompt_tokens": int(r[4] or 0),
            "completion_tokens": int(r[5] or 0),
        }
        for r in rows
    ]
    total_prompt = sum(p["prompt_tokens"] for p in points)
    total_completion = sum(p["completion_tokens"] for p in points)
    by_product: dict[str, dict[str, int]] = {}
    for p in points:
        pk = p["product_key"] or "shared"
        bucket = by_product.setdefault(pk, {"prompt_tokens": 0, "completion_tokens": 0})
        bucket["prompt_tokens"] += p["prompt_tokens"]
        bucket["completion_tokens"] += p["completion_tokens"]

    err_q = db.query(AiCallError).filter(AiCallError.user_id == user.id, AiCallError.created_at >= since)
    failed_count = err_q.filter(AiCallError.status == "failed").count()
    degraded_count = err_q.filter(AiCallError.status == "degraded").count()

    return {
        "days": days,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "by_product": by_product,
        "points": points,
        "error_failed_count": failed_count,
        "error_degraded_count": degraded_count,
    }


@router.get("/errors")
def list_errors(
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None, description="failed / degraded / 空=全部"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = db.query(AiCallError).filter(AiCallError.user_id == user.id, AiCallError.created_at >= since)
    if status in ("failed", "degraded"):
        q = q.filter(AiCallError.status == status)
    rows = q.order_by(AiCallError.created_at.desc()).limit(limit).all()
    return {
        "days": days,
        "items": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "purpose": r.purpose,
                "product_key": r.product_key or "shared",
                "provider_name": r.provider_name,
                "model_name": r.model_name,
                "status": r.status,
                "error": r.error,
            }
            for r in rows
        ],
    }
