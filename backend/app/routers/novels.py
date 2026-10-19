"""书架:标签 + 小说 CRUD + TXT 导入。"""

import asyncio

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..ai.novel_import import (
    IMPORT_EXTENSIONS,
    MIN_CHAPTER_CHARS,
    ai_enrich_import,
    decode_txt_bytes,
    ensure_unique_titles,
    parse_novel_txt,
    sanitize_parsed_novel,
)
from ..database import get_db
from ..models import (
    AnalysisReport,
    Chapter,
    ChapterStoryboard,
    ChapterStoryboardFrame,
    ChapterVersion,
    Character,
    CharacterRelationship,
    ChatMessage,
    ChatSession,
    Foreshadow,
    Novel,
    NovelCollaborator,
    NovelKbLink,
    NovelPlatform,
    NovelReaderStats,
    NovelRule,
    NovelScene,
    NovelSkillLink,
    NovelTag,
    OutlineDoc,
    PUBLISH_PLATFORMS,
    PublishTask,
    ReaderFavorite,
    ReaderHistory,
    ReaderLike,
    ReaderProgress,
    ReadingProgress,
    ResourceMount,
    ReviewRecord,
    ReviewableFilter,
    RollingSummary,
    Tag,
    UsageLog,
    User,
    Volume,
    WorldEntry,
)
from ..schemas import CoverGenerateIn, NovelIn, NovelOut, NovelUpdateIn, TagIn, TagOut, TagUpdateIn
from ..security import get_current_user

router = APIRouter(prefix="/novels", tags=["novels"])
tags_router = APIRouter(prefix="/tags", tags=["tags"])

NOVEL_STATUSES = {"draft": "构思中", "serializing": "连载中", "completed": "已完本"}
MAX_IMPORT_BYTES = 25 * 1024 * 1024
MAX_IMPORT_CHAPTERS = 3000


def get_owned_novel(novel_id: int, user: User, db: Session) -> Novel:
    """书的访问入口:所有者或协作者(editor/reviewer)均可。"""
    novel = db.query(Novel).filter_by(id=novel_id).first()
    if novel is None:
        raise HTTPException(404, "小说不存在")
    if novel.owner_id == user.id:
        return novel
    collab = db.query(NovelCollaborator).filter_by(novel_id=novel_id, user_id=user.id).first()
    if collab is None:
        raise HTTPException(404, "小说不存在")
    return novel


def require_owner(novel: Novel, user: User):
    if novel.owner_id != user.id:
        raise HTTPException(403, "只有书的所有者可以执行此操作")


def novel_role(novel: Novel, user_id: int, db: Session) -> str:
    if novel.owner_id == user_id:
        return "owner"
    collab = db.query(NovelCollaborator).filter_by(novel_id=novel.id, user_id=user_id).first()
    return collab.role if collab else "none"


def recompute_words(db: Session, novel: Novel):
    total = (
        db.query(func.coalesce(func.sum(Chapter.word_count), 0))
        .filter(Chapter.novel_id == novel.id, Chapter.status.in_(ReviewableFilter))
        .scalar()
    )
    novel.current_words = int(total or 0)


def set_novel_platforms(db: Session, novel: Novel, platforms: list[str]):
    """替换书的发布平台标记;保留已有平台的发布进度。"""
    wanted = [p for p in dict.fromkeys(platforms) if p in PUBLISH_PLATFORMS]
    existing = {lp.platform: lp for lp in novel.platforms}
    for platform, link in existing.items():
        if platform not in wanted:
            db.delete(link)
    for platform in wanted:
        if platform not in existing:
            db.add(NovelPlatform(novel_id=novel.id, platform=platform))
    db.flush()


# ---------- 标签 ----------
@tags_router.get("", response_model=list[TagOut])
def list_tags(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Tag).filter_by(user_id=user.id).order_by(Tag.type, Tag.id).all()


@tags_router.post("", response_model=TagOut)
def create_tag(body: TagIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    if body.type not in ("genre", "style", "status", "custom"):
        raise HTTPException(400, "标签类型无效")
    if db.query(Tag).filter_by(user_id=user.id, name=name).first():
        raise HTTPException(400, "标签已存在")
    tag = Tag(user_id=user.id, name=name[:32], type=body.type, color=body.color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@tags_router.put("/{tag_id}", response_model=TagOut)
def update_tag(
    tag_id: int,
    body: TagUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tag = db.query(Tag).filter_by(id=tag_id, user_id=user.id).first()
    if tag is None:
        raise HTTPException(404, "标签不存在")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "标签名不能为空")
        dup = db.query(Tag).filter(Tag.user_id == user.id, Tag.name == name, Tag.id != tag_id).first()
        if dup:
            raise HTTPException(400, "标签已存在")
        tag.name = name[:32]
    if body.type is not None:
        if body.type not in ("genre", "style", "status", "custom"):
            raise HTTPException(400, "标签类型无效")
        tag.type = body.type
    if body.color is not None:
        tag.color = body.color or None
    db.commit()
    db.refresh(tag)
    return tag


@tags_router.delete("/{tag_id}")
def delete_tag(tag_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tag = db.query(Tag).filter_by(id=tag_id, user_id=user.id).first()
    if tag is None:
        raise HTTPException(404, "标签不存在")
    db.delete(tag)
    db.commit()
    return {"ok": True}


# ---------- 小说 ----------
@router.get("", response_model=list[NovelOut])
def bookshelf(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """所有者 + 被共享的书。"""
    shared_ids = [
        row.novel_id
        for row in db.query(NovelCollaborator).filter_by(user_id=user.id).all()
    ]
    novels = (
        db.query(Novel)
        .filter(or_(Novel.owner_id == user.id, Novel.id.in_(shared_ids or [0])))
        .order_by(Novel.updated_at.desc())
        .all()
    )
    # 自愈:审核通过后若漏算 current_words,打开书架时按已过审章重算
    dirty = False
    for n in novels:
        before = n.current_words
        recompute_words(db, n)
        if n.current_words != before:
            dirty = True
    if dirty:
        db.commit()
    out = []
    for n in novels:
        item = NovelOut.model_validate(n)
        item.my_role = novel_role(n, user.id, db)
        out.append(item)
    return out


@router.post("", response_model=NovelOut)
def create_novel(body: NovelIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    novel = Novel(
        owner_id=user.id,
        title=body.title,
        summary=body.summary,
        cover=body.cover,
        status=body.status if body.status in NOVEL_STATUSES else "draft",
        target_words=body.target_words,
    )
    for tid in body.tag_ids:
        tag = db.query(Tag).filter_by(id=tid, user_id=user.id).first()
        if tag:
            novel.tags.append(tag)
    db.add(novel)
    db.flush()
    set_novel_platforms(db, novel, body.platforms)
    db.commit()
    db.refresh(novel)
    return novel


@router.post("/import-txt")
async def import_novel_txt(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    use_ai: str = Form("true"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传 TXT/Markdown 小说:规则拆卷章 + AI 补书名/简介,批量录入正文。"""
    filename = file.filename or "novel.txt"
    lower = filename.lower()
    if not lower.endswith(IMPORT_EXTENSIONS):
        raise HTTPException(400, "仅支持 .txt / .md 文本文件")
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(400, f"文件过大(上限 {MAX_IMPORT_BYTES // (1024 * 1024)}MB)")

    text = await asyncio.to_thread(decode_txt_bytes, data)
    # 拆分+并行清洗+按序合并(O(n),放到线程池避免卡住事件循环)
    parsed = await asyncio.to_thread(parse_novel_txt, text, filename)

    override = (title or "").strip()
    if override:
        parsed.title = override[:128]

    # 大文件/多章节一律跳过 AI,避免卡住
    ai_on = str(use_ai).strip().lower() in ("1", "true", "yes", "on")
    ch_preview = sum(len(v.chapters) for v in parsed.volumes)
    if ai_on and ch_preview <= 40 and len(text) < 800_000:
        try:
            parsed = ai_enrich_import(db, user.id, parsed, text)
            if override:
                parsed.title = override[:128]
        except Exception:
            pass

    # 同名避让
    base_title = parsed.title
    n = 2
    while db.query(Novel).filter_by(owner_id=user.id, title=parsed.title).first():
        parsed.title = f"{base_title}({n})"[:128]
        n += 1

    parsed = sanitize_parsed_novel(parsed, already_cleaned=True)
    ensure_unique_titles(parsed.volumes)
    total_chapters = sum(len(v.chapters) for v in parsed.volumes)
    if total_chapters == 0:
        raise HTTPException(400, f"未能解析出有效章节(正文需至少 {MIN_CHAPTER_CHARS} 字,且已去掉重复/作者说)")
    truncated = False
    if total_chapters > MAX_IMPORT_CHAPTERS:
        truncated = True
        kept = 0
        for v in parsed.volumes:
            remain = MAX_IMPORT_CHAPTERS - kept
            if remain <= 0:
                v.chapters = []
                continue
            if len(v.chapters) > remain:
                v.chapters = v.chapters[:remain]
            kept += len(v.chapters)
        parsed.volumes = [v for v in parsed.volumes if v.chapters]

    novel = Novel(
        owner_id=user.id,
        title=parsed.title,
        summary=parsed.summary,
        status="serializing",
    )
    db.add(novel)
    db.flush()

    # 批量写库:卷 → flush → 章 → flush → 版本 → flush → 回填 current_version
    vol_rows: list[Volume] = []
    for vol_sort, v in enumerate(parsed.volumes, start=1):
        vol = Volume(novel_id=novel.id, title=v.title[:128], outline=v.outline, sort=vol_sort)
        db.add(vol)
        vol_rows.append(vol)
    db.flush()

    chapter_rows: list[tuple[Chapter, str]] = []
    ch_sort = 0
    for vol, v in zip(vol_rows, parsed.volumes):
        for ch in v.chapters:
            content = (ch.content or "").strip()
            # 硬闸:绝不写入短章
            if len(content) < MIN_CHAPTER_CHARS:
                continue
            ch_sort += 1
            chapter = Chapter(
                novel_id=novel.id,
                volume_id=vol.id,
                title=ch.title[:128],
                outline=ch.outline,
                sort=ch_sort,
                status="approved",
                word_count=len(content),
            )
            db.add(chapter)
            chapter_rows.append((chapter, content))
    db.flush()

    if not chapter_rows:
        db.rollback()
        raise HTTPException(400, f"未能解析出有效章节(正文需至少 {MIN_CHAPTER_CHARS} 字)")

    version_rows: list[tuple[Chapter, ChapterVersion]] = []
    for chapter, content in chapter_rows:
        version = ChapterVersion(
            chapter_id=chapter.id,
            kind="manual",
            content=content,
            word_count=len(content),
            created_by_id=user.id,
        )
        db.add(version)
        version_rows.append((chapter, version))
    db.flush()

    for chapter, version in version_rows:
        chapter.current_version_id = version.id

    created_volumes = len(vol_rows)
    created_chapters = len(chapter_rows)

    recompute_words(db, novel)
    db.commit()
    db.refresh(novel)

    return {
        "ok": True,
        "novel_id": novel.id,
        "title": novel.title,
        "summary": novel.summary,
        "volumes_created": created_volumes,
        "chapters_created": created_chapters,
        "word_count": novel.current_words,
        "source": parsed.source,
        "truncated": truncated,
        "message": (
            f"已导入前 {MAX_IMPORT_CHAPTERS} 章,后续可分段再导。"
            if truncated
            else f"已解析并录入 {created_volumes} 卷 / {created_chapters} 章"
        ),
    }


@router.get("/{novel_id}", response_model=NovelOut)
def get_novel(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    novel = get_owned_novel(novel_id, user, db)
    item = NovelOut.model_validate(novel)
    item.my_role = novel_role(novel, user.id, db)
    return item


@router.put("/{novel_id}", response_model=NovelOut)
def update_novel(
    novel_id: int,
    body: NovelUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    novel = get_owned_novel(novel_id, user, db)
    data = body.model_dump(exclude_unset=True)
    for field in ("title", "summary", "cover", "visual_style_prompt", "target_words"):
        if field in data:
            setattr(novel, field, data[field])
    if "status" in data:
        if data["status"] not in NOVEL_STATUSES:
            raise HTTPException(400, "非法状态")
        novel.status = data["status"]
    if body.tag_ids is not None:
        novel.tags = [
            t for t in db.query(Tag).filter(Tag.user_id == user.id, Tag.id.in_(body.tag_ids)).all()
        ]
    if body.platforms is not None:
        set_novel_platforms(db, novel, body.platforms)
    db.commit()
    db.refresh(novel)
    return novel


def _novel_out(novel: Novel, user_id: int, db: Session) -> NovelOut:
    item = NovelOut.model_validate(novel)
    item.my_role = novel_role(novel, user_id, db)
    return item


def _default_cover_keywords(db: Session, novel: Novel) -> str:
    parts: list[str] = []
    for t in novel.tags or []:
        if t.name and t.name not in parts:
            parts.append(t.name)
    worlds = (
        db.query(WorldEntry)
        .filter_by(novel_id=novel.id)
        .order_by(WorldEntry.sort.asc(), WorldEntry.id.asc())
        .limit(8)
        .all()
    )
    for w in worlds:
        title = (w.title or "").strip()
        if title and title not in parts:
            parts.append(title)
    return "、".join(parts[:12])


def build_cover_prompt(novel: Novel, keywords: str, extra: str | None = None) -> str:
    style = (novel.visual_style_prompt or "").strip() or (
        "高质量商业网文封面,电影感光影,竖构图,书名可读区域留白,无水印无logo无二维码"
    )
    summary = (novel.summary or "").strip()[:320]
    kw = (keywords or "").strip() or novel.title
    extra_line = (extra or "").strip()
    lines = [
        f"为小说《{novel.title}》绘制一张竖版封面。",
        f"关键词:{kw}",
    ]
    if summary:
        lines.append(f"故事气质:{summary}")
    if extra_line:
        lines.append(f"画面补充:{extra_line}")
    lines.append(f"画风约束:{style}")
    lines.append(
        "要求:单幅完整封面插画,主体清晰,氛围贴合关键词,不要大段文字堆砌,"
        "最多可有艺术化书名题字;不要边框、不要拼贴海报模板。"
    )
    return "\n".join(lines)


@router.post("/{novel_id}/cover/upload", response_model=NovelOut)
async def upload_cover(
    novel_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传封面图,写入 novel.cover。"""
    from ..ai.media_gen import save_bytes

    novel = get_owned_novel(novel_id, user, db)
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "封面图超过 8MB")
    name = (file.filename or "cover.png").lower()
    ext = "png"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        ext = "jpg"
    elif name.endswith(".webp"):
        ext = "webp"
    elif name.endswith(".gif"):
        ext = "gif"
    url = save_bytes(user.id, data, ext)
    novel.cover = url
    db.commit()
    db.refresh(novel)
    return _novel_out(novel, user.id, db)


@router.post("/{novel_id}/cover/generate", response_model=NovelOut)
async def generate_cover(
    novel_id: int,
    body: CoverGenerateIn = Body(default_factory=CoverGenerateIn),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按书名/简介/标签/世界观关键词 AI 生成封面。"""
    from ..ai.media_gen import find_modality_model, generate_image, resolve_modality_route

    novel = get_owned_novel(novel_id, user, db)
    keywords = (body.keywords or "").strip() or _default_cover_keywords(db, novel)
    prompt = build_cover_prompt(novel, keywords, body.extra)

    found = find_modality_model(db, user.id, "image")
    if found:
        target = resolve_modality_route(
            db, user.id, "novel.visual", "image", provider_id=found[0], model_id=found[1]
        )
        # 竖版封面常用 1024x1792;不支持时 generate_image 内部会回退
        try:
            urls = await generate_image(target, prompt, user.id, size="1024x1792", n=1)
        except Exception:
            urls = await generate_image(target, prompt, user.id, size="1024x1024", n=1)
        url = urls[0] if urls else None
    else:
        from ..ai.local_image import render_local_card

        url = render_local_card(user.id, novel.title, context=f"{keywords}\n{(novel.summary or '')[:160]}")

    if not url:
        raise HTTPException(502, "封面生成失败,未返回地址。请在个人中心配置可用的图片模型后重试。")
    novel.cover = url
    db.commit()
    db.refresh(novel)
    return _novel_out(novel, user.id, db)


@router.delete("/{novel_id}/cover", response_model=NovelOut)
def clear_cover(
    novel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    novel = get_owned_novel(novel_id, user, db)
    novel.cover = None
    db.commit()
    db.refresh(novel)
    return _novel_out(novel, user.id, db)


def _purge_novel_data(db: Session, novel_id: int) -> dict[str, int]:
    """物理清空一部小说的全部关联数据(SQLite 未开 FK 时也能清干净)。"""
    counts: dict[str, int] = {}

    def _del(label: str, q) -> None:
        n = q.delete(synchronize_session=False)
        counts[label] = int(n or 0)

    chapter_ids = [cid for (cid,) in db.query(Chapter.id).filter_by(novel_id=novel_id).all()]
    session_ids = [sid for (sid,) in db.query(ChatSession.id).filter_by(novel_id=novel_id).all()]

    # 先解章节 ↔ 版本环引用,再删版本与审核
    if chapter_ids:
        db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).update(
            {Chapter.current_version_id: None}, synchronize_session=False
        )
        board_ids = [
            bid
            for (bid,) in db.query(ChapterStoryboard.id)
            .filter(ChapterStoryboard.chapter_id.in_(chapter_ids))
            .all()
        ]
        if board_ids:
            _del(
                "storyboard_frames",
                db.query(ChapterStoryboardFrame).filter(ChapterStoryboardFrame.storyboard_id.in_(board_ids)),
            )
            _del(
                "storyboards",
                db.query(ChapterStoryboard).filter(ChapterStoryboard.id.in_(board_ids)),
            )
        _del("review_records", db.query(ReviewRecord).filter(ReviewRecord.chapter_id.in_(chapter_ids)))
        _del("analysis_reports", db.query(AnalysisReport).filter(AnalysisReport.chapter_id.in_(chapter_ids)))
        _del("chapter_versions", db.query(ChapterVersion).filter(ChapterVersion.chapter_id.in_(chapter_ids)))
        _del("publish_tasks", db.query(PublishTask).filter(PublishTask.chapter_id.in_(chapter_ids)))

    if session_ids:
        _del("chat_messages", db.query(ChatMessage).filter(ChatMessage.session_id.in_(session_ids)))

    _del("chat_sessions", db.query(ChatSession).filter_by(novel_id=novel_id))
    _del("foreshadows", db.query(Foreshadow).filter_by(novel_id=novel_id))
    _del("chapters", db.query(Chapter).filter_by(novel_id=novel_id))
    _del("volumes", db.query(Volume).filter_by(novel_id=novel_id))
    _del("outline_docs", db.query(OutlineDoc).filter_by(novel_id=novel_id))
    _del("characters_rels", db.query(CharacterRelationship).filter_by(novel_id=novel_id))
    _del("characters", db.query(Character).filter_by(novel_id=novel_id))
    _del("novel_scenes", db.query(NovelScene).filter_by(novel_id=novel_id))
    _del("world_entries", db.query(WorldEntry).filter_by(novel_id=novel_id))
    _del("rules", db.query(NovelRule).filter_by(novel_id=novel_id))
    _del("rolling_summary", db.query(RollingSummary).filter_by(novel_id=novel_id))
    _del("reading_progress", db.query(ReadingProgress).filter_by(novel_id=novel_id))
    _del("platforms", db.query(NovelPlatform).filter_by(novel_id=novel_id))
    _del("publish_tasks_by_novel", db.query(PublishTask).filter_by(novel_id=novel_id))
    _del("kb_links", db.query(NovelKbLink).filter_by(novel_id=novel_id))
    _del("skill_links", db.query(NovelSkillLink).filter_by(novel_id=novel_id))
    _del("collaborators", db.query(NovelCollaborator).filter_by(novel_id=novel_id))
    _del("tags", db.query(NovelTag).filter_by(novel_id=novel_id))
    _del("reader_progress", db.query(ReaderProgress).filter_by(novel_id=novel_id))
    _del("reader_history", db.query(ReaderHistory).filter_by(novel_id=novel_id))
    _del("reader_favorites", db.query(ReaderFavorite).filter_by(novel_id=novel_id))
    _del("reader_likes", db.query(ReaderLike).filter_by(novel_id=novel_id))
    _del("reader_stats", db.query(NovelReaderStats).filter_by(novel_id=novel_id))
    _del(
        "resource_mounts",
        db.query(ResourceMount).filter_by(workspace_type="novel", workspace_id=novel_id),
    )
    # 用量日志保留统计,仅断开小说关联
    db.query(UsageLog).filter_by(novel_id=novel_id).update({"novel_id": None}, synchronize_session=False)
    return counts


@router.delete("/{novel_id}")
def delete_novel(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除小说并清空其全部内容数据(章节/设定/对话/发布任务等)。"""
    novel = get_owned_novel(novel_id, user, db)
    require_owner(novel, user)
    title = novel.title
    nid = novel.id
    purged = _purge_novel_data(db, nid)
    # 关联已手删干净;勿再用 ORM cascade(会二次删 novel_tags 触发 StaleDataError)
    db.expunge(novel)
    db.query(Novel).filter_by(id=nid).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "title": title, "purged": purged}


@router.get("/{novel_id}/stats")
def novel_stats(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    novel = get_owned_novel(novel_id, user, db)
    rows = (
        db.query(Chapter.status, func.count(Chapter.id), func.coalesce(func.sum(Chapter.word_count), 0))
        .filter(Chapter.novel_id == novel.id)
        .group_by(Chapter.status)
        .all()
    )
    by_status = {status: {"count": count, "words": int(words)} for status, count, words in rows}
    return {
        "novel_id": novel.id,
        "title": novel.title,
        "target_words": novel.target_words,
        "published_words": novel.current_words,
        "total_chapters": sum(v["count"] for v in by_status.values()),
        "by_status": by_status,
    }
