"""阅读器书城:独立读者账号、发现、我的书城、收藏/喜欢。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Chapter,
    Novel,
    NovelReaderStats,
    NovelTag,
    ReaderFavorite,
    ReaderHistory,
    ReaderLike,
    ReaderProgress,
    ReaderUser,
    ReviewableFilter,
    Tag,
    now as model_now,
)
from ..schemas import (
    ReaderBookshelfItemOut,
    ReaderLoginIn,
    ReaderNovelCardOut,
    ReaderNovelDetailOut,
    ReaderProgressIn,
    ReaderRegisterIn,
    ReaderSyncLocalIn,
    ReaderTokenOut,
    ReaderUserOut,
)
from ..security import (
    create_reader_access_token,
    get_current_reader,
    get_optional_reader,
    hash_password,
    verify_password,
)
router = APIRouter(prefix="/reader", tags=["reader-store"])

# 固定一级分类:key -> 展示名 + 标签别名(大小写不敏感,书的任意标签命中即归入)
STORE_CATEGORIES: list[dict[str, object]] = [
    {"key": "all", "label": "全部", "tag_aliases": []},
    {"key": "dushi", "label": "都市", "tag_aliases": ["都市", "都市生活", "都市日常", "现代都市"]},
    {"key": "xuanhuan", "label": "玄幻", "tag_aliases": ["玄幻", "奇幻", "修真", "仙侠", "玄幻修真"]},
    {"key": "naodong", "label": "脑洞", "tag_aliases": ["脑洞", "脑洞大开", "创意", "无限"]},
    {"key": "chuanyue", "label": "穿越", "tag_aliases": ["穿越", "穿越重生", "重生", "穿越时空"]},
    {"key": "xitong", "label": "系统", "tag_aliases": ["系统", "系统流", "金手指"]},
    {"key": "yanqing", "label": "言情", "tag_aliases": ["言情", "甜宠", "现代言情", "古代言情", "恋爱"]},
    {"key": "lishi", "label": "历史", "tag_aliases": ["历史", "架空历史", "历史军事", "军事"]},
    {"key": "kehuan", "label": "科幻", "tag_aliases": ["科幻", "未来", "星际", "末世"]},
]


def _category_public() -> list[dict[str, str]]:
    return [{"key": str(c["key"]), "label": str(c["label"])} for c in STORE_CATEGORIES]


def _category_aliases(key: str) -> list[str] | None:
    """None = 全部不过滤; [] = 未知 key 视为全部。"""
    k = (key or "all").strip().lower()
    if k in ("", "all", "全部"):
        return None
    for c in STORE_CATEGORIES:
        ck = str(c["key"]).lower()
        label = str(c["label"])
        if k == ck or k == label.lower() or k == label:
            aliases = c.get("tag_aliases") or []
            return [str(a) for a in aliases]  # type: ignore[arg-type]
    # 允许直接用中文 label 当 key
    for c in STORE_CATEGORIES:
        if str(c["label"]) == (key or "").strip():
            aliases = c.get("tag_aliases") or []
            return [str(a) for a in aliases]  # type: ignore[arg-type]
    return None


def _novel_in_category(tags: list[str], category: str | None) -> bool:
    aliases = _category_aliases(category or "all")
    if aliases is None:
        return True
    if not aliases:
        return True
    tag_set = {t.strip().lower() for t in tags if t and t.strip()}
    alias_set = {a.strip().lower() for a in aliases if a and a.strip()}
    return bool(tag_set & alias_set)


def _now():
    return model_now()


def _public_novel_ids(db: Session) -> set[int]:
    rows = (
        db.query(Chapter.novel_id)
        .filter(Chapter.status.in_(ReviewableFilter))
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def _ensure_stats(db: Session, novel_id: int) -> NovelReaderStats:
    row = db.get(NovelReaderStats, novel_id)
    if row is None:
        row = NovelReaderStats(novel_id=novel_id, read_count=0, like_count=0, favorite_count=0)
        db.add(row)
        db.flush()
    return row


def _tag_names(db: Session, novel_id: int) -> list[str]:
    tags = (
        db.query(Tag.name)
        .join(NovelTag, NovelTag.tag_id == Tag.id)
        .filter(NovelTag.novel_id == novel_id)
        .all()
    )
    return [t[0] for t in tags if t[0]]


def _chapter_agg(db: Session, novel_ids: list[int]) -> dict[int, tuple[int, int, datetime | None]]:
    """novel_id -> (chapter_count, word_count, max_updated_at)"""
    if not novel_ids:
        return {}
    rows = (
        db.query(
            Chapter.novel_id,
            func.count(Chapter.id),
            func.coalesce(func.sum(Chapter.word_count), 0),
            func.max(Chapter.updated_at),
        )
        .filter(Chapter.novel_id.in_(novel_ids), Chapter.status.in_(ReviewableFilter))
        .group_by(Chapter.novel_id)
        .all()
    )
    return {r[0]: (int(r[1] or 0), int(r[2] or 0), r[3]) for r in rows}


def _stats_map(db: Session, novel_ids: list[int]) -> dict[int, NovelReaderStats]:
    if not novel_ids:
        return {}
    rows = db.query(NovelReaderStats).filter(NovelReaderStats.novel_id.in_(novel_ids)).all()
    return {r.novel_id: r for r in rows}


def _card(
    novel: Novel,
    *,
    tags: list[str],
    chapter_count: int,
    word_count: int,
    stats: NovelReaderStats | None,
    progress_chapter_id: int | None = None,
    progress_chapter_title: str | None = None,
    favorited: bool = False,
    liked: bool = False,
    updated_at: datetime | None = None,
) -> ReaderNovelCardOut:
    return ReaderNovelCardOut(
        id=novel.id,
        title=novel.title,
        summary=novel.summary,
        cover=novel.cover,
        tags=tags,
        word_count=word_count,
        chapter_count=chapter_count,
        read_count=stats.read_count if stats else 0,
        like_count=stats.like_count if stats else 0,
        favorite_count=stats.favorite_count if stats else 0,
        updated_at=updated_at or novel.updated_at,
        progress_chapter_id=progress_chapter_id,
        progress_chapter_title=progress_chapter_title,
        favorited=favorited,
        liked=liked,
    )


def _touch_history(db: Session, reader: ReaderUser, novel_id: int, chapter_id: int | None):
    row = (
        db.query(ReaderHistory)
        .filter_by(reader_user_id=reader.id, novel_id=novel_id)
        .first()
    )
    if row is None:
        db.add(
            ReaderHistory(
                reader_user_id=reader.id,
                novel_id=novel_id,
                last_chapter_id=chapter_id,
                visited_at=_now(),
            )
        )
    else:
        row.last_chapter_id = chapter_id or row.last_chapter_id
        row.visited_at = _now()


# ---------- Auth ----------
@router.post("/auth/register", response_model=ReaderTokenOut)
def reader_register(body: ReaderRegisterIn, db: Session = Depends(get_db)):
    if db.query(ReaderUser).filter_by(username=body.username).first():
        raise HTTPException(400, "用户名已存在")
    user = ReaderUser(
        username=body.username,
        nickname=body.nickname or body.username,
        password_hash=hash_password(body.password),
        last_login_at=_now(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return ReaderTokenOut(
        access_token=create_reader_access_token(user.id),
        user=ReaderUserOut.model_validate(user),
    )


@router.post("/auth/login", response_model=ReaderTokenOut)
def reader_login(body: ReaderLoginIn, db: Session = Depends(get_db)):
    user = db.query(ReaderUser).filter_by(username=body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    user.last_login_at = _now()
    db.commit()
    db.refresh(user)
    return ReaderTokenOut(
        access_token=create_reader_access_token(user.id),
        user=ReaderUserOut.model_validate(user),
    )


@router.get("/auth/me", response_model=ReaderUserOut)
def reader_me(user: ReaderUser = Depends(get_current_reader)):
    return ReaderUserOut.model_validate(user)


# ---------- Store ----------
@router.get("/store/categories")
def store_categories():
    return {"categories": _category_public()}


@router.get("/store/home")
def store_home(
    category: str = Query("all"),
    reader: ReaderUser | None = Depends(get_optional_reader),
    db: Session = Depends(get_db),
):
    public_ids = list(_public_novel_ids(db))
    continue_reading: list[ReaderNovelCardOut] = []
    if reader and public_ids:
        progresses = (
            db.query(ReaderProgress)
            .filter(ReaderProgress.reader_user_id == reader.id, ReaderProgress.novel_id.in_(public_ids))
            .order_by(ReaderProgress.updated_at.desc())
            .limit(8)
            .all()
        )
        continue_reading = _cards_for_progress(db, progresses, reader)

    hot = _list_novels(db, sort="hot", limit=12, category=category, reader=reader)
    newest = _list_novels(db, sort="new", limit=12, category=category, reader=reader)
    for_you = _recommend(db, reader, limit=12)
    rank = _list_novels(db, sort="hot", limit=8, category=category, reader=reader)
    featured = for_you if (category or "all").strip().lower() in ("", "all", "全部") else hot[:8]

    # 标签:从公开书关联的标签聚合
    tag_rows = (
        db.query(Tag.name, func.count(NovelTag.novel_id))
        .join(NovelTag, NovelTag.tag_id == Tag.id)
        .filter(NovelTag.novel_id.in_(public_ids or [0]))
        .group_by(Tag.name)
        .order_by(func.count(NovelTag.novel_id).desc())
        .limit(20)
        .all()
    )
    tags = [{"name": n, "count": int(c)} for n, c in tag_rows if n]

    return {
        "continue_reading": continue_reading,
        "hot": hot,
        "newest": newest,
        "for_you": for_you,
        "rank": rank,
        "featured": featured,
        "categories": _category_public(),
        "category": category or "all",
        "tags": tags,
    }


@router.get("/store/rank")
def store_rank(
    category: str = Query("all"),
    sort: str = Query("hot", pattern="^(hot|new|likes)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=60),
    reader: ReaderUser | None = Depends(get_optional_reader),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    books = _list_novels(
        db,
        category=category,
        sort=sort,
        limit=page_size,
        offset=offset,
        reader=reader,
    )
    return {
        "category": category or "all",
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "items": [
            {**b.model_dump(), "rank": offset + i + 1}
            for i, b in enumerate(books)
        ],
    }


@router.get("/store/novels", response_model=list[ReaderNovelCardOut])
def store_novels(
    q: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    sort: str = Query("hot", pattern="^(hot|new|likes)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    reader: ReaderUser | None = Depends(get_optional_reader),
    db: Session = Depends(get_db),
):
    return _list_novels(
        db,
        q=q,
        tag=tag,
        category=category,
        sort=sort,
        limit=page_size,
        offset=(page - 1) * page_size,
        reader=reader,
    )


@router.get("/store/novels/{novel_id}", response_model=ReaderNovelDetailOut)
def store_novel_detail(
    novel_id: int,
    reader: ReaderUser | None = Depends(get_optional_reader),
    db: Session = Depends(get_db),
):
    novel = db.get(Novel, novel_id)
    if novel is None or novel_id not in _public_novel_ids(db):
        raise HTTPException(404, "小说不存在或尚未有可阅读章节")
    cards = _build_cards(db, [novel], reader)
    card = cards[0] if cards else None
    if card is None:
        raise HTTPException(404, "小说不存在")
    if reader:
        _touch_history(db, reader, novel_id, card.progress_chapter_id)
        db.commit()
    return ReaderNovelDetailOut(**card.model_dump())


def _list_novels(
    db: Session,
    *,
    q: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    sort: str = "hot",
    limit: int = 24,
    offset: int = 0,
    reader: ReaderUser | None = None,
) -> list[ReaderNovelCardOut]:
    public_ids = _public_novel_ids(db)
    if not public_ids:
        return []
    query = db.query(Novel).filter(Novel.id.in_(public_ids))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(Novel.title.ilike(like), Novel.summary.ilike(like)))
    if tag and tag.strip():
        query = (
            query.join(NovelTag, NovelTag.novel_id == Novel.id)
            .join(Tag, Tag.id == NovelTag.tag_id)
            .filter(Tag.name == tag.strip())
        )
    novels = query.all()
    if not novels:
        return []

    # 一级分类:按标签别名过滤(全部不过滤)
    aliases = _category_aliases(category or "all")
    if aliases is not None:
        filtered: list[Novel] = []
        for n in novels:
            if _novel_in_category(_tag_names(db, n.id), category):
                filtered.append(n)
        novels = filtered
        if not novels:
            return []

    agg = _chapter_agg(db, [n.id for n in novels])
    stats = _stats_map(db, [n.id for n in novels])

    def sort_key(n: Novel):
        st = stats.get(n.id)
        ch_count, words, updated = agg.get(n.id, (0, 0, n.updated_at))
        if sort == "likes":
            return (st.like_count if st else 0, st.read_count if st else 0, updated or n.updated_at)
        if sort == "new":
            return updated or n.updated_at or datetime.min
        # hot: read + like*3
        rc = st.read_count if st else 0
        lc = st.like_count if st else 0
        return (rc + lc * 3, updated or n.updated_at)

    novels.sort(key=sort_key, reverse=True)
    sliced = novels[offset : offset + limit]
    return _build_cards(db, sliced, reader, agg=agg, stats=stats)


def _recommend(db: Session, reader: ReaderUser | None, limit: int = 12) -> list[ReaderNovelCardOut]:
    if reader is None:
        return _list_novels(db, sort="hot", limit=limit, reader=None)
    # 最近喜欢/阅读的标签
    liked_ids = [
        r.novel_id
        for r in db.query(ReaderLike).filter_by(reader_user_id=reader.id).order_by(ReaderLike.created_at.desc()).limit(20).all()
    ]
    prog_ids = [
        r.novel_id
        for r in db.query(ReaderProgress).filter_by(reader_user_id=reader.id).order_by(ReaderProgress.updated_at.desc()).limit(20).all()
    ]
    seed = list(dict.fromkeys(liked_ids + prog_ids))
    if not seed:
        return _list_novels(db, sort="hot", limit=limit, reader=reader)
    tag_names = set()
    for nid in seed[:10]:
        tag_names.update(_tag_names(db, nid))
    if not tag_names:
        return _list_novels(db, sort="hot", limit=limit, reader=reader)
    # 同标签热门,排除已读种子
    public_ids = _public_novel_ids(db) - set(seed)
    if not public_ids:
        return _list_novels(db, sort="hot", limit=limit, reader=reader)
    matched = (
        db.query(Novel)
        .join(NovelTag, NovelTag.novel_id == Novel.id)
        .join(Tag, Tag.id == NovelTag.tag_id)
        .filter(Novel.id.in_(public_ids), Tag.name.in_(list(tag_names)))
        .distinct()
        .all()
    )
    if not matched:
        return _list_novels(db, sort="hot", limit=limit, reader=reader)
    cards = _build_cards(db, matched, reader)
    cards.sort(key=lambda c: c.read_count + c.like_count * 3, reverse=True)
    return cards[:limit]


def _build_cards(
    db: Session,
    novels: list[Novel],
    reader: ReaderUser | None,
    *,
    agg: dict | None = None,
    stats: dict | None = None,
) -> list[ReaderNovelCardOut]:
    if not novels:
        return []
    ids = [n.id for n in novels]
    agg = agg or _chapter_agg(db, ids)
    stats = stats or _stats_map(db, ids)
    progress_map: dict[int, ReaderProgress] = {}
    fav_set: set[int] = set()
    like_set: set[int] = set()
    if reader:
        for p in db.query(ReaderProgress).filter(
            ReaderProgress.reader_user_id == reader.id, ReaderProgress.novel_id.in_(ids)
        ).all():
            progress_map[p.novel_id] = p
        fav_set = {
            f.novel_id
            for f in db.query(ReaderFavorite).filter(
                ReaderFavorite.reader_user_id == reader.id, ReaderFavorite.novel_id.in_(ids)
            ).all()
        }
        like_set = {
            f.novel_id
            for f in db.query(ReaderLike).filter(
                ReaderLike.reader_user_id == reader.id, ReaderLike.novel_id.in_(ids)
            ).all()
        }
    chapter_titles: dict[int, str] = {}
    ch_ids = [p.chapter_id for p in progress_map.values()]
    if ch_ids:
        for c in db.query(Chapter).filter(Chapter.id.in_(ch_ids)).all():
            chapter_titles[c.id] = c.title

    out: list[ReaderNovelCardOut] = []
    for n in novels:
        ch_count, words, updated = agg.get(n.id, (0, 0, n.updated_at))
        prog = progress_map.get(n.id)
        out.append(
            _card(
                n,
                tags=_tag_names(db, n.id),
                chapter_count=ch_count,
                word_count=words,
                stats=stats.get(n.id),
                progress_chapter_id=prog.chapter_id if prog else None,
                progress_chapter_title=chapter_titles.get(prog.chapter_id) if prog else None,
                favorited=n.id in fav_set,
                liked=n.id in like_set,
                updated_at=updated,
            )
        )
    return out


def _cards_for_progress(
    db: Session, progresses: list[ReaderProgress], reader: ReaderUser
) -> list[ReaderNovelCardOut]:
    if not progresses:
        return []
    novels = {n.id: n for n in db.query(Novel).filter(Novel.id.in_([p.novel_id for p in progresses])).all()}
    ordered = [novels[p.novel_id] for p in progresses if p.novel_id in novels]
    return _build_cards(db, ordered, reader)


# ---------- Bookshelf / me ----------
@router.get("/me/bookshelf", response_model=list[ReaderBookshelfItemOut])
def my_bookshelf(
    tab: str = Query("reading", pattern="^(reading|favorite|like|history)$"),
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    public_ids = _public_novel_ids(db)
    items: list[ReaderBookshelfItemOut] = []
    if tab == "reading":
        rows = (
            db.query(ReaderProgress)
            .filter(ReaderProgress.reader_user_id == reader.id, ReaderProgress.novel_id.in_(public_ids or [0]))
            .order_by(ReaderProgress.updated_at.desc())
            .limit(100)
            .all()
        )
        cards = _cards_for_progress(db, rows, reader)
        prog_map = {r.novel_id: r for r in rows}
        for c in cards:
            p = prog_map.get(c.id)
            items.append(
                ReaderBookshelfItemOut(
                    **c.model_dump(),
                    progress_updated_at=p.updated_at if p else None,
                )
            )
    elif tab == "favorite":
        rows = (
            db.query(ReaderFavorite)
            .filter(ReaderFavorite.reader_user_id == reader.id, ReaderFavorite.novel_id.in_(public_ids or [0]))
            .order_by(ReaderFavorite.created_at.desc())
            .limit(100)
            .all()
        )
        novels = {n.id: n for n in db.query(Novel).filter(Novel.id.in_([r.novel_id for r in rows] or [0])).all()}
        cards = _build_cards(db, [novels[r.novel_id] for r in rows if r.novel_id in novels], reader)
        for c in cards:
            items.append(ReaderBookshelfItemOut(**c.model_dump()))
    elif tab == "like":
        rows = (
            db.query(ReaderLike)
            .filter(ReaderLike.reader_user_id == reader.id, ReaderLike.novel_id.in_(public_ids or [0]))
            .order_by(ReaderLike.created_at.desc())
            .limit(100)
            .all()
        )
        novels = {n.id: n for n in db.query(Novel).filter(Novel.id.in_([r.novel_id for r in rows] or [0])).all()}
        cards = _build_cards(db, [novels[r.novel_id] for r in rows if r.novel_id in novels], reader)
        for c in cards:
            items.append(ReaderBookshelfItemOut(**c.model_dump()))
    else:  # history
        rows = (
            db.query(ReaderHistory)
            .filter(ReaderHistory.reader_user_id == reader.id, ReaderHistory.novel_id.in_(public_ids or [0]))
            .order_by(ReaderHistory.visited_at.desc())
            .limit(100)
            .all()
        )
        novels = {n.id: n for n in db.query(Novel).filter(Novel.id.in_([r.novel_id for r in rows] or [0])).all()}
        cards = _build_cards(db, [novels[r.novel_id] for r in rows if r.novel_id in novels], reader)
        hist_map = {r.novel_id: r for r in rows}
        for c in cards:
            h = hist_map.get(c.id)
            items.append(ReaderBookshelfItemOut(**c.model_dump(), visited_at=h.visited_at if h else None))
    return items


@router.put("/novels/{novel_id}/progress")
def put_progress(
    novel_id: int,
    body: ReaderProgressIn,
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    if novel_id not in _public_novel_ids(db):
        raise HTTPException(404, "小说不存在或尚未有可阅读章节")
    chapter = db.query(Chapter).filter_by(id=body.chapter_id, novel_id=novel_id).first()
    if chapter is None or chapter.status not in ReviewableFilter:
        raise HTTPException(400, "章节不可读")
    pct = max(0, min(100, int(body.position_pct or 0)))
    row = db.query(ReaderProgress).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
    created = False
    if row is None:
        row = ReaderProgress(
            reader_user_id=reader.id,
            novel_id=novel_id,
            chapter_id=body.chapter_id,
            position_pct=pct,
        )
        db.add(row)
        created = True
    else:
        row.chapter_id = body.chapter_id
        row.position_pct = pct
        row.updated_at = _now()
    _touch_history(db, reader, novel_id, body.chapter_id)
    if created:
        stats = _ensure_stats(db, novel_id)
        stats.read_count = int(stats.read_count or 0) + 1
    db.commit()
    return {"ok": True, "chapter_id": body.chapter_id, "position_pct": pct}


@router.delete("/novels/{novel_id}/progress")
def delete_progress(
    novel_id: int,
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    db.query(ReaderProgress).filter_by(reader_user_id=reader.id, novel_id=novel_id).delete()
    db.commit()
    return {"ok": True}


@router.post("/novels/{novel_id}/favorite")
def add_favorite(
    novel_id: int,
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    if novel_id not in _public_novel_ids(db):
        raise HTTPException(404, "小说不存在")
    exists = db.query(ReaderFavorite).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
    if not exists:
        db.add(ReaderFavorite(reader_user_id=reader.id, novel_id=novel_id))
        stats = _ensure_stats(db, novel_id)
        stats.favorite_count = int(stats.favorite_count or 0) + 1
        db.commit()
    return {"ok": True, "favorited": True}


@router.delete("/novels/{novel_id}/favorite")
def remove_favorite(
    novel_id: int,
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    row = db.query(ReaderFavorite).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
    if row:
        db.delete(row)
        stats = _ensure_stats(db, novel_id)
        stats.favorite_count = max(0, int(stats.favorite_count or 0) - 1)
        db.commit()
    return {"ok": True, "favorited": False}


@router.post("/novels/{novel_id}/like")
def add_like(
    novel_id: int,
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    if novel_id not in _public_novel_ids(db):
        raise HTTPException(404, "小说不存在")
    exists = db.query(ReaderLike).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
    if not exists:
        db.add(ReaderLike(reader_user_id=reader.id, novel_id=novel_id))
        stats = _ensure_stats(db, novel_id)
        stats.like_count = int(stats.like_count or 0) + 1
        db.commit()
    return {"ok": True, "liked": True}


@router.delete("/novels/{novel_id}/like")
def remove_like(
    novel_id: int,
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    row = db.query(ReaderLike).filter_by(reader_user_id=reader.id, novel_id=novel_id).first()
    if row:
        db.delete(row)
        stats = _ensure_stats(db, novel_id)
        stats.like_count = max(0, int(stats.like_count or 0) - 1)
        db.commit()
    return {"ok": True, "liked": False}


@router.post("/me/sync-local")
def sync_local(
    body: ReaderSyncLocalIn,
    reader: ReaderUser = Depends(get_current_reader),
    db: Session = Depends(get_db),
):
    """合并游客 localStorage 进度/收藏/喜欢到服务端。"""
    public_ids = _public_novel_ids(db)
    for item in body.progress:
        if item.novel_id not in public_ids:
            continue
        chapter = db.query(Chapter).filter_by(id=item.chapter_id, novel_id=item.novel_id).first()
        if chapter is None or chapter.status not in ReviewableFilter:
            continue
        row = db.query(ReaderProgress).filter_by(reader_user_id=reader.id, novel_id=item.novel_id).first()
        pct = max(0, min(100, int(item.position_pct or 0)))
        if row is None:
            db.add(
                ReaderProgress(
                    reader_user_id=reader.id,
                    novel_id=item.novel_id,
                    chapter_id=item.chapter_id,
                    position_pct=pct,
                )
            )
        else:
            # 以较新本地章为准(简单:直接覆盖)
            row.chapter_id = item.chapter_id
            row.position_pct = pct
            row.updated_at = _now()
        _touch_history(db, reader, item.novel_id, item.chapter_id)

    for nid in body.favorite_novel_ids:
        if nid not in public_ids:
            continue
        if not db.query(ReaderFavorite).filter_by(reader_user_id=reader.id, novel_id=nid).first():
            db.add(ReaderFavorite(reader_user_id=reader.id, novel_id=nid))
            stats = _ensure_stats(db, nid)
            stats.favorite_count = int(stats.favorite_count or 0) + 1

    for nid in body.like_novel_ids:
        if nid not in public_ids:
            continue
        if not db.query(ReaderLike).filter_by(reader_user_id=reader.id, novel_id=nid).first():
            db.add(ReaderLike(reader_user_id=reader.id, novel_id=nid))
            stats = _ensure_stats(db, nid)
            stats.like_count = int(stats.like_count or 0) + 1

    db.commit()
    return {"ok": True}


@router.get("/novels/{novel_id}/share-meta")
def share_meta(novel_id: int, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None or novel_id not in _public_novel_ids(db):
        raise HTTPException(404, "小说不存在")
    return {
        "id": novel.id,
        "title": novel.title,
        "summary": (novel.summary or "")[:200],
        "cover": novel.cover,
        "url_path": f"/{novel.id}",
    }
