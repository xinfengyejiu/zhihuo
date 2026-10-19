"""审核工作流:待审列表、详情(含 AI 稿与当前稿)、通过 / 驳回、AI 审查官。"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..ai.gateway import log_usage, resolve_any_purpose, resolve_route, stream_chat
from ..database import SessionLocal, get_db
from ..models import (
    AnalysisReport,
    Chapter,
    Character,
    Novel,
    NovelRule,
    ReviewRecord,
    RollingSummary,
    Skill,
    User,
)
from ..routers.novels import get_owned_novel, recompute_words
from ..security import get_current_user

router = APIRouter(prefix="/review", tags=["review"])


def _owned_chapter(db: Session, user: User, chapter_id: int) -> Chapter:
    chapter = db.query(Chapter).filter_by(id=chapter_id).first()
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    get_owned_novel(chapter.novel_id, user, db)
    return chapter


def _current_content(chapter: Chapter) -> str:
    for v in chapter.versions:
        if v.id == chapter.current_version_id:
            return v.content or ""
    return ""


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/pending")
def pending_list(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """本作者(含协作)全部书的 待审核 / 已通过 / 被驳回 章节。"""
    from ..models import NovelCollaborator

    owned = db.query(Novel).filter_by(owner_id=user.id).all()
    shared_ids = [
        row.novel_id for row in db.query(NovelCollaborator).filter_by(user_id=user.id).all()
    ]
    shared = db.query(Novel).filter(Novel.id.in_(shared_ids or [0])).all() if shared_ids else []
    novels = {n.id: n for n in owned + shared}
    novel_ids = list(novels.keys())
    if not novel_ids:
        return {"pending": [], "approved": [], "rejected": [], "approved_total": 0}

    def serialize(ch: Chapter) -> dict:
        return {
            "id": ch.id,
            "novel_id": ch.novel_id,
            "novel_title": novels[ch.novel_id].title,
            "title": ch.title,
            "status": ch.status,
            "word_count": ch.word_count,
            "updated_at": ch.updated_at,
        }

    pending = (
        db.query(Chapter)
        .filter(Chapter.novel_id.in_(novel_ids), Chapter.status == "pending_review")
        .order_by(Chapter.novel_id, Chapter.sort, Chapter.updated_at)
        .all()
    )
    # 已审核:含通过/排期/已发布;每本书只取最近通过的 3 章,避免导入长篇刷屏
    approved_statuses = ("approved", "scheduled", "published")
    approved_total = (
        db.query(func.count(Chapter.id))
        .filter(Chapter.novel_id.in_(novel_ids), Chapter.status.in_(approved_statuses))
        .scalar()
        or 0
    )
    approved: list[Chapter] = []
    for nid in novel_ids:
        approved.extend(
            db.query(Chapter)
            .filter(Chapter.novel_id == nid, Chapter.status.in_(approved_statuses))
            .order_by(Chapter.updated_at.desc(), Chapter.id.desc())
            .limit(3)
            .all()
        )
    rejected = (
        db.query(Chapter)
        .filter(Chapter.novel_id.in_(novel_ids), Chapter.status == "rejected")
        .order_by(Chapter.novel_id, Chapter.updated_at.desc())
        .limit(50)
        .all()
    )
    return {
        "pending": [serialize(c) for c in pending],
        "approved": [serialize(c) for c in approved],
        "rejected": [serialize(c) for c in rejected],
        "approved_total": int(approved_total),
    }


@router.get("/chapters/{chapter_id}")
def review_detail(chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """审核详情:AI 初稿 vs 当前稿 + 审核记录。"""
    chapter = _owned_chapter(db, user, chapter_id)
    current = next((v for v in chapter.versions if v.id == chapter.current_version_id), None)
    ai_draft = next((v for v in reversed(chapter.versions) if v.kind == "ai_draft"), None)
    records = (
        db.query(ReviewRecord)
        .filter_by(chapter_id=chapter.id)
        .order_by(ReviewRecord.created_at.desc())
        .all()
    )
    last_reject = next((r for r in records if r.action == "rejected" and (r.comment or "").strip()), None)
    # 最近一次落库的 AI 审查报告(复盘表),驳回后重开也可对照
    latest_ai = (
        db.query(AnalysisReport)
        .filter_by(chapter_id=chapter.id)
        .order_by(AnalysisReport.created_at.desc())
        .first()
    )
    novel = db.get(Novel, chapter.novel_id)
    return {
        "chapter": {
            "id": chapter.id,
            "novel_id": chapter.novel_id,
            "novel_title": novel.title if novel else "",
            "title": chapter.title,
            "status": chapter.status,
            "word_count": chapter.word_count,
            "outline": chapter.outline,
            "foreshadow_note": chapter.foreshadow_note,
        },
        "current": {"id": current.id, "content": current.content} if current else None,
        "ai_draft": {"id": ai_draft.id, "content": ai_draft.content} if ai_draft else None,
        "last_reject_comment": (last_reject.comment or "").strip() if last_reject else None,
        "last_reject_at": last_reject.created_at if last_reject else None,
        "latest_ai_report": (
            {
                "id": latest_ai.id,
                "content": latest_ai.content,
                "created_at": latest_ai.created_at,
            }
            if latest_ai and (latest_ai.content or "").strip()
            else None
        ),
        "records": [
            {
                "id": r.id,
                "action": r.action,
                "comment": r.comment,
                "created_at": r.created_at,
            }
            for r in records
        ],
    }


@router.post("/chapters/{chapter_id}/approve")
def approve(
    chapter_id: int,
    body: dict | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter = _owned_chapter(db, user, chapter_id)
    if chapter.status != "pending_review":
        raise HTTPException(400, "该章节不在待审核状态")
    chapter.status = "approved"
    recompute_words(db, chapter.novel)
    db.add(
        ReviewRecord(
            chapter_id=chapter.id,
            version_id=chapter.current_version_id,
            reviewer_id=user.id,
            action="approved",
            comment=(body or {}).get("comment"),
        )
    )
    db.commit()

    # 审核通过后滚动更新前情摘要;失败不影响审核结果
    summary_error = None
    try:
        from ..routers.chapters import refresh_summary

        refresh_summary(db, chapter.novel)
    except Exception as e:  # noqa: BLE001 - AI 失败不阻塞审核
        summary_error = str(e)

    return {"ok": True, "status": chapter.status, "summary_error": summary_error}


@router.post("/chapters/{chapter_id}/reject")
def reject(
    chapter_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comment = (body or {}).get("comment") or ""
    if not comment.strip():
        raise HTTPException(400, "驳回必须填写审核意见")
    chapter = _owned_chapter(db, user, chapter_id)
    if chapter.status != "pending_review":
        raise HTTPException(400, "该章节不在待审核状态")
    chapter.status = "rejected"
    db.add(
        ReviewRecord(
            chapter_id=chapter.id,
            version_id=chapter.current_version_id,
            reviewer_id=user.id,
            action="rejected",
            comment=comment,
        )
    )
    db.commit()
    return {"ok": True, "status": chapter.status}


@router.post("/chapters/{chapter_id}/reopen")
def reopen(
    chapter_id: int,
    body: dict | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """已通过章节退回修改:回到草稿,退出阅读器/发布统计字数。"""
    chapter = _owned_chapter(db, user, chapter_id)
    if chapter.status != "approved":
        raise HTTPException(400, "只有审核通过的章节可以退回修改")
    comment = ((body or {}).get("comment") or "").strip() or None
    chapter.status = "draft"
    recompute_words(db, chapter.novel)
    db.add(
        ReviewRecord(
            chapter_id=chapter.id,
            version_id=chapter.current_version_id,
            reviewer_id=user.id,
            action="reopened",
            comment=comment,
        )
    )
    db.commit()
    return {"ok": True, "status": chapter.status}


@router.post("/chapters/{chapter_id}/ai-review")
async def ai_review(
    chapter_id: int,
    body: dict | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """流式 AI 审查官。body: {skill_ids?: number[], instruction?: string, save?: bool}"""
    chapter = _owned_chapter(db, user, chapter_id)
    content = _current_content(chapter)
    if len(content.strip()) < 50:
        raise HTTPException(400, "章节正文太短,无法审查")

    body = body or {}
    skill_ids = body.get("skill_ids") or []
    if not isinstance(skill_ids, list):
        raise HTTPException(400, "skill_ids 必须是数组")
    instruction = str(body.get("instruction") or "").strip()
    save_report = bool(body.get("save", True))

    skills: list[Skill] = []
    if skill_ids:
        skills = (
            db.query(Skill)
            .filter(Skill.user_id == user.id, Skill.id.in_([int(x) for x in skill_ids]))
            .all()
        )
        if len(skills) != len(set(int(x) for x in skill_ids)):
            raise HTTPException(400, "部分技能不存在或不属于当前用户")
        by_id = {s.id: s for s in skills}
        skills = [by_id[int(x)] for x in skill_ids if int(x) in by_id]

    purpose = resolve_any_purpose(db, user.id, ["analysis", "chat", "outline", "chapter"])
    resolve_route(db, user.id, purpose)

    novel = db.get(Novel, chapter.novel_id)
    rules = (
        db.query(NovelRule)
        .filter_by(novel_id=chapter.novel_id, enabled=True)
        .order_by(NovelRule.priority.desc())
        .limit(20)
        .all()
    )
    rule_text = "\n".join(f"- {r.content}" for r in rules) or "(暂无)"
    involved = chapter.characters_involved or []
    if involved:
        chars = db.query(Character).filter(Character.novel_id == chapter.novel_id, Character.id.in_(involved)).all()
    else:
        chars = db.query(Character).filter_by(novel_id=chapter.novel_id).limit(12).all()
    char_text = "\n".join(
        f"- {c.name}({c.role}): 性格={c.personality or '未填'};目标={c.goal or '未填'};备注={c.notes or '无'}"
        for c in chars
    ) or "(暂无人物卡)"
    summary_row = db.get(RollingSummary, chapter.novel_id)
    summary_text = summary_row.content if summary_row and summary_row.content else "(暂无前情)"

    if skills:
        from ..ai.skill_pack import build_skill_context

        skill_block = "\n\n".join(build_skill_context(s) for s in skills)
    else:
        skill_block = (
            "### 默认审查要求\n"
            "请综合审查本章:是否适合通过、有无硬伤、人设是否崩、章纲是否完成,"
            "以及是否存在明显 AI 味(空泛排比、总结腔、模板对话、强行升华等)。"
        )

    temperature_override = None
    for s in skills:
        temp = (s.params or {}).get("temperature")
        if temp is not None:
            try:
                temperature_override = float(temp)
            except (TypeError, ValueError):
                pass
            break

    system = (
        "你是织火平台的 AI 审查官。你的职责是帮助人工审核员判断章节质量,"
        "给出可执行的通过/驳回建议。输出使用简体中文 Markdown,结论清楚,摘录必须来自正文。\n\n"
        f"## 审查技能(必须严格遵循)\n{skill_block}"
    )
    user_text = (
        f"## 书名\n《{novel.title if novel else ''}》\n\n"
        f"## 全书规则\n{rule_text}\n\n"
        f"## 人物设定\n{char_text}\n\n"
        f"## 前情提要\n{summary_text}\n\n"
        f"## 本章标题\n{chapter.title}\n\n"
        f"## 章纲\n{chapter.outline or '(未填)'}\n\n"
        f"## 伏笔任务\n{chapter.foreshadow_note or '(未填)'}\n\n"
        f"## 本章正文({len(content.strip())} 字)\n{content[:12000]}\n\n"
        f"{'## 审核员补充要求\n' + instruction + '\n\n' if instruction else ''}"
        "## 任务\n请按所选审查技能完成本章审核报告。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]

    user_id = user.id
    novel_id = chapter.novel_id
    ch_id = chapter.id
    skill_names = [s.name for s in skills]

    async def event_stream():
        parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict = {}
        s = SessionLocal()
        try:
            if skill_names:
                yield _sse({"type": "notice", "detail": "已启用技能:" + "、".join(skill_names)})
            async for event in stream_chat(
                s,
                user_id,
                novel_id,
                purpose,
                messages,
                temperature_override=temperature_override,
                max_tokens_override=8192,
                allow_reasoning_only=True,
            ):
                et = event.get("type")
                if et == "delta":
                    parts.append(event["content"])
                elif et == "reasoning":
                    reasoning_parts.append(event["content"])
                elif et == "usage":
                    usage = event
                elif et == "notice":
                    # 网关推理兜底提示,转给前端
                    pass
                yield _sse(event)

            # 双保险:若网关未回填,这里再把思考过程提成报告
            if not "".join(parts).strip() and "".join(reasoning_parts).strip():
                salvaged = "".join(reasoning_parts).strip()
                yield _sse({"type": "notice", "detail": "模型主要输出了思考过程,已整理为审查报告"})
                yield _sse({"type": "delta", "content": salvaged})
                parts.append(salvaged)
        except HTTPException as e:
            salvaged = "".join(reasoning_parts).strip()
            if salvaged and not "".join(parts).strip():
                yield _sse({"type": "notice", "detail": "模型主要输出了思考过程,已整理为审查报告"})
                yield _sse({"type": "delta", "content": salvaged})
                parts.append(salvaged)
            else:
                detail = e.detail if isinstance(e.detail, str) else str(e.detail)
                yield _sse({"type": "error", "detail": detail})
                return
        except Exception as e:
            salvaged = "".join(reasoning_parts).strip()
            if salvaged and not "".join(parts).strip():
                yield _sse({"type": "notice", "detail": "模型主要输出了思考过程,已整理为审查报告"})
                yield _sse({"type": "delta", "content": salvaged})
                parts.append(salvaged)
            else:
                yield _sse({"type": "error", "detail": f"审查异常:{e}"})
                return
        finally:
            s.close()

        text = "".join(parts).strip() or "".join(reasoning_parts).strip()
        report_id = None
        if save_report and text:
            s2 = SessionLocal()
            try:
                header = ""
                if skill_names:
                    header = "【审查技能】" + "、".join(skill_names) + "\n\n"
                report = AnalysisReport(
                    novel_id=novel_id,
                    chapter_id=ch_id,
                    content=header + text,
                    model_name=usage.get("model_name"),
                )
                s2.add(report)
                log_usage(s2, user_id, novel_id, purpose, usage)
                s2.commit()
                s2.refresh(report)
                report_id = report.id
            finally:
                s2.close()
        elif usage:
            s2 = SessionLocal()
            try:
                log_usage(s2, user_id, novel_id, purpose, usage)
            finally:
                s2.close()
        if not text:
            yield _sse(
                {
                    "type": "error",
                    "detail": "审查未生成内容。请换非推理模型,或在个人中心把「复盘分析」用途的 max_tokens 调到 8192+",
                }
            )
            return
        yield _sse({"type": "done", "report_id": report_id, "word_count": len(text), "content": text})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
