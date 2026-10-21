"""书架:标签 + 小说 CRUD + TXT 导入。"""

import asyncio
import io
import re
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..ai.novel_import import (
    IMPORT_EXTENSIONS,
    MIN_CHAPTER_CHARS,
    ParsedChapter,
    _chapter_order_key,
    ai_enrich_import,
    decode_txt_bytes,
    ensure_unique_titles,
    maybe_ai_split_parsed,
    parse_novel_txt,
    sanitize_parsed_novel,
    title_from_batch_novel_filename,
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
from ..schemas import (
    CoverBatchGenerateIn,
    CoverBatchGenerateOut,
    CoverBatchItemOut,
    CoverGenerateIn,
    NovelIn,
    NovelOut,
    NovelUpdateIn,
    TagIn,
    TagOut,
    TagUpdateIn,
)
from ..security import get_current_user

router = APIRouter(prefix="/novels", tags=["novels"])
tags_router = APIRouter(prefix="/tags", tags=["tags"])

NOVEL_STATUSES = {"draft": "构思中", "serializing": "连载中", "completed": "已完本"}
MAX_IMPORT_BYTES = 25 * 1024 * 1024
MAX_IMPORT_CHAPTERS = 3000
MAX_BATCH_CHAPTER_FILES = 500
MAX_BATCH_CHAPTER_FILE_BYTES = 2 * 1024 * 1024
MAX_BATCH_NOVEL_ZIP_BYTES = 200 * 1024 * 1024

# 文件名序号前缀:0001_第一章 / 12.标题 / 003-序章
_FILENAME_ORDER_PREFIX = re.compile(
    r"^(?P<num>\d{1,6})(?:\s*[_\-.\s]\s*|\s+)(?P<title>.+)$"
)


def _stem_from_chapter_filename(filename: str) -> str:
    """去掉路径与扩展名后的纯文件名。"""
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    lower = name.lower()
    for ext in IMPORT_EXTENSIONS:
        if lower.endswith(ext):
            name = name[: -len(ext)]
            break
    return name.strip()


def _title_from_chapter_filename(filename: str) -> str:
    """从上传文件名取章名:去掉路径、扩展名与可选数字序号前缀。"""
    name = _stem_from_chapter_filename(filename)
    m = _FILENAME_ORDER_PREFIX.match(name)
    if m:
        name = (m.group("title") or "").strip()
    return name[:128] or "未命名章节"


def _batch_chapter_sort_key(filename: str, title: str, content: str, idx: int) -> tuple:
    """批量导入排序:优先文件名数字前缀(0001_),再中文章序,最后上传序号。"""
    stem = _stem_from_chapter_filename(filename)
    m = _FILENAME_ORDER_PREFIX.match(stem)
    if m:
        try:
            return (0, int(m.group("num")), idx)
        except ValueError:
            pass
    # 无数字前缀时用章名规则(第N章/话),再回退上传顺序
    ch_key = _chapter_order_key(ParsedChapter(title=title or stem, content=content, pos=idx))
    return (1, *ch_key)


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


async def _import_one_novel_from_bytes(
    db: Session,
    user: User,
    *,
    data: bytes,
    filename: str,
    title_override: str | None = None,
    use_ai: bool = True,
    prefer_filename_title: bool = False,
) -> dict:
    """将单本 TXT/MD 字节解析并入库。成功返回结果 dict;失败抛 HTTPException。"""
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(400, f"文件过大(上限 {MAX_IMPORT_BYTES // (1024 * 1024)}MB)")

    text = await asyncio.to_thread(decode_txt_bytes, data)
    parsed = await asyncio.to_thread(parse_novel_txt, text, filename)

    override = (title_override or "").strip()
    if prefer_filename_title and not override:
        override = title_from_batch_novel_filename(filename)
    if override:
        parsed.title = override[:128]

    ch_preview = sum(len(v.chapters) for v in parsed.volumes)
    if use_ai and ch_preview <= 40 and len(text) < 800_000:
        try:
            parsed = ai_enrich_import(db, user.id, parsed, text)
            if override:
                parsed.title = override[:128]
        except Exception:
            pass

    # 规则仍只有一章时,强制走大模型分章(与是否开启「AI 补书名」无关)
    ch_preview = sum(len(v.chapters) for v in parsed.volumes)
    if ch_preview <= 1 and len(text) >= MIN_CHAPTER_CHARS * 4:
        try:
            parsed = maybe_ai_split_parsed(db, user.id, parsed, text)
            if override:
                parsed.title = override[:128]
        except Exception:
            pass

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
    ai_on = str(use_ai).strip().lower() in ("1", "true", "yes", "on")
    return await _import_one_novel_from_bytes(
        db,
        user,
        data=data,
        filename=filename,
        title_override=title,
        use_ai=ai_on,
        prefer_filename_title=False,
    )


def _iter_zip_novel_files(data: bytes) -> list[tuple[str, bytes]]:
    """从 zip 取出 txt/md;(显示名, 内容)。跳过目录与 macOS 垃圾项。"""
    out: list[tuple[str, bytes]] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise HTTPException(400, f"无法解析压缩包: {e}") from e
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            path = info.filename.replace("\\", "/")
            base = path.split("/")[-1]
            if not base or base.startswith("."):
                continue
            if "__MACOSX" in path.split("/"):
                continue
            lower = base.lower()
            if not lower.endswith(IMPORT_EXTENSIONS):
                continue
            if info.file_size > MAX_IMPORT_BYTES:
                continue
            raw = zf.read(info)
            if not raw:
                continue
            out.append((base, raw))
    return out


@router.post("/import-txt-batch")
async def import_novels_batch(
    files: list[UploadFile] = File(...),
    use_ai: str = Form("false"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """批量多书导入:每个 txt/md 一本;也可上传一个 zip(包内每个 txt 一本)。

    书名取自文件名,自动去掉开头编号与 [标签]/例
    ``001.[红袖vip完结]日租:….txt`` → ``日租:…``。
    """
    if not files:
        raise HTTPException(400, "请选择文件夹内的 txt,或多个文件,或一个 zip")

    ai_on = str(use_ai).strip().lower() in ("1", "true", "yes", "on")
    # (filename, bytes)
    pending: list[tuple[str, bytes]] = []
    skipped: list[dict] = []

    for upload in files:
        filename = upload.filename or "novel.txt"
        lower = filename.lower().replace("\\", "/")
        base = lower.split("/")[-1]
        raw = await upload.read()
        if not raw:
            skipped.append({"file": filename, "reason": "文件为空"})
            continue

        if base.endswith(".zip") or lower.endswith(".zip"):
            if len(raw) > MAX_BATCH_NOVEL_ZIP_BYTES:
                skipped.append(
                    {
                        "file": filename,
                        "reason": f"压缩包超过 {MAX_BATCH_NOVEL_ZIP_BYTES // (1024 * 1024)}MB",
                    }
                )
                continue
            try:
                extracted = await asyncio.to_thread(_iter_zip_novel_files, raw)
            except HTTPException as e:
                skipped.append({"file": filename, "reason": str(e.detail)})
                continue
            if not extracted:
                skipped.append({"file": filename, "reason": "压缩包内无 txt/md"})
                continue
            pending.extend(extracted)
            continue

        if not any(base.endswith(ext) for ext in IMPORT_EXTENSIONS):
            skipped.append({"file": filename, "reason": "仅支持 .txt / .md / .zip"})
            continue
        if len(raw) > MAX_IMPORT_BYTES:
            skipped.append(
                {
                    "file": filename,
                    "reason": f"单文件超过 {MAX_IMPORT_BYTES // (1024 * 1024)}MB",
                }
            )
            continue
        # 保留原始 basename(含中文),用于解析书名
        display = (filename or "novel.txt").replace("\\", "/").split("/")[-1]
        pending.append((display, raw))

    if not pending:
        raise HTTPException(
            400,
            "没有可导入的小说文件"
            + (f"({len(skipped)} 项被跳过)" if skipped else ""),
        )

    # 按文件名排序,编号靠前的先导入
    pending.sort(key=lambda item: item[0].lower())

    created: list[dict] = []
    failed: list[dict] = []
    for fname, data in pending:
        try:
            # 每本独立提交;失败不影响已成功的
            result = await _import_one_novel_from_bytes(
                db,
                user,
                data=data,
                filename=fname,
                title_override=None,
                use_ai=ai_on,
                prefer_filename_title=True,
            )
            created.append(
                {
                    "file": fname,
                    "novel_id": result["novel_id"],
                    "title": result["title"],
                    "chapters_created": result["chapters_created"],
                    "word_count": result["word_count"],
                }
            )
        except HTTPException as e:
            failed.append({"file": fname, "reason": str(e.detail)})
            db.rollback()
        except Exception as e:
            failed.append({"file": fname, "reason": str(e)})
            db.rollback()

    return {
        "ok": True,
        "created_count": len(created),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "created": created,
        "failed": failed,
        "skipped": skipped,
        "message": (
            f"成功导入 {len(created)} 本"
            + (f",失败 {len(failed)} 本" if failed else "")
            + (f",跳过 {len(skipped)} 项" if skipped else "")
        ),
    }


@router.post("/cover/generate-batch", response_model=CoverBatchGenerateOut)
async def generate_covers_batch(
    body: CoverBatchGenerateIn = Body(default_factory=CoverBatchGenerateIn),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """一批书依次 AI 生成封面。默认只补无封面的书;单次最多 40 本。

    须声明在 /{novel_id}/… 之前,避免 path 被当成 novel_id。
    """
    limit = max(1, min(int(body.limit or MAX_COVER_BATCH), MAX_COVER_BATCH))
    q = _accessible_novels_query(db, user.id)
    if body.novel_ids:
        ids = list(dict.fromkeys(int(i) for i in body.novel_ids if i))
        novels = q.filter(Novel.id.in_(ids)).order_by(Novel.id.asc()).all()
        by_id = {n.id: n for n in novels}
        ordered = [by_id[i] for i in ids if i in by_id]
    else:
        ordered = q.order_by(Novel.updated_at.desc()).all()

    targets: list[Novel] = []
    skipped_pre: list[CoverBatchItemOut] = []
    for novel in ordered:
        if body.only_missing and (novel.cover or "").strip():
            skipped_pre.append(
                CoverBatchItemOut(
                    novel_id=novel.id,
                    title=novel.title,
                    status="skipped",
                    cover=novel.cover,
                    error="已有封面",
                )
            )
            continue
        targets.append(novel)
        if len(targets) >= limit:
            break

    items: list[CoverBatchItemOut] = list(skipped_pre)
    ok = failed = 0
    skipped = len(skipped_pre)
    for novel in targets:
        try:
            url = await _generate_cover_url(db, user, novel, extra=body.extra)
            novel.cover = url
            db.commit()
            db.refresh(novel)
            ok += 1
            items.append(
                CoverBatchItemOut(
                    novel_id=novel.id, title=novel.title, status="ok", cover=url
                )
            )
        except HTTPException as e:
            db.rollback()
            failed += 1
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            items.append(
                CoverBatchItemOut(
                    novel_id=novel.id, title=novel.title, status="failed", error=detail
                )
            )
        except Exception as e:
            db.rollback()
            failed += 1
            items.append(
                CoverBatchItemOut(
                    novel_id=novel.id,
                    title=novel.title,
                    status="failed",
                    error=str(e)[:200] or "生成失败",
                )
            )
        await asyncio.sleep(0.35)

    return CoverBatchGenerateOut(
        total=ok + skipped + failed,
        ok=ok,
        skipped=skipped,
        failed=failed,
        items=items,
    )


@router.post("/{novel_id}/import-chapters")
async def import_chapters_batch(
    novel_id: int,
    files: list[UploadFile] = File(...),
    volume_id: int | None = Form(None),
    status: str = Form("approved"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """已有书批量导入章节:每个 txt/md 文件名=章名,文件内容=正文,按章序追加。"""
    novel = get_owned_novel(novel_id, user, db)
    if not files:
        raise HTTPException(400, "请选择至少一个 txt/md 文件")
    if len(files) > MAX_BATCH_CHAPTER_FILES:
        raise HTTPException(400, f"单次最多导入 {MAX_BATCH_CHAPTER_FILES} 个文件")

    chapter_status = (status or "approved").strip().lower()
    if chapter_status not in ("approved", "draft"):
        raise HTTPException(400, "status 仅支持 approved 或 draft")

    target_volume_id: int | None = None
    if volume_id is not None:
        vol = db.query(Volume).filter_by(id=volume_id, novel_id=novel_id).first()
        if vol is None:
            raise HTTPException(400, "目标卷不存在")
        target_volume_id = vol.id

    skipped: list[dict] = []
    # filename, title, content, upload_idx
    pending: list[tuple[str, str, str, int]] = []

    for idx, upload in enumerate(files):
        filename = upload.filename or f"chapter_{idx + 1}.txt"
        lower = filename.lower().replace("\\", "/")
        base_name = lower.split("/")[-1]
        if not any(base_name.endswith(ext) for ext in IMPORT_EXTENSIONS):
            skipped.append({"file": filename, "reason": "仅支持 .txt / .md"})
            continue
        raw = await upload.read()
        if not raw:
            skipped.append({"file": filename, "reason": "文件为空"})
            continue
        if len(raw) > MAX_BATCH_CHAPTER_FILE_BYTES:
            skipped.append({"file": filename, "reason": f"单文件超过 {MAX_BATCH_CHAPTER_FILE_BYTES // (1024 * 1024)}MB"})
            continue
        try:
            text = await asyncio.to_thread(decode_txt_bytes, raw)
        except Exception as e:
            skipped.append({"file": filename, "reason": f"解码失败: {e}"})
            continue
        content = (text or "").strip()
        if len(content) < MIN_CHAPTER_CHARS:
            skipped.append(
                {
                    "file": filename,
                    "reason": f"正文不足 {MIN_CHAPTER_CHARS} 字",
                }
            )
            continue
        title = _title_from_chapter_filename(filename)
        pending.append((filename, title, content, idx))

    if not pending:
        raise HTTPException(
            400,
            "没有可导入的章节"
            + (f"({len(skipped)} 个文件被跳过)" if skipped else ""),
        )

    # 优先按 0001_ 这种文件名序号排序(浏览器上传顺序不可靠)
    pending.sort(key=lambda item: _batch_chapter_sort_key(item[0], item[1], item[2], item[3]))

    max_sort = db.query(func.coalesce(func.max(Chapter.sort), -1)).filter_by(novel_id=novel_id).scalar()
    start_sort = int(max_sort or -1) + 1

    chapter_rows: list[tuple[Chapter, str]] = []
    for i, (_filename, title, content, _) in enumerate(pending):
        chapter = Chapter(
            novel_id=novel.id,
            volume_id=target_volume_id,
            title=title[:128],
            sort=start_sort + i,
            status=chapter_status,
            word_count=len(content),
        )
        db.add(chapter)
        chapter_rows.append((chapter, content))
    db.flush()

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

    recompute_words(db, novel)
    db.commit()

    created = len(chapter_rows)
    return {
        "ok": True,
        "novel_id": novel.id,
        "chapters_created": created,
        "skipped": skipped,
        "skipped_count": len(skipped),
        "word_count": novel.current_words,
        "status": chapter_status,
        "message": (
            f"已导入 {created} 章"
            + (f",跳过 {len(skipped)} 个文件" if skipped else "")
        ),
    }


def _safe_export_stem(title: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (title or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s or "未命名章节")[:80]


@router.get("/{novel_id}/export-chapters")
def export_chapters(
    novel_id: int,
    volume_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导出章节正文为 zip(每章一个 txt,文件名 0001_章名.txt,可再导入)。"""
    novel = get_owned_novel(novel_id, user, db)
    q = db.query(Chapter).filter_by(novel_id=novel_id)
    if volume_id is not None:
        vol = db.query(Volume).filter_by(id=volume_id, novel_id=novel_id).first()
        if vol is None:
            raise HTTPException(400, "目标卷不存在")
        q = q.filter_by(volume_id=volume_id)
    chapters = q.order_by(Chapter.sort, Chapter.id).all()
    if not chapters:
        raise HTTPException(400, "没有可导出的章节")

    ver_ids = [c.current_version_id for c in chapters if c.current_version_id]
    content_map: dict[int, str] = {}
    if ver_ids:
        for row in db.query(ChapterVersion.id, ChapterVersion.content).filter(ChapterVersion.id.in_(ver_ids)).all():
            content_map[int(row[0])] = row[1] or ""

    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, ch in enumerate(chapters, start=1):
            stem = _safe_export_stem(ch.title)
            name = f"{i:04d}_{stem}.txt"
            if name in used_names:
                name = f"{i:04d}_{stem}_{ch.id}.txt"
            used_names.add(name)
            body = ""
            if ch.current_version_id:
                body = content_map.get(int(ch.current_version_id), "")
            # 无 BOM,便于与导入解码一致
            zf.writestr(name, body.encode("utf-8"))

    buf.seek(0)
    zip_name = f"{_safe_export_stem(novel.title)}_chapters.zip"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}",
    }
    return StreamingResponse(buf, media_type="application/zip", headers=headers)


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


MAX_COVER_BATCH = 40


async def _generate_cover_url(
    db: Session,
    user: User,
    novel: Novel,
    *,
    keywords: str | None = None,
    extra: str | None = None,
) -> str:
    """生成并返回封面 URL,不写库。失败抛 HTTPException。"""
    from ..ai.media_gen import find_modality_model, generate_image, resolve_modality_route

    kw = (keywords or "").strip() or _default_cover_keywords(db, novel)
    prompt = build_cover_prompt(novel, kw, extra)

    found = find_modality_model(db, user.id, "image")
    if found:
        target = resolve_modality_route(
            db, user.id, "novel.visual", "image", provider_id=found[0], model_id=found[1]
        )
        try:
            urls = await generate_image(target, prompt, user.id, size="1024x1792", n=1)
        except Exception:
            urls = await generate_image(target, prompt, user.id, size="1024x1024", n=1)
        url = urls[0] if urls else None
    else:
        from ..ai.local_image import render_local_card

        url = render_local_card(user.id, novel.title, context=f"{kw}\n{(novel.summary or '')[:160]}")

    if not url:
        raise HTTPException(502, "封面生成失败,未返回地址。请在个人中心配置可用的图片模型后重试。")
    return url


def _accessible_novels_query(db: Session, user_id: int):
    collab_ids = [
        nid
        for (nid,) in db.query(NovelCollaborator.novel_id).filter_by(user_id=user_id).all()
    ]
    return db.query(Novel).filter(or_(Novel.owner_id == user_id, Novel.id.in_(collab_ids or [-1])))


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
    novel = get_owned_novel(novel_id, user, db)
    url = await _generate_cover_url(
        db, user, novel, keywords=body.keywords, extra=body.extra
    )
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


@router.post("/{novel_id}/resplit-chapters")
async def resplit_novel_chapters(
    novel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对「整本一章」的书重新分章:先规则,失败再大模型。"""
    novel = get_owned_novel(novel_id, user, db)
    chapters = db.query(Chapter).filter_by(novel_id=novel_id).order_by(Chapter.sort, Chapter.id).all()
    if not chapters:
        raise HTTPException(400, "没有章节可重切")
    # 拼回全文
    parts: list[str] = []
    for ch in chapters:
        body = ""
        if ch.current_version_id:
            ver = db.query(ChapterVersion).filter_by(id=ch.current_version_id).first()
            body = (ver.content if ver else "") or ""
        parts.append(body)
    text = "\n\n".join(p for p in parts if p).strip()
    if len(text) < MIN_CHAPTER_CHARS * 4:
        raise HTTPException(400, "正文太短,无法重切")

    parsed = await asyncio.to_thread(parse_novel_txt, text, f"{novel.title}.txt")
    if sum(len(v.chapters) for v in parsed.volumes) <= 1:
        parsed = maybe_ai_split_parsed(db, user.id, parsed, text, novel_id=novel.id)
    parsed.title = novel.title
    parsed = sanitize_parsed_novel(parsed, already_cleaned=True)
    ensure_unique_titles(parsed.volumes)
    total = sum(len(v.chapters) for v in parsed.volumes)
    if total <= 1:
        raise HTTPException(400, "规则与 AI 均未能拆出多章,请检查正文是否有场景分界")

    if total > MAX_IMPORT_CHAPTERS:
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
        total = sum(len(v.chapters) for v in parsed.volumes)

    chapter_ids = [c.id for c in chapters]
    db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).update(
        {Chapter.current_version_id: None}, synchronize_session=False
    )
    db.query(ChapterVersion).filter(ChapterVersion.chapter_id.in_(chapter_ids)).delete(synchronize_session=False)
    db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).delete(synchronize_session=False)
    db.query(Volume).filter_by(novel_id=novel_id).delete(synchronize_session=False)
    db.flush()

    vol_rows: list[tuple[Volume, object]] = []
    for vol_sort, v in enumerate(parsed.volumes, start=1):
        vol = Volume(novel_id=novel.id, title=v.title[:128], outline=v.outline, sort=vol_sort)
        db.add(vol)
        vol_rows.append((vol, v))
    db.flush()

    chapter_rows: list[tuple[Chapter, str]] = []
    ch_sort = 0
    for vol, v in vol_rows:
        for pch in v.chapters:
            content = (pch.content or "").strip()
            if len(content) < MIN_CHAPTER_CHARS:
                continue
            ch_sort += 1
            chapter = Chapter(
                novel_id=novel.id,
                volume_id=vol.id,
                title=pch.title[:128],
                outline=pch.outline,
                sort=ch_sort,
                status="approved",
                word_count=len(content),
            )
            db.add(chapter)
            chapter_rows.append((chapter, content))
    db.flush()
    for chapter, content in chapter_rows:
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

    recompute_words(db, novel)
    db.commit()
    return {
        "ok": True,
        "novel_id": novel.id,
        "title": novel.title,
        "chapters_created": len(chapter_rows),
        "source": parsed.source,
        "message": f"已重切为 {len(chapter_rows)} 章({parsed.source})",
    }


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
