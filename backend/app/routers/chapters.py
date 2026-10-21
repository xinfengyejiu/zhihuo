"""大纲(卷/章)与章节正文:AI 生成、版本、状态机。"""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..ai import context as ai_context
from ..ai.gateway import log_usage, resolve_route, stream_chat
from ..ai.outline_catalog import chapter_key, parse_catalog
from ..ai.reparagraph import ai_reparagraph_novel, smart_reparagraph
from ..database import SessionLocal, get_db
from ..models import (
    Chapter,
    ChapterVersion,
    NovelRule,
    OutlineDoc,
    ReviewRecord,
    RollingSummary,
    User,
    Volume,
)
from ..routers.novels import get_owned_novel, recompute_words
from ..schemas import (
    AdoptIn,
    ChapterContentIn,
    ChapterIn,
    ChapterOut,
    ChapterUpdateIn,
    VolumeIn,
    VolumeOut,
)
from ..security import get_current_user

router = APIRouter(prefix="/novels/{novel_id}", tags=["chapters"])


# ---------- 卷 ----------
@router.get("/volumes", response_model=list[VolumeOut])
def list_volumes(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(Volume).filter_by(novel_id=novel_id).order_by(Volume.sort, Volume.id).all()


@router.post("/volumes", response_model=VolumeOut)
def create_volume(
    novel_id: int, body: VolumeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    max_sort = db.query(func.coalesce(func.max(Volume.sort), -1)).filter_by(novel_id=novel_id).scalar()
    row = Volume(novel_id=novel_id, title=body.title, outline=body.outline, sort=body.sort or max_sort + 1)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/volumes/{volume_id}", response_model=VolumeOut)
def update_volume(
    novel_id: int,
    volume_id: int,
    body: VolumeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    row = db.query(Volume).filter_by(id=volume_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "卷不存在")
    row.title, row.outline, row.sort = body.title, body.outline, body.sort
    db.commit()
    db.refresh(row)
    return row


@router.delete("/volumes/{volume_id}")
def delete_volume(
    novel_id: int, volume_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    row = db.query(Volume).filter_by(id=volume_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "卷不存在")
    count = db.query(Chapter).filter(Chapter.volume_id == volume_id).count()
    if count:
        raise HTTPException(400, f"该卷下还有 {count} 章,请先移走或删除")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------- 章节 ----------
def _get_chapter(novel_id: int, chapter_id: int, user: User, db: Session) -> Chapter:
    get_owned_novel(novel_id, user, db)
    chapter = db.query(Chapter).filter_by(id=chapter_id, novel_id=novel_id).first()
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    return chapter


def _last_reject_comment(db: Session, chapter_id: int) -> str | None:
    """最近一次驳回意见;若之后已审核通过则不再返回。"""
    rec = (
        db.query(ReviewRecord)
        .filter_by(chapter_id=chapter_id, action="rejected")
        .order_by(ReviewRecord.created_at.desc())
        .first()
    )
    if rec is None or not (rec.comment or "").strip():
        return None
    later_ok = (
        db.query(ReviewRecord.id)
        .filter(
            ReviewRecord.chapter_id == chapter_id,
            ReviewRecord.action == "approved",
            ReviewRecord.created_at > rec.created_at,
        )
        .first()
    )
    if later_ok:
        return None
    return rec.comment.strip()


def _to_chapter_out(db: Session, chapter: Chapter) -> ChapterOut:
    out = ChapterOut.model_validate(chapter)
    out.last_reject_comment = _last_reject_comment(db, chapter.id)
    return out


def _novel_outline_text(db: Session, novel_id: int, cap: int = 12000) -> str | None:
    """汇总本书全部总大纲文档,作为创作蓝图注入(超长截断)。"""
    docs = db.query(OutlineDoc).filter_by(novel_id=novel_id).order_by(OutlineDoc.sort, OutlineDoc.id).all()
    if not docs:
        return None
    parts: list[str] = []
    total = 0
    for d in docs:
        piece = f"### {d.title}\n{(d.content or '').strip()}"
        parts.append(piece)
        total += len(piece)
        if total >= cap:
            break
    text = "\n\n".join(parts)
    return text[:cap] if len(text) > cap else text


# ---------- 总大纲文档(创作蓝图,与卷/章节独立) ----------
@router.get("/outline-docs")
def list_outline_docs(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    docs = db.query(OutlineDoc).filter_by(novel_id=novel_id).order_by(OutlineDoc.sort, OutlineDoc.id).all()
    return [
        {"id": d.id, "title": d.title, "word_count": len(d.content or ""), "updated_at": d.updated_at}
        for d in docs
    ]


@router.post("/outline-docs")
def create_outline_doc(novel_id: int, body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    title = str(body.get("title") or "").strip() or "大纲"
    content = str(body.get("content") or "")
    max_sort = db.query(func.coalesce(func.max(OutlineDoc.sort), -1)).filter_by(novel_id=novel_id).scalar()
    doc = OutlineDoc(novel_id=novel_id, title=title[:128], content=content, sort=max_sort + 1)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "title": doc.title, "word_count": len(doc.content), "updated_at": doc.updated_at}


@router.get("/outline-docs/{doc_id}")
def get_outline_doc(novel_id: int, doc_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    doc = db.query(OutlineDoc).filter_by(id=doc_id, novel_id=novel_id).first()
    if doc is None:
        raise HTTPException(404, "大纲文档不存在")
    return {"id": doc.id, "title": doc.title, "content": doc.content, "updated_at": doc.updated_at}


@router.put("/outline-docs/{doc_id}")
def update_outline_doc(
    novel_id: int, doc_id: int, body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    doc = db.query(OutlineDoc).filter_by(id=doc_id, novel_id=novel_id).first()
    if doc is None:
        raise HTTPException(404, "大纲文档不存在")
    if body.get("title") is not None:
        doc.title = str(body["title"]).strip()[:128] or doc.title
    if body.get("content") is not None:
        doc.content = str(body["content"])
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "title": doc.title, "word_count": len(doc.content), "updated_at": doc.updated_at}


@router.delete("/outline-docs/{doc_id}")
def delete_outline_doc(novel_id: int, doc_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    doc = db.query(OutlineDoc).filter_by(id=doc_id, novel_id=novel_id).first()
    if doc is None:
        raise HTTPException(404, "大纲文档不存在")
    db.delete(doc)
    db.commit()
    return {"ok": True}


@router.get("/chapters", response_model=list[ChapterOut])
def list_chapters(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(Chapter).filter_by(novel_id=novel_id).order_by(Chapter.sort, Chapter.id).all()


@router.post("/chapters", response_model=ChapterOut)
def create_chapter(
    novel_id: int, body: ChapterIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    if body.volume_id is not None:
        if not db.query(Volume).filter_by(id=body.volume_id, novel_id=novel_id).first():
            raise HTTPException(400, "目标卷不存在")
    max_sort = db.query(func.coalesce(func.max(Chapter.sort), -1)).filter_by(novel_id=novel_id).scalar()
    payload = body.model_dump()
    row = Chapter(novel_id=novel_id, sort=max_sort + 1, **payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/chapters/{chapter_id}", response_model=ChapterOut)
def get_chapter(novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _to_chapter_out(db, _get_chapter(novel_id, chapter_id, user, db))


@router.put("/chapters/{chapter_id}", response_model=ChapterOut)
def update_chapter(
    novel_id: int,
    chapter_id: int,
    body: ChapterUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    for field in ("title", "volume_id", "sort", "outline", "characters_involved", "foreshadow_note"):
        value = getattr(body, field)
        if value is not None:
            setattr(chapter, field, value)
    db.commit()
    db.refresh(chapter)
    return chapter


@router.delete("/chapters/{chapter_id}")
def delete_chapter(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    db.delete(chapter)
    recompute_words(db, chapter.novel)
    db.commit()
    return {"ok": True}


def _current_content(chapter: Chapter) -> str:
    for v in chapter.versions:
        if v.id == chapter.current_version_id:
            return v.content
    return ""


@router.get("/chapters/{chapter_id}/content")
def get_chapter_content(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    ai_draft = next((v for v in chapter.versions if v.kind == "ai_draft"), None)
    return {
        "chapter_id": chapter.id,
        "status": chapter.status,
        "content": _current_content(chapter),
        "current_version_id": chapter.current_version_id,
        "ai_draft": {"id": ai_draft.id, "content": ai_draft.content} if ai_draft else None,
    }


@router.put("/chapters/{chapter_id}/content", response_model=ChapterOut)
def save_chapter_content(
    novel_id: int,
    chapter_id: int,
    body: ChapterContentIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存正文:每次生成一个 manual 版本;驳回章节保存后自动回到草稿。"""
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    content = body.content.strip()
    version = ChapterVersion(
        chapter_id=chapter.id,
        kind="manual",
        content=content,
        word_count=len(content),
        created_by_id=user.id,
    )
    db.add(version)
    db.flush()
    chapter.current_version_id = version.id
    chapter.word_count = len(content)
    if chapter.status == "rejected":
        chapter.status = "draft"
    recompute_words(db, chapter.novel)
    db.commit()
    db.refresh(chapter)
    return _to_chapter_out(db, chapter)


async def _collect_model_text(
    db: Session,
    user_id: int,
    novel_id: int,
    purpose: str,
    messages: list[dict],
    *,
    max_tokens_override: int = 8192,
) -> str:
    parts: list[str] = []
    usage = None
    model_name = ""
    async for ev in stream_chat(
        db,
        user_id,
        novel_id,
        purpose,
        messages,
        temperature_override=0.2,
        max_tokens_override=max_tokens_override,
        product_key="novel",
    ):
        et = ev.get("type")
        if et == "delta":
            parts.append(str(ev.get("content") or ""))
        elif et == "reasoning" and not parts:
            # 部分思考模型把正文放在 reasoning;先攒着,若无 delta 再启用
            pass
        elif et == "usage":
            usage = ev
        elif et == "model":
            model_name = str(ev.get("name") or ev.get("model") or "")
    text = "".join(parts).strip()
    if usage:
        log_usage(
            db,
            user_id,
            novel_id,
            purpose,
            {
                "provider_name": usage.get("provider_name"),
                "model_name": usage.get("model_name") or model_name,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "_product_key": "novel",
            },
        )
    return text


def _save_reparagraph_version(db: Session, chapter: Chapter, user_id: int, new: str) -> None:
    version = ChapterVersion(
        chapter_id=chapter.id,
        kind="manual",
        content=new,
        word_count=len(new),
        created_by_id=user_id,
    )
    db.add(version)
    db.flush()
    chapter.current_version_id = version.id
    chapter.word_count = len(new)
    if chapter.status == "rejected":
        chapter.status = "draft"


async def _ai_reparagraph_chapter_content(
    db: Session,
    user_id: int,
    novel_id: int,
    old: str,
) -> tuple[str, str]:
    """调用润色用途模型做网文分段;失败回退规则短段。"""
    purpose = "polish"
    try:
        resolve_route(db, user_id, purpose)
    except HTTPException:
        purpose = "chapter"
        resolve_route(db, user_id, purpose)

    # 分段输出≈原文+换行,按输入估 max_tokens,避免过大浪费
    est = max(1024, min(8192, len(re.sub(r"\s+", "", old or "")) + 800))

    async def complete(messages: list[dict]) -> str:
        return await _collect_model_text(
            db,
            user_id,
            novel_id,
            purpose,
            messages,
            max_tokens_override=est,
        )

    return await ai_reparagraph_novel(old, complete=complete)


@router.post("/chapters/{chapter_id}/reparagraph")
async def reparagraph_chapter(
    novel_id: int,
    chapter_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """单章 AI 网文分段:对话独立、短段落;校验不改字。"""
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    old = _current_content(chapter) or ""
    if not old.strip():
        return {"ok": True, "changed": False, "message": "正文为空", "chapter_id": chapter.id}

    new, source = await _ai_reparagraph_chapter_content(db, user.id, novel_id, old)
    if new == old:
        return {
            "ok": True,
            "changed": False,
            "message": "无需调整",
            "chapter_id": chapter.id,
            "source": source,
        }

    _save_reparagraph_version(db, chapter, user.id, new)
    recompute_words(db, chapter.novel)
    db.commit()
    db.refresh(chapter)
    paras_before = max(1, old.count("\n\n") + 1)
    paras_after = max(1, new.count("\n\n") + 1)
    return {
        "ok": True,
        "changed": True,
        "source": source,
        "chapter_id": chapter.id,
        "title": chapter.title,
        "paras_before": paras_before,
        "paras_after": paras_after,
        "chapter": _to_chapter_out(db, chapter).model_dump(),
        "message": (
            f"已整理版式({paras_before}→{paras_after} 段)"
            if source == "skip"
            else f"AI 已按网文格式分段({paras_before}→{paras_after} 段"
            + ("" if source == "ai" else ",部分回退规则")
            + ")"
        ),
    }


@router.post("/chapters/reparagraph-all")
async def reparagraph_all_chapters(
    novel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全书 AI 网文分段(NDJSON 进度流)。每章调用模型,耗时随章数增加。"""
    novel = get_owned_novel(novel_id, user, db)
    chapter_ids = [
        int(row[0])
        for row in db.query(Chapter.id)
        .filter_by(novel_id=novel_id)
        .order_by(Chapter.sort, Chapter.id)
        .all()
    ]
    try:
        resolve_route(db, user.id, "polish")
    except HTTPException:
        resolve_route(db, user.id, "chapter")

    user_id = user.id
    novel_id_ = novel.id
    total = len(chapter_ids)

    async def event_stream():
        changed = 0
        skipped = 0
        failed = 0
        def pack(ev: dict) -> str:
            return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

        yield pack({"type": "status", "detail": f"开始 AI 网文分段,共 {total} 章"})
        for i, cid in enumerate(chapter_ids, start=1):
            s = SessionLocal()
            try:
                ch = s.query(Chapter).filter_by(id=cid, novel_id=novel_id_).first()
                if ch is None:
                    skipped += 1
                    continue
                old = ""
                for v in ch.versions:
                    if v.id == ch.current_version_id:
                        old = v.content or ""
                        break
                title = ch.title
                if not (old or "").strip():
                    skipped += 1
                    yield pack(
                        {
                            "type": "progress",
                            "current": i,
                            "total": total,
                            "chapter_id": cid,
                            "title": title,
                            "changed": False,
                            "detail": "空正文,跳过",
                        }
                    )
                    continue
                try:
                    new, source = await _ai_reparagraph_chapter_content(s, user_id, novel_id_, old)
                except Exception as e:
                    failed += 1
                    yield pack(
                        {
                            "type": "progress",
                            "current": i,
                            "total": total,
                            "chapter_id": cid,
                            "title": title,
                            "changed": False,
                            "detail": f"失败:{e}",
                        }
                    )
                    continue
                if new == old:
                    skipped += 1
                    yield pack(
                        {
                            "type": "progress",
                            "current": i,
                            "total": total,
                            "chapter_id": cid,
                            "title": title,
                            "changed": False,
                            "source": source,
                            "detail": "无需调整",
                        }
                    )
                    continue
                _save_reparagraph_version(s, ch, user_id, new)
                recompute_words(s, ch.novel)
                s.commit()
                changed += 1
                yield pack(
                    {
                        "type": "progress",
                        "current": i,
                        "total": total,
                        "chapter_id": cid,
                        "title": title,
                        "changed": True,
                        "source": source,
                        "detail": f"已分段({source})",
                    }
                )
            except Exception as e:
                s.rollback()
                failed += 1
                yield pack(
                    {
                        "type": "progress",
                        "current": i,
                        "total": total,
                        "chapter_id": cid,
                        "changed": False,
                        "detail": f"异常:{e}",
                    }
                )
            finally:
                s.close()

        yield pack(
            {
                "type": "done",
                "total": total,
                "changed": changed,
                "skipped": skipped,
                "failed": failed,
                "message": f"完成:分段 {changed} 章,跳过 {skipped},失败 {failed}",
            }
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.post("/chapters/{chapter_id}/adopt", response_model=ChapterOut)
def adopt_ai_draft(
    novel_id: int,
    chapter_id: int,
    body: AdoptIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """采纳 AI 初稿为当前内容。"""
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    version = db.query(ChapterVersion).filter_by(id=body.version_id, chapter_id=chapter.id).first()
    if version is None:
        raise HTTPException(404, "版本不存在")
    chapter.current_version_id = version.id
    chapter.word_count = version.word_count
    recompute_words(db, chapter.novel)
    db.commit()
    db.refresh(chapter)
    return _to_chapter_out(db, chapter)


# ---------- 状态机:提交审核 / 撤回 / 通过 / 驳回 ----------
MIN_SUBMIT_WORDS = 100


@router.post("/chapters/{chapter_id}/submit", response_model=ChapterOut)
def submit_review(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    if chapter.status not in ("draft", "rejected"):
        raise HTTPException(400, "只有草稿/被驳回的章节可以提交审核")
    if chapter.word_count < MIN_SUBMIT_WORDS:
        raise HTTPException(400, f"正文字数不足 {MIN_SUBMIT_WORDS} 字,请先保存内容")
    chapter.status = "pending_review"
    db.add(ReviewRecord(chapter_id=chapter.id, version_id=chapter.current_version_id, reviewer_id=user.id, action="submitted"))
    db.commit()
    db.refresh(chapter)
    return _to_chapter_out(db, chapter)


@router.post("/chapters/{chapter_id}/withdraw", response_model=ChapterOut)
def withdraw_review(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    if chapter.status != "pending_review":
        raise HTTPException(400, "只有待审核的章节可以撤回")
    chapter.status = "draft"
    db.commit()
    db.refresh(chapter)
    return _to_chapter_out(db, chapter)


# ---------- AI 生成(SSE) ----------
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chapters/{chapter_id}/generate")
async def generate_chapter(
    novel_id: int,
    chapter_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """流式生成本章正文。body: {mode: chapter|continue|polish|rewrite|fix_reject, instruction?}"""
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    if chapter.status not in ("draft", "rejected"):
        raise HTTPException(400, "当前状态不允许生成(请先撤回审核或修改被驳回章节)")
    mode = body.get("mode", "chapter")
    instruction = body.get("instruction") or None
    # 按驳回改:自动注入最近驳回意见
    if mode == "fix_reject":
        reject_comment = _last_reject_comment(db, chapter.id)
        if not reject_comment and not (instruction or "").strip():
            raise HTTPException(400, "没有驳回意见可参考,请先在审核台驳回并填写意见")
        prefix = "【按驳回建议修改】"
        if instruction and str(instruction).lstrip().startswith(prefix):
            pass
        elif instruction and instruction.strip():
            instruction = f"{prefix}\n## 审核驳回意见\n{reject_comment or '(见下方补充)'}\n\n## 作者补充\n{instruction.strip()}"
        else:
            instruction = f"{prefix}\n## 审核驳回意见\n{reject_comment}"
        mode = "polish"
    novel = chapter.novel

    # 预检路由配置,错误在此处抛出为标准 HTTP 错误
    purpose = "polish" if mode == "polish" else "chapter"
    resolve_route(db, user.id, purpose)

    previous = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.sort < chapter.sort)
        .order_by(Chapter.sort.desc())
        .first()
    )
    current = _current_content(chapter)
    messages = ai_context.build_chapter_messages(
        db,
        novel,
        chapter,
        mode,
        instruction,
        previous,
        # 长篇策略:正文生成不再灌整本总大纲,由 context 按邻近章纲+滚动前情组装
        outline_text=None,
        current_content=current,
    )

    # Skill 注入:按阶段取书上挂载的技能,系统提示词追加;温度可被技能参数覆盖
    from ..routers.skills import get_mounted_skill

    skill_stage = "polish" if mode == "polish" else "chapter"
    skill = get_mounted_skill(db, novel.id, skill_stage)
    from ..ai.skill_pack import build_skill_context

    temperature_override = None
    if skill:
        messages[0] = {
            "role": "system",
            "content": messages[0]["content"] + "\n\n" + build_skill_context(skill),
        }
        temperature_override = (skill.params or {}).get("temperature")

    # 知识库 RAG:只取少量短片段,避免挤占正文生成额度
    kb_notice = None
    try:
        from ..ai.retrieval import retrieve

        query_parts = [chapter.title, chapter.outline or "", instruction or ""]
        refs = await retrieve(db, user.id, novel.id, " ".join(p for p in query_parts if p), top_k=4)
        if refs:
            ref_text = "\n\n".join(
                f"[片段{i + 1}]\n{(r['content'] or '')[:400]}" for i, r in enumerate(refs)
            )
            messages[1] = {
                "role": "user",
                "content": messages[1]["content"]
                + "\n\n## 参考资料(知识库短摘,可核对设定,勿大段照抄)\n"
                + ref_text,
            }
            kb_notice = f"已检索注入 {len(refs)} 条知识库短摘"
    except HTTPException as e:
        # Embedding 未配置等情况下不阻塞生成
        kb_notice = f"知识库检索跳过:{e.detail}"

    user_id, novel_id_, chapter_id_ = user.id, novel.id, chapter.id
    chapter_title = chapter.title

    async def event_stream():
        content_parts: list[str] = []
        usage: dict = {}
        s = SessionLocal()
        try:
            if kb_notice:
                yield _sse({"type": "notice", "detail": kb_notice})
            async for event in stream_chat(
                s,
                user_id,
                novel_id_,
                purpose,
                messages,
                temperature_override=temperature_override,
                max_tokens_override=8192,
                allow_reasoning_only=True,
            ):
                if event["type"] == "delta":
                    content_parts.append(event["content"])
                elif event["type"] == "usage":
                    usage = event
                # notice(推理回填提示)一并转给前端
                yield _sse(event)
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            yield _sse({"type": "error", "detail": detail})
            return
        except Exception as e:
            yield _sse({"type": "error", "detail": f"生成异常:{e}"})
            return
        finally:
            s.close()

        content = "".join(content_parts).strip()
        if not content:
            yield _sse(
                {
                    "type": "error",
                    "detail": "模型未返回正文。请换非推理模型,或到个人中心把「章节生成」用途的 max_tokens 调大",
                }
            )
            return

        # 用独立会话落库
        s2 = SessionLocal()
        try:
            version = ChapterVersion(
                chapter_id=chapter_id_,
                kind="ai_draft",
                content=content,
                word_count=len(content),
                model_name=usage.get("model_name"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                created_by_id=user_id,
            )
            s2.add(version)
            s2.flush()
            ch = s2.get(Chapter, chapter_id_)
            if ch.current_version_id is None:
                ch.current_version_id = version.id
            log_usage(s2, user_id, novel_id_, purpose, usage)
            yield _sse({"type": "done", "version_id": version.id, "word_count": version.word_count})
        finally:
            s2.close()

    sse_headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=sse_headers)


@router.post("/generate-outline")
async def generate_outline(
    novel_id: int, body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """流式生成卷章目录(不落库)。body: {instruction?, mode?: catalog|next, count?}

    章节过多时自动压缩已有目录;指令含「继续/往后」或已有≥40章时切到 next 续写模式。
    """
    novel = get_owned_novel(novel_id, user, db)
    resolve_route(db, user.id, "outline")
    mode = str(body.get("mode") or "catalog")
    count = int(body.get("count") or 12)
    existing_n = db.query(Chapter).filter_by(novel_id=novel.id).count()
    instruction = body.get("instruction")
    if mode != "next" and ai_context.looks_like_continue_outline(
        str(instruction) if instruction is not None else None, existing_n
    ):
        mode = "next"
    if mode == "next" and count < 12:
        count = 12
    # 蓝图封顶:续写更短,全书拆目录中等;超多章节再压一档
    if mode == "next":
        blueprint_cap = 6000
    elif existing_n >= 80:
        blueprint_cap = 8000
    else:
        blueprint_cap = 14000
    messages = ai_context.build_outline_messages(
        db,
        novel,
        instruction,
        count,
        outline_text=_novel_outline_text(db, novel.id, cap=blueprint_cap),
        mode=mode,
    )
    temperature_override = None
    try:
        from ..routers.skills import get_mounted_skill

        from ..ai.skill_pack import build_skill_context

        skill = get_mounted_skill(db, novel.id, "outline")
        if skill:
            messages[0] = {
                "role": "system",
                "content": messages[0]["content"] + "\n\n" + build_skill_context(skill),
            }
            temperature_override = (skill.params or {}).get("temperature")
    except HTTPException:
        pass
    messages[0]["content"] += "\n\n输出格式优先于技能文风:本次只输出 JSON 目录,不要写成分析、散文或正文。"
    user_id, nid = user.id, novel.id

    async def event_stream():
        parts: list[str] = []
        usage: dict = {}
        s = SessionLocal()
        try:
            yield _sse(
                {
                    "type": "status",
                    "detail": (
                        f"续写模式 · 已有 {existing_n} 章已压缩进上下文"
                        if mode == "next"
                        else f"拆目录模式 · 已有 {existing_n} 章"
                        + ("(已压缩)" if existing_n > ai_context.OUTLINE_EXISTING_FULL else "")
                    ),
                    "mode": mode,
                    "existing_chapters": existing_n,
                }
            )
            async for event in stream_chat(
                s,
                user_id,
                nid,
                "outline",
                messages,
                temperature_override=temperature_override,
                max_tokens_override=8192,
            ):
                if event["type"] == "delta":
                    parts.append(event["content"])
                elif event["type"] == "usage":
                    usage = event
                yield _sse(event)
        except HTTPException as e:
            yield _sse({"type": "error", "detail": e.detail})
            return
        finally:
            s.close()
        s2 = SessionLocal()
        try:
            log_usage(s2, user_id, nid, "outline", usage)
        finally:
            s2.close()
        yield _sse({"type": "done", "mode": mode})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def materialize_catalog(
    db: Session,
    novel_id: int,
    text: str,
    *,
    volume_id: int | None = None,
    new_volume_title: str | None = None,
    dry_run: bool = False,
    commit: bool = True,
) -> dict:
    """把大纲/目录文本落成 Volume + Chapter。chat 应用到书架与大纲页共用。"""
    from ..models import Novel as NovelModel

    if db.query(NovelModel).filter_by(id=novel_id).first() is None:
        raise HTTPException(404, "小说不存在")

    groups, source = parse_catalog(text)
    pinned = None
    if volume_id:
        pinned = db.query(Volume).filter_by(id=volume_id, novel_id=novel_id).first()
        if pinned is None:
            raise HTTPException(400, "目标卷不存在")

    existing_volumes = {v.title.strip(): v for v in db.query(Volume).filter_by(novel_id=novel_id).all()}
    first_existing = db.query(Volume).filter_by(novel_id=novel_id).order_by(Volume.sort).first()
    seen: set[str] = set()
    for ch in db.query(Chapter).filter_by(novel_id=novel_id).all():
        seen.add(ch.title.strip())
        seen.add(chapter_key(ch.title))

    parsed_count = sum(len(group.chapters) for group in groups)
    planned: list[dict] = []
    created_chapters = 0
    hit_cap = False
    for group in groups:
        chapters = []
        for ch in group.chapters:
            if created_chapters >= 300:
                hit_cap = True
                break
            key = chapter_key(ch.title)
            if not key or key in seen or ch.title.strip() in seen:
                continue
            seen.add(key)
            seen.add(ch.title.strip())
            chapters.append({"title": ch.title[:128], "outline": ch.outline})
            created_chapters += 1
        if chapters:
            planned.append({"title": group.title, "outline": group.outline, "chapters": chapters})
        if hit_cap:
            break

    if created_chapters == 0:
        if parsed_count > 0:
            message = "这些章节标题已经存在,没有新建。"
        elif source == "json":
            message = "没有从生成结果里解析出章节。请再生成一次;如果模型只输出了说明文字,换一个非推理模型后再试。"
        else:
            message = "没有解析出新的章节。请确认每行至少有章节标题。"
        return {"ok": True, "created": 0, "volumes_created": 0, "volumes": [], "source": source, "message": message}

    fallback = pinned.title if pinned else (new_volume_title or (first_existing.title if first_existing else "第一卷"))
    preview: list[dict] = []
    new_titles: list[str] = []
    known = set(existing_volumes)
    for group in planned:
        title = (group["title"] or fallback)[:128]
        if title not in known:
            known.add(title)
            new_titles.append(title)
        preview.append({"title": title, "outline": group["outline"], "chapters": group["chapters"]})

    stripped = text.strip()
    truncated = hit_cap or (source == "json" and not (stripped.endswith("}") or stripped.endswith("```")))
    payload = {
        "ok": True,
        "created": created_chapters,
        "volumes_created": len(new_titles),
        "volumes": preview,
        "source": source,
        "truncated": truncated,
        "volume_title": preview[-1]["title"],
        "message": "目录较长,已截取前 300 章,可再生成一次补齐后面的章节。" if hit_cap else None,
    }
    if dry_run:
        return payload

    max_sort = db.query(func.coalesce(func.max(Chapter.sort), -1)).filter_by(novel_id=novel_id).scalar()
    max_volume_sort = db.query(func.coalesce(func.max(Volume.sort), -1)).filter_by(novel_id=novel_id).scalar()
    outline_by_title = {}
    for item in preview:
        if item.get("outline") and item["title"] not in outline_by_title:
            outline_by_title[item["title"]] = item["outline"]

    for title in new_titles:
        max_volume_sort += 1
        vol = Volume(
            novel_id=novel_id,
            title=title,
            outline=outline_by_title.get(title),
            sort=max_volume_sort,
        )
        db.add(vol)
        db.flush()
        existing_volumes[title] = vol

    last_volume = None
    for item in preview:
        vol = existing_volumes[item["title"]]
        last_volume = vol
        if item.get("outline") and not vol.outline:
            vol.outline = item["outline"]
        for ch in item["chapters"]:
            max_sort += 1
            db.add(
                Chapter(
                    novel_id=novel_id,
                    volume_id=vol.id,
                    title=ch["title"],
                    outline=ch["outline"],
                    sort=max_sort,
                )
            )
    if commit:
        db.commit()
    else:
        db.flush()
    payload["volume_id"] = last_volume.id if last_volume else None
    return payload


@router.post("/apply-outline")
def apply_outline(novel_id: int, body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """把大纲文本批量创建为卷和章节。

    优先解析 JSON 目录;否则识别「卷:」/「第X卷」与「第N章 标题|剧情要点」。
    body.dry_run=true 时只返回将创建的目录,不写库。
    未声明卷时章节落入目标卷(指定 volume_id / 新卷名 / 第一卷)。
    """
    get_owned_novel(novel_id, user, db)
    return materialize_catalog(
        db,
        novel_id,
        str(body.get("text") or ""),
        volume_id=body.get("volume_id"),
        new_volume_title=str(body.get("new_volume_title") or "").strip() or None,
        dry_run=bool(body.get("dry_run")),
        commit=True,
    )


# ---------- 前情摘要 ----------
def refresh_summary(db: Session, novel) -> str:
    """取最近一章已通过正文,滚动更新前情摘要。"""
    import asyncio

    last = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.status.in_(("approved", "published", "scheduled")))
        .order_by(Chapter.sort.desc())
        .first()
    )
    if last is None:
        raise HTTPException(400, "还没有审核通过的章节,无法生成前情摘要")
    row = db.get(RollingSummary, novel.id)
    previous = row.content if row else ""
    content = _current_content(last)
    messages = ai_context.build_summary_messages(previous, last.title, content)

    async def collect():
        parts = []
        async for event in stream_chat(db, novel.owner_id, novel.id, "summary", messages):
            if event["type"] == "delta":
                parts.append(event["content"])
            elif event["type"] == "usage":
                log_usage(db, novel.owner_id, novel.id, "summary", event)
        return "".join(parts)

    loop = asyncio.new_event_loop()
    try:
        summary = loop.run_until_complete(collect())
    finally:
        loop.close()
    if row is None:
        row = RollingSummary(novel_id=novel.id, content=summary)
        db.add(row)
    else:
        row.content = summary
    db.commit()
    return summary


@router.get("/summary")
def get_summary(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    novel = get_owned_novel(novel_id, user, db)
    row = db.get(RollingSummary, novel.id)
    return {"content": row.content if row else ""}


@router.post("/summary/refresh")
def refresh_summary_api(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    novel = get_owned_novel(novel_id, user, db)
    resolve_route(db, user.id, "summary")
    try:
        summary = refresh_summary(db, novel)
    except HTTPException:
        raise
    return {"content": summary}


# ---------- 规则注入预览(调试用) ----------
@router.get("/chapters/{chapter_id}/context-preview")
def context_preview(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    chapter = _get_chapter(novel_id, chapter_id, user, db)
    previous = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel_id, Chapter.sort < chapter.sort)
        .order_by(Chapter.sort.desc())
        .first()
    )
    messages = ai_context.build_chapter_messages(
        db,
        chapter.novel,
        chapter,
        "chapter",
        None,
        previous,
        current_content=_current_content(chapter),
    )
    total_chars = sum(len(m.get("content") or "") for m in messages)
    return {
        "messages": messages,
        "rule_count": db.query(NovelRule).filter_by(novel_id=novel_id, enabled=True).count(),
        "context_chars": total_chars,
        "strategy": "rolling_summary + nearby_outlines + prev_ending_tail (no full novel text)",
    }
