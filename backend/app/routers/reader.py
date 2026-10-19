"""阅读器:成书目录(仅审核通过及之后)+ 正文。公开访问;读者账号记进度与统计。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Chapter,
    Novel,
    NovelReaderStats,
    ReaderHistory,
    ReaderProgress,
    ReaderUser,
    ReviewableFilter,
    Volume,
    now,
)
from ..security import get_optional_reader

router = APIRouter(prefix="/reader", tags=["reader"])

CATALOG_PAGE_SIZE = 40
CONTENT_CHARS_PER_PAGE = 1800


def _split_content_pages(content: str, chars_per_page: int) -> list[str]:
    """按段落尽量整齐切成若干页,避免半句截断。"""
    text = (content or "").replace("\r\n", "\n").strip()
    if not text:
        return [""]
    if len(text) <= chars_per_page:
        return [text]
    paras = text.split("\n")
    pages: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        add = len(p) + (1 if buf else 0)
        if buf and size + add > chars_per_page:
            pages.append("\n".join(buf))
            buf = [p]
            size = len(p)
        else:
            if buf:
                size += 1
            buf.append(p)
            size += len(p)
    if buf:
        pages.append("\n".join(buf))
    # 极长无换行段落再硬切
    out: list[str] = []
    for page in pages:
        if len(page) <= chars_per_page * 2:
            out.append(page)
            continue
        start = 0
        while start < len(page):
            out.append(page[start : start + chars_per_page])
            start += chars_per_page
    return out or [""]


def _get_public_novel(db: Session, novel_id: int) -> Novel:
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")
    return novel


def _has_readable(db: Session, novel_id: int) -> bool:
    return (
        db.query(Chapter.id)
        .filter(Chapter.novel_id == novel_id, Chapter.status.in_(ReviewableFilter))
        .first()
        is not None
    )


def _ensure_stats(db: Session, novel_id: int) -> NovelReaderStats:
    row = db.get(NovelReaderStats, novel_id)
    if row is None:
        row = NovelReaderStats(novel_id=novel_id, read_count=0, like_count=0, favorite_count=0)
        db.add(row)
        db.flush()
    return row


def _touch_history(db: Session, reader: ReaderUser, novel_id: int, chapter_id: int):
    row = db.query(ReaderHistory).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
    if row is None:
        db.add(
            ReaderHistory(
                reader_user_id=reader.id,
                novel_id=novel_id,
                last_chapter_id=chapter_id,
                visited_at=now(),
            )
        )
    else:
        row.last_chapter_id = chapter_id
        row.visited_at = now()


@router.get("/novels/{novel_id}/catalog")
def catalog(
    novel_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(CATALOG_PAGE_SIZE, ge=10, le=100),
    reader: ReaderUser | None = Depends(get_optional_reader),
    db: Session = Depends(get_db),
):
    novel = _get_public_novel(db, novel_id)
    if not _has_readable(db, novel_id):
        raise HTTPException(404, "小说尚无可阅读章节")
    filt = and_(Chapter.novel_id == novel_id, Chapter.status.in_(ReviewableFilter))
    total = int(db.query(func.count(Chapter.id)).filter(filt).scalar() or 0)
    first = db.query(Chapter).filter(filt).order_by(Chapter.sort, Chapter.id).first()
    chapters = (
        db.query(Chapter)
        .filter(filt)
        .order_by(Chapter.sort, Chapter.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    vol_ids = {c.volume_id for c in chapters if c.volume_id}
    volumes = {}
    if vol_ids:
        volumes = {
            v.id: v.title
            for v in db.query(Volume).filter(Volume.id.in_(vol_ids)).all()
        }
    progress_chapter_id = None
    position_pct = 0
    if reader is not None:
        progress = db.query(ReaderProgress).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
        if progress:
            progress_chapter_id = progress.chapter_id
            position_pct = progress.position_pct or 0
        hist_ch = progress_chapter_id or (first.id if first else None)
        if hist_ch:
            _touch_history(db, reader, novel_id, hist_ch)
        db.commit()
    return {
        "novel": {"id": novel.id, "title": novel.title, "summary": novel.summary, "cover": novel.cover},
        "progress_chapter_id": progress_chapter_id,
        "position_pct": position_pct,
        "first_chapter_id": first.id if first else None,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "has_more": page * page_size < int(total),
        "chapters": [
            {
                "id": c.id,
                "title": c.title,
                "status": c.status,
                "word_count": c.word_count,
                "sort": c.sort,
                "volume_id": c.volume_id,
                "volume_title": volumes.get(c.volume_id),
            }
            for c in chapters
        ],
    }


@router.get("/novels/{novel_id}/chapters/{chapter_id}")
def read_chapter(
    novel_id: int,
    chapter_id: int,
    page: int = Query(1, ge=1),
    chars_per_page: int = Query(CONTENT_CHARS_PER_PAGE, ge=600, le=8000),
    reader: ReaderUser | None = Depends(get_optional_reader),
    db: Session = Depends(get_db),
):
    _get_public_novel(db, novel_id)
    chapter = db.query(Chapter).filter_by(id=chapter_id, novel_id=novel_id).first()
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    if chapter.status not in ReviewableFilter:
        raise HTTPException(403, "该章节尚未审核通过,不能在阅读器中查看")

    if reader is not None:
        row = db.query(ReaderProgress).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
        created = False
        if row is None:
            db.add(
                ReaderProgress(
                    reader_user_id=reader.id,
                    novel_id=novel_id,
                    chapter_id=chapter.id,
                    position_pct=0,
                )
            )
            created = True
        else:
            row.chapter_id = chapter.id
            row.updated_at = now()
        _touch_history(db, reader, novel_id, chapter.id)
        if created:
            stats = _ensure_stats(db, novel_id)
            stats.read_count = int(stats.read_count or 0) + 1
        db.commit()

    # 邻章:只查前后各一条,避免拉全量目录
    prev = (
        db.query(Chapter)
        .filter(
            Chapter.novel_id == novel_id,
            Chapter.status.in_(ReviewableFilter),
            or_(
                Chapter.sort < chapter.sort,
                and_(Chapter.sort == chapter.sort, Chapter.id < chapter.id),
            ),
        )
        .order_by(Chapter.sort.desc(), Chapter.id.desc())
        .first()
    )
    nxt = (
        db.query(Chapter)
        .filter(
            Chapter.novel_id == novel_id,
            Chapter.status.in_(ReviewableFilter),
            or_(
                Chapter.sort > chapter.sort,
                and_(Chapter.sort == chapter.sort, Chapter.id > chapter.id),
            ),
        )
        .order_by(Chapter.sort.asc(), Chapter.id.asc())
        .first()
    )
    content = ""
    for v in chapter.versions:
        if v.id == chapter.current_version_id:
            content = v.content or ""
            break
    pages = _split_content_pages(content, chars_per_page)
    total_pages = len(pages)
    page = min(page, total_pages)
    return {
        "id": chapter.id,
        "title": chapter.title,
        "content": pages[page - 1],
        "word_count": chapter.word_count,
        "full_char_count": len(content),
        "page": page,
        "total_pages": total_pages,
        "chars_per_page": chars_per_page,
        "has_more": page < total_pages,
        "prev_id": prev.id if prev else None,
        "next_id": nxt.id if nxt else None,
    }
