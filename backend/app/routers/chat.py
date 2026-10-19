"""对话:首页自由聊 + 一书一对话(跨章节共用 session_id),超长自动压缩上下文。"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai.extract import extract_json_object
from ..ai.gateway import log_usage, resolve_any_purpose, stream_chat
from ..database import SessionLocal, get_db
from ..models import (
    Chapter,
    Character,
    CharacterRelationship,
    ChatMessage,
    ChatSession,
    Foreshadow,
    Novel,
    NovelRule,
    OutlineDoc,
    RELATIONSHIP_TYPES,
    RollingSummary,
    Skill,
    Tag,
    User,
    WorldEntry,
    WORLD_CATEGORIES,
    now,
)
from ..routers.novels import get_owned_novel
from ..schemas import ORMModel
from ..security import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])

CHAT_SYSTEM_PROMPT = (
    "你是「织火」AI 创作助手,一位资深网文主编兼创作顾问。"
    "用户可能和你讨论:选题灵感、题材与平台分析、大纲构思、人物设定、桥段与爽点设计、书名起名、文笔润色、更新发布策略等。"
    "回答要求:直接、具体、有创意;给出可执行的方案而不是空泛的建议;需要列举时用简洁条目;默认使用中文。"
)

NOVEL_CHAT_SYSTEM = (
    "你是「织火」书内创作顾问,正在协助作者打磨同一部连载小说。"
    "本书所有章节的讨论共用一个对话线程:请保持设定、人设、伏笔与文风连贯。"
    "依据给出的规则、人物、滚动前情与章纲作答;不要假设读过全书全文。"
    "回答直接、可执行,默认中文。"
)

# 发给模型的近期轮次与总字符预算;超出则压缩更早消息
KEEP_RECENT_MESSAGES = 12
HISTORY_CHAR_BUDGET = 18000
COMPRESSED_SUMMARY_CAP = 2500
PER_MESSAGE_CAP = 3500


class SessionCreateIn(BaseModel):
    title: str | None = None
    novel_id: int | None = None


class SessionRenameIn(BaseModel):
    title: str


class ChatIn(BaseModel):
    message: str
    skill_id: int | None = None
    chapter_id: int | None = None


class SessionOut(ORMModel):
    id: int
    title: str
    updated_at: object
    novel_id: int | None = None
    skill_id: int | None = None
    compressed: bool = False


def _session_payload(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "updated_at": s.updated_at,
        "novel_id": s.novel_id,
        "skill_id": s.skill_id,
        "compressed": bool(s.compressed_summary),
    }


@router.get("/sessions")
def list_sessions(
    product_key: str = "novel",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """默认只列小说产品会话,避免与自媒体会话混在一起。"""
    q = db.query(ChatSession).filter_by(user_id=user.id)
    if product_key:
        q = q.filter_by(product_key=product_key)
    rows = q.order_by(ChatSession.updated_at.desc()).limit(100).all()
    return [_session_payload(s) for s in rows]


@router.post("/sessions")
def create_session(
    body: SessionCreateIn | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    novel_id = body.novel_id if body else None
    if novel_id is not None:
        # 一书一对话:已有则直接返回
        novel = get_owned_novel(novel_id, user, db)
        existing = db.query(ChatSession).filter_by(user_id=user.id, novel_id=novel.id).first()
        if existing:
            return _session_payload(existing)
        title = (body.title if body and body.title else f"《{novel.title}》创作对话")[:64]
        session = ChatSession(
            user_id=user.id,
            novel_id=novel.id,
            product_key="novel",
            workspace_id=novel.id,
            title=title,
        )
    else:
        session = ChatSession(
            user_id=user.id,
            product_key="novel",
            title=(body.title if body and body.title else "新对话")[:64],
        )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_payload(session)


@router.get("/novels/{novel_id}/session")
def get_or_create_novel_session(
    novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """同一部书固定一个对话 id:不存在则创建。跨章节复用。"""
    novel = get_owned_novel(novel_id, user, db)
    session = db.query(ChatSession).filter_by(user_id=user.id, novel_id=novel.id).first()
    created = False
    if session is None:
        session = ChatSession(
            user_id=user.id,
            novel_id=novel.id,
            product_key="novel",
            workspace_id=novel.id,
            title=f"《{novel.title}》创作对话"[:64],
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        created = True
    payload = _session_payload(session)
    payload["created"] = created
    return payload


@router.put("/sessions/{session_id}")
def rename_session(
    session_id: int, body: SessionRenameIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    session = _get_session(db, session_id, user)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    session.title = title[:64]
    db.commit()
    return {"ok": True, "title": session.title}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session(db, session_id, user)
    db.delete(session)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session(db, session_id, user)
    rows = (
        db.query(ChatMessage)
        .filter_by(session_id=session_id)
        .order_by(ChatMessage.id)
        .limit(500)
        .all()
    )
    out = []
    if session.compressed_summary:
        out.append(
            {
                "id": 0,
                "role": "system",
                "content": f"【已自动压缩的早期对话记忆】\n{session.compressed_summary}",
                "model_name": None,
                "chapter_id": None,
                "created_at": session.created_at,
            }
        )
    out.extend(
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "model_name": m.model_name,
            "chapter_id": m.chapter_id,
            "created_at": m.created_at,
        }
        for m in rows
    )
    return out


def _get_session(db: Session, session_id: int, user: User) -> ChatSession:
    session = db.query(ChatSession).filter_by(id=session_id, user_id=user.id).first()
    if session is None:
        raise HTTPException(404, "对话不存在")
    return session


def _novel_system_context(db: Session, novel: Novel) -> str:
    rules = (
        db.query(NovelRule)
        .filter_by(novel_id=novel.id, enabled=True)
        .order_by(NovelRule.priority.desc())
        .limit(20)
        .all()
    )
    rule_text = "\n".join(f"- {r.content}" for r in rules) or "(暂无)"
    chars = db.query(Character).filter_by(novel_id=novel.id).order_by(Character.id).limit(10).all()
    char_text = "\n".join(
        f"- {c.name}({c.role}): {(c.personality or '')[:40]};目标={(c.goal or '')[:40]}" for c in chars
    ) or "(暂无人物卡)"
    summary_row = db.get(RollingSummary, novel.id)
    summary = (summary_row.content if summary_row and summary_row.content else "(暂无前情提要)")[:1200]
    return (
        f"## 当前作品\n《{novel.title}》\n简介:{(novel.summary or '(未填)')[:200]}\n\n"
        f"## 全书规则\n{rule_text}\n\n"
        f"## 主要人物\n{char_text}\n\n"
        f"## 滚动前情提要\n{summary}"
    )


def _build_model_messages(
    db: Session,
    session: ChatSession,
    system_prompt: str,
) -> list[dict]:
    """压缩记忆 + 近期消息,控制总预算。"""
    history = (
        db.query(ChatMessage)
        .filter_by(session_id=session.id)
        .order_by(ChatMessage.id.desc())
        .limit(KEEP_RECENT_MESSAGES)
        .all()
    )
    history.reverse()

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if session.compressed_summary:
        messages.append(
            {
                "role": "system",
                "content": f"## 此前对话压缩记忆(自动生成,请当作已发生的共识)\n{session.compressed_summary[:COMPRESSED_SUMMARY_CAP]}",
            }
        )

    used = sum(len(m["content"]) for m in messages)
    packed: list[dict] = []
    for m in reversed(history):
        chunk = m.content[:PER_MESSAGE_CAP]
        if used + len(chunk) > HISTORY_CHAR_BUDGET and packed:
            break
        packed.append({"role": m.role if m.role in ("user", "assistant", "system") else "user", "content": chunk})
        used += len(chunk)
    packed.reverse()
    messages.extend(packed)
    return messages


async def _compress_if_needed(db: Session, session: ChatSession, user_id: int) -> bool:
    """历史过长时把旧消息压进 compressed_summary,并删除旧行。"""
    total = db.query(ChatMessage).filter_by(session_id=session.id).count()
    if total <= KEEP_RECENT_MESSAGES + 4:
        # 再按字符粗判
        recent = (
            db.query(ChatMessage)
            .filter_by(session_id=session.id)
            .order_by(ChatMessage.id.desc())
            .limit(KEEP_RECENT_MESSAGES)
            .all()
        )
        chars = sum(len(m.content or "") for m in recent) + len(session.compressed_summary or "")
        if chars < HISTORY_CHAR_BUDGET and total <= KEEP_RECENT_MESSAGES * 2:
            return False

    all_msgs = db.query(ChatMessage).filter_by(session_id=session.id).order_by(ChatMessage.id).all()
    if len(all_msgs) <= KEEP_RECENT_MESSAGES:
        return False

    old, keep = all_msgs[: -KEEP_RECENT_MESSAGES], all_msgs[-KEEP_RECENT_MESSAGES:]
    transcript = "\n".join(
        f"{'用户' if m.role == 'user' else 'AI'}:{m.content[:1200]}" for m in old[-40:]
    )
    purpose = resolve_any_purpose(db, user_id, ["summary", "chat", "analysis", "outline"])
    system = (
        "你是长篇连载的对话记忆压缩器。把旧对话压成简洁中文纪要,保留:"
        "已定设定、人物决策、剧情共识、未决问题、作者偏好。"
        f"总长不超过 {COMPRESSED_SUMMARY_CAP} 字,不要写客套话。"
    )
    user = (
        f"## 已有压缩记忆\n{(session.compressed_summary or '(无)')}\n\n"
        f"## 待压缩的旧对话\n{transcript}\n\n"
        "请输出合并后的完整压缩记忆(覆盖式全文)。"
    )
    parts: list[str] = []
    try:
        async for event in stream_chat(
            db,
            user_id,
            session.novel_id,
            purpose,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens_override=2048,
        ):
            if event["type"] == "delta":
                parts.append(event["content"])
            elif event["type"] == "usage":
                log_usage(db, user_id, session.novel_id, purpose, event)
    except HTTPException:
        # 压缩失败不阻断主对话:简单截断拼接
        fallback = (session.compressed_summary or "") + "\n" + transcript
        session.compressed_summary = fallback[-COMPRESSED_SUMMARY_CAP:]
    else:
        text = "".join(parts).strip()
        if text:
            session.compressed_summary = text[:COMPRESSED_SUMMARY_CAP]
        else:
            return False

    for m in old:
        db.delete(m)
    db.commit()
    return True


@router.post("/sessions/{session_id}/chat")
async def chat(
    session_id: int, body: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    session = _get_session(db, session_id, user)
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    if len(message) > 8000:
        raise HTTPException(400, "单条消息过长(上限 8000 字)")
    purpose = resolve_any_purpose(db, user.id, ["chat", "outline", "chapter", "analysis", "polish"])

    novel = None
    if session.novel_id:
        novel = get_owned_novel(session.novel_id, user, db)

    chapter = None
    if body.chapter_id is not None:
        chapter = db.query(Chapter).filter_by(id=body.chapter_id).first()
        if chapter is None or (novel and chapter.novel_id != novel.id):
            raise HTTPException(400, "章节不存在或不属于当前书")
        if novel is None:
            # 自由聊附带 chapter_id 时仍校验归属
            get_owned_novel(chapter.novel_id, user, db)

    # 系统提示
    if novel:
        system_prompt = NOVEL_CHAT_SYSTEM + "\n\n" + _novel_system_context(db, novel)
        if chapter:
            system_prompt += (
                f"\n\n## 当前聚焦章节\n标题:{chapter.title}\n"
                f"章纲:{chapter.outline or '(未填)'}\n"
                f"伏笔任务:{chapter.foreshadow_note or '(无)'}"
            )
    else:
        system_prompt = CHAT_SYSTEM_PROMPT

    if body.skill_id is not None:
        skill = db.query(Skill).filter(Skill.id == body.skill_id, Skill.user_id == user.id).first()
        if skill is None:
            raise HTTPException(400, "技能不存在")
        from ..ai.skill_pack import build_skill_context

        system_prompt = f"{system_prompt}\n\n{build_skill_context(skill)}"
    db.query(ChatSession).filter_by(id=session.id).update({"skill_id": body.skill_id})

    user_content = message
    if chapter:
        user_content = f"（关于章节《{chapter.title}》）\n{message}"

    db.add(
        ChatMessage(
            session_id=session.id,
            role="user",
            content=user_content,
            chapter_id=chapter.id if chapter else None,
        )
    )
    if session.title in ("", "新对话") and not session.novel_id:
        session.title = message[:20]
    db.commit()

    # 发消息前若已过长,先压缩一次
    try:
        await _compress_if_needed(db, session, user.id)
        db.refresh(session)
    except Exception:
        pass

    messages = _build_model_messages(db, session, system_prompt)
    user_id = user.id
    novel_id = session.novel_id

    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def event_stream():
        parts: list[str] = []
        usage: dict = {}
        s = SessionLocal()
        try:
            async for event in stream_chat(s, user_id, novel_id, purpose, messages):
                if event["type"] == "delta":
                    parts.append(event["content"])
                elif event["type"] == "usage":
                    usage = event
                yield _sse(event)
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            yield _sse({"type": "error", "detail": detail})
            return
        finally:
            s.close()
        text = "".join(parts).strip()
        s2 = SessionLocal()
        compressed = False
        try:
            s2.add(
                ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=text or "(AI 未返回内容)",
                    model_name=usage.get("model_name"),
                    chapter_id=chapter.id if chapter else None,
                )
            )
            cs = s2.get(ChatSession, session_id)
            if cs:
                cs.updated_at = now()
            if usage:
                log_usage(s2, user_id, novel_id, purpose, usage)
            s2.commit()
            # 回复后再尝试压缩
            if cs:
                try:
                    compressed = await _compress_if_needed(s2, cs, user_id)
                except Exception:
                    compressed = False
        finally:
            s2.close()
        yield _sse({"type": "done", "compressed": compressed, "session_id": session_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.post("/sessions/{session_id}/compress")
async def compress_now(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """手动触发上下文压缩。"""
    session = _get_session(db, session_id, user)
    ok = await _compress_if_needed(db, session, user.id)
    db.refresh(session)
    return {
        "ok": True,
        "compressed": ok,
        "summary_preview": (session.compressed_summary or "")[:200],
        "message_count": db.query(ChatMessage).filter_by(session_id=session.id).count(),
    }


# ---------- 应用选题到书架 ----------
_APPLY_PROMPT = (
    "你是网文选题策划。根据用户与 AI 的对话记录,提取并补全一个可直接建书的完整方案,"
    "输出严格的 JSON 对象,不要输出任何其他内容。格式:\n"
    '{"title":"书名(4-8字,有记忆点)","summary":"一句话卖点简介,50字内",'
    '"tags":["题材或风格标签,最多4个"],'
    '"outline":"全书大纲,markdown 格式:包含核心创意、故事线(主线/暗线)、分阶段剧情走向(开端-发展-高潮-结局),300-800字",'
    '"volumes":[{"title":"第一卷","outline":"本卷主线一句话",'
    '"chapters":[{"title":"章名(可带第N章)","outline":"本章冲突/事件/钩子,40-120字","characters":"出场人物"}]}],'
    '"world_entries":[{"category":"geography|faction|power|item|event|other","title":"条目标题","content":"条目内容"}],'
    '"rules":["写作规则/禁忌,如:主角不杀无辜;单章2000-3000字;每章结尾留钩子"],'
    '"characters":[{"name":"姓名","role":"protagonist|support|villain|minor","personality":"性格","goal":"目标动机","notes":"外貌/组织等设定"}],'
    '"relationships":[{"from":"姓名","to":"姓名","type":"family|mentor|rival|lover|ally|enemy|other","intimacy":-100到100,"note":"关系备注"}],'
    '"foreshadows":[{"description":"伏笔内容","status":"planted|recalled","note":"补充说明"}]}\n'
    "规则:\n"
    "1. 主角必须有且 role=protagonist,人物 2-6 个;\n"
    "2. 对话里提到的力量体系、地理地点、宗门势力、重要物品、大事件,整理成 world_entries(2-8 条),category 按语义选;\n"
    "3. 对话中明确约定的写作规则、文风约束、字数要求整理成 rules,没有则空数组;\n"
    "4. relationships 体现人物间的师徒/亲属/恩怨/盟友等,没有则空数组;\n"
    "5. 对话中的伏笔、悬念、埋线清单整理成 foreshadows(已回收的标 recalled),没有则空数组;\n"
    "6. 对话信息不足时按该题材的常规套路合理补全;outline 必须体现完整故事线;\n"
    "7. 标签用常见网文分类词(如 玄幻、都市、悬疑、逆袭、无敌流);\n"
    "8. volumes 必须落地完整卷章目录(不是只写散文大纲):至少 1 卷;"
    "若对话里已有分章/卷纲/章纲清单,优先原样整理进 volumes,尽量不丢章;"
    "若对话只有粗纲,补全可写的分章目录,合计约 12-30 章,每章 outline 写清冲突与章末钩子;"
    "禁止 volumes 为空数组。"
)


def _catalog_text_from_apply_data(data: dict, outline: str) -> str:
    """优先用 volumes JSON;否则尝试从散文大纲里解析「第N章」行。"""
    volumes = data.get("volumes")
    if isinstance(volumes, list) and volumes:
        return json.dumps({"volumes": volumes}, ensure_ascii=False)
    chapters = data.get("chapters")
    if isinstance(chapters, list) and chapters:
        return json.dumps(
            {"volumes": [{"title": "第一卷", "outline": "", "chapters": chapters}]},
            ensure_ascii=False,
        )
    return outline or ""


@router.post("/sessions/{session_id}/apply-to-novel")
def apply_to_novel(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """把对话中聊定的选题整理成书:建书 + 大纲文档 + 卷章目录 + 世界观 + 规则 + 人物卡 + 关系 + 伏笔 + 标签。"""
    from ..routers.chapters import materialize_catalog

    session = _get_session(db, session_id, user)
    if session.novel_id:
        raise HTTPException(400, "该书内对话已绑定作品,无需再「应用到书架」")
    msgs = db.query(ChatMessage).filter_by(session_id=session.id).order_by(ChatMessage.id).all()
    if len([m for m in msgs if m.role == "user"]) < 1:
        raise HTTPException(400, "对话还没有实质内容,先聊出选题再应用")

    transcript_parts = []
    if session.compressed_summary:
        transcript_parts.append(f"压缩记忆:{session.compressed_summary[:2000]}")
    # 多留助手长文(章纲常在后半段)
    for m in msgs[-60:]:
        cap = 4000 if m.role == "assistant" else 2000
        transcript_parts.append(f"{'用户' if m.role == 'user' else 'AI'}:{m.content[:cap]}")
    transcript = "\n".join(transcript_parts)
    data = extract_json_object(db, user.id, _APPLY_PROMPT, f"## 对话记录\n{transcript}")

    title = str(data.get("title") or "").strip() or "未命名新书"
    summary = str(data.get("summary") or "").strip() or None
    if db.query(Novel).filter_by(owner_id=user.id, title=title).first():
        title = f"{title}(新)"

    novel = Novel(owner_id=user.id, title=title[:128], summary=summary)
    db.add(novel)
    db.flush()

    # 把自由聊会话绑定到新书(一书一对话)
    session.novel_id = novel.id
    session.title = f"《{novel.title}》创作对话"[:64]

    tag_names = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()][:4]
    attached = []
    for tname in tag_names:
        tag = db.query(Tag).filter_by(user_id=user.id, name=tname).first()
        if tag is None:
            tag = Tag(user_id=user.id, name=tname[:32], type="genre")
            db.add(tag)
            db.flush()
        if tag not in novel.tags:
            novel.tags.append(tag)
            attached.append(tag.name)

    outline = str(data.get("outline") or "").strip()
    if outline:
        db.add(OutlineDoc(novel_id=novel.id, title=f"{title}·选题大纲(来自对话)", content=outline[:20000]))

    valid_roles = ("protagonist", "support", "villain", "minor")
    name_to_id: dict[str, int] = {}
    char_created = 0
    for item in data.get("characters") or []:
        name = str(item.get("name") or "").strip()
        if not name or name in name_to_id:
            continue
        role = item.get("role") if item.get("role") in valid_roles else "support"
        row = Character(
            novel_id=novel.id,
            name=name[:64],
            role=role,
            personality=(str(item.get("personality") or "").strip() or None),
            goal=(str(item.get("goal") or "").strip() or None),
            notes=(str(item.get("notes") or "").strip() or None),
        )
        db.add(row)
        db.flush()
        name_to_id[name] = row.id
        char_created += 1

    world_created = 0
    for item in data.get("world_entries") or []:
        wtitle = str(item.get("title") or "").strip()
        if not wtitle:
            continue
        category = item.get("category") if item.get("category") in WORLD_CATEGORIES else "other"
        db.add(
            WorldEntry(
                novel_id=novel.id,
                category=category,
                title=wtitle[:128],
                content=(str(item.get("content") or "").strip() or None),
            )
        )
        world_created += 1

    rules_created = 0
    for item in data.get("rules") or []:
        content = str(item or "").strip()
        if not content:
            continue
        db.add(NovelRule(novel_id=novel.id, content=content[:500], priority=5, enabled=True))
        rules_created += 1

    rel_created = 0
    for item in data.get("relationships") or []:
        fr, to = str(item.get("from") or "").strip(), str(item.get("to") or "").strip()
        if not fr or not to or fr == to:
            continue
        rel_type = item.get("type") if item.get("type") in RELATIONSHIP_TYPES else "other"
        try:
            intimacy = max(-100, min(100, int(item.get("intimacy", 0))))
        except (TypeError, ValueError):
            intimacy = 0
        for pname in (fr, to):
            if pname not in name_to_id:
                row = Character(novel_id=novel.id, name=pname[:64], role="support")
                db.add(row)
                db.flush()
                name_to_id[pname] = row.id
        db.add(
            CharacterRelationship(
                novel_id=novel.id,
                from_character_id=name_to_id[fr],
                to_character_id=name_to_id[to],
                type=rel_type,
                intimacy=intimacy,
                note=(str(item.get("note") or "").strip() or None),
            )
        )
        rel_created += 1

    foreshadow_created = 0
    for item in data.get("foreshadows") or []:
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        status = item.get("status") if item.get("status") in ("planted", "recalled", "overdue") else "planted"
        note = str(item.get("note") or "").strip()
        db.add(
            Foreshadow(
                novel_id=novel.id,
                description=(f"{description}({note})" if note else description)[:500],
                status=status,
            )
        )
        foreshadow_created += 1

    # 卷章目录:与大纲页「套用为章节」同一路径
    catalog_text = _catalog_text_from_apply_data(data, outline)
    chapters_created = 0
    volumes_created = 0
    catalog_message = None
    if catalog_text.strip():
        cat = materialize_catalog(db, novel.id, catalog_text, commit=False)
        chapters_created = int(cat.get("created") or 0)
        volumes_created = int(cat.get("volumes_created") or 0)
        catalog_message = cat.get("message")

    db.commit()
    db.refresh(novel)
    return {
        "novel_id": novel.id,
        "title": novel.title,
        "session_id": session.id,
        "tags": attached,
        "outline_created": bool(outline),
        "volumes_created": volumes_created,
        "chapters_created": chapters_created,
        "catalog_message": catalog_message,
        "characters_created": char_created,
        "world_created": world_created,
        "rules_created": rules_created,
        "relationships_created": rel_created,
        "foreshadows_created": foreshadow_created,
    }
