"""阅读器:成书目录(仅审核通过及之后)+ 正文。公开访问;读者账号记进度与统计。"""

from fastapi import APIRouter, Depends, HTTPException
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
    reader: ReaderUser | None = Depends(get_optional_reader),
    db: Session = Depends(get_db),
):
    novel = _get_public_novel(db, novel_id)
    if not _has_readable(db, novel_id):
        raise HTTPException(404, "小说尚无可阅读章节")
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel_id, Chapter.status.in_(ReviewableFilter))
        .order_by(Chapter.sort, Chapter.id)
        .all()
    )
    volumes = {v.id: v.title for v in db.query(Volume).filter_by(novel_id=novel_id).all()}
    progress_chapter_id = None
    position_pct = 0
    if reader is not None:
        progress = db.query(ReaderProgress).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
        if progress:
            progress_chapter_id = progress.chapter_id
            position_pct = progress.position_pct or 0
        hist_ch = progress_chapter_id or (chapters[0].id if chapters else None)
        if hist_ch:
            _touch_history(db, reader, novel_id, hist_ch)
        db.commit()
    return {
        "novel": {"id": novel.id, "title": novel.title, "summary": novel.summary, "cover": novel.cover},
        "progress_chapter_id": progress_chapter_id,
        "position_pct": position_pct,
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

    siblings = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel_id, Chapter.status.in_(ReviewableFilter))
        .order_by(Chapter.sort, Chapter.id)
        .all()
    )
    idx = next((i for i, c in enumerate(siblings) if c.id == chapter.id), None)
    content = ""
    for v in chapter.versions:
        if v.id == chapter.current_version_id:
            content = v.content
            break
    return {
        "id": chapter.id,
        "title": chapter.title,
        "content": content,
        "word_count": chapter.word_count,
        "prev_id": siblings[idx - 1].id if idx is not None and idx > 0 else None,
        "next_id": siblings[idx + 1].id if idx is not None and idx < len(siblings) - 1 else None,
    }
