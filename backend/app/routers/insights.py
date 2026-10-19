"""M3 复盘与伏笔台账。"""

import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..ai import context as ai_context
from ..ai.gateway import log_usage, resolve_route, stream_chat
from ..ai.self_check import build_fix_instruction, run_self_check
from ..database import get_db
from ..models import (
    AnalysisReport,
    Chapter,
    Character,
    Foreshadow,
    RollingSummary,
    User,
)
from ..routers.novels import get_owned_novel
from ..schemas import (
    ForeshadowIn,
    ForeshadowOut,
    ReportOut,
    SelfCheckFixIn,
    SelfCheckIn,
    SelfCheckIssueOut,
    SelfCheckOut,
)
from ..security import get_current_user

router = APIRouter(prefix="/novels/{novel_id}", tags=["insights"])

FORESHADOW_STATUSES = ["planted", "recalled", "overdue"]


# ---------- 伏笔台账 ----------
@router.get("/foreshadows", response_model=list[ForeshadowOut])
def list_foreshadows(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(Foreshadow).filter_by(novel_id=novel_id).order_by(Foreshadow.id.desc()).all()


@router.post("/foreshadows", response_model=ForeshadowOut)
def create_foreshadow(
    novel_id: int, body: ForeshadowIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    if body.status not in FORESHADOW_STATUSES:
        raise HTTPException(400, f"未知伏笔状态:{body.status}")
    row = Foreshadow(novel_id=novel_id, **body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/foreshadows/{foreshadow_id}", response_model=ForeshadowOut)
def update_foreshadow(
    novel_id: int,
    foreshadow_id: int,
    body: ForeshadowIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    row = db.query(Foreshadow).filter_by(id=foreshadow_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "伏笔不存在")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/foreshadows/{foreshadow_id}")
def delete_foreshadow(
    novel_id: int, foreshadow_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    row = db.query(Foreshadow).filter_by(id=foreshadow_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "伏笔不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------- 章节 AI 复盘 ----------
def _chapter_content(chapter: Chapter) -> str:
    for v in chapter.versions:
        if v.id == chapter.current_version_id:
            return v.content
    return ""


@router.post("/chapters/{chapter_id}/self-check", response_model=SelfCheckOut)
def self_check_chapter(
    novel_id: int,
    chapter_id: int,
    body: SelfCheckIn = Body(default_factory=SelfCheckIn),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """规则自检:AI 味词、开头套话、总结腔、无标点长句、超长段、人物衔接。

    纯规则、不调模型;不改正文、不挡保存——只是镜子。
    """
    get_owned_novel(novel_id, user, db)
    chapter = db.query(Chapter).filter_by(id=chapter_id, novel_id=novel_id).first()
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    content = body.content if body.content is not None else _chapter_content(chapter)
    names = [c.name for c in db.query(Character).filter_by(novel_id=novel_id).all() if c.name]
    result = run_self_check(content or "", character_names=names)
    return SelfCheckOut(
        score=result.score,
        word_count=result.word_count,
        para_count=result.para_count,
        avg_para_chars=result.avg_para_chars,
        issue_count=result.issue_count,
        summary=result.summary,
        issues=[
            SelfCheckIssueOut(
                kind=i.kind,
                title=i.title,
                detail=i.detail,
                advice=i.advice,
                excerpt=i.excerpt,
                count=i.count,
                hits=i.hits or [],
            )
            for i in result.issues
        ],
    )


@router.post("/chapters/{chapter_id}/self-check/fix-preview")
def self_check_fix_preview(
    novel_id: int,
    chapter_id: int,
    body: SelfCheckFixIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把勾选的自检项整理成定点修改指令,供前端走章节 polish 生成。"""
    get_owned_novel(novel_id, user, db)
    chapter = db.query(Chapter).filter_by(id=chapter_id, novel_id=novel_id).first()
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    if not body.issues:
        raise HTTPException(400, "请至少勾选一处建议")
    instruction = build_fix_instruction([i.model_dump() for i in body.issues])
    return {"ok": True, "instruction": instruction, "issue_count": len(body.issues)}


@router.get("/chapters/{chapter_id}/reports", response_model=list[ReportOut])
def list_reports(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    chapter = db.query(Chapter).filter_by(id=chapter_id, novel_id=novel_id).first()
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    rows = (
        db.query(AnalysisReport)
        .filter_by(chapter_id=chapter_id)
        .order_by(AnalysisReport.created_at.desc())
        .all()
    )
    return [ReportOut.model_validate(r) for r in rows]


@router.post("/chapters/{chapter_id}/analyze", response_model=ReportOut)
def analyze_chapter(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """对本章跑 AI 复盘:钩子/伏笔/节奏/一致性/矛盾检测。"""
    get_owned_novel(novel_id, user, db)
    chapter = db.query(Chapter).filter_by(id=chapter_id, novel_id=novel_id).first()
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    content = _chapter_content(chapter)
    if len(content.strip()) < 50:
        raise HTTPException(400, "章节还没有正文,无法复盘")
    resolve_route(db, user.id, "analysis")

    summary_row = db.get(RollingSummary, novel_id)
    summary_text = summary_row.content if summary_row else ""
    novel = chapter.novel
    system = ai_context.PERSONA
    user_text = (
        f"## 书名\n《{novel.title}》\n\n"
        f"## 前情提要\n{summary_text or '(无)'}\n\n"
        f"## 本章章纲\n{chapter.outline or '(未填)'}\n\n"
        f"## 伏笔任务\n{chapter.foreshadow_note or '(未填)'}\n\n"
        f"## 本章正文\n{content[:8000]}\n\n"
        "请按照复盘分析师的输出格式,对本章进行完整复盘。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]

    async def collect():
        parts = []
        usage = {}
        async for event in stream_chat(db, user.id, novel_id, "analysis", messages):
            if event["type"] == "delta":
                parts.append(event["content"])
            elif event["type"] == "usage":
                usage = event
        return "".join(parts), usage

    loop = asyncio.new_event_loop()
    try:
        text, usage = loop.run_until_complete(collect())
    finally:
        loop.close()

    report = AnalysisReport(novel_id=novel_id, chapter_id=chapter_id, content=text, model_name=usage.get("model_name"))
    db.add(report)
    log_usage(db, user.id, novel_id, "analysis", usage)
    db.commit()
    db.refresh(report)
    return ReportOut.model_validate(report)
