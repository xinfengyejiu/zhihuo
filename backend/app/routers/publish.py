"""发布中心:平台账号、发布任务(章节 × 平台)。

当前为半自动模式:任务创建后由作者去平台后台发布,回系统点"标记已发布";
M3 接入 Playwright 自动化适配器后,queued/scheduled 任务将由 worker 执行。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Chapter,
    Novel,
    NovelPlatform,
    PLATFORM_ACCOUNT_STATUSES,
    PLATFORM_LABELS,
    PlatformAccount,
    PUBLISH_PLATFORMS,
    PublishTask,
    User,
)
from ..routers.novels import get_owned_novel
from ..schemas import (
    AccountStatusIn,
    MarkSuccessIn,
    PlatformAccountOut,
    PublishChapterOut,
    PublishCreateIn,
    PublishTaskOut,
)
from ..security import get_current_user

router = APIRouter(prefix="/publish", tags=["publish"])

ACTIVE_TASK_STATUSES = ("queued", "scheduled", "need_manual", "failed")


def _now():
    return datetime.now(timezone.utc)


def _check_platform(platform: str):
    if platform not in PUBLISH_PLATFORMS:
        raise HTTPException(400, f"未知平台:{platform}")


@router.get("/platforms")
def list_platforms():
    return [{"key": k, "label": PLATFORM_LABELS[k]} for k in PUBLISH_PLATFORMS]


# ---------- 平台账号 ----------
@router.get("/accounts", response_model=list[PlatformAccountOut])
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = {a.platform: a for a in db.query(PlatformAccount).filter_by(user_id=user.id).all()}
    out = []
    for platform in PUBLISH_PLATFORMS:
        row = rows.get(platform)
        out.append(
            PlatformAccountOut(
                platform=platform,
                status=row.status if row else "not_configured",
                note=row.note if row else None,
                updated_at=row.updated_at if row else _now(),
            )
        )
    return out


@router.put("/accounts/{platform}", response_model=PlatformAccountOut)
def set_account(
    platform: str,
    body: AccountStatusIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_platform(platform)
    if body.status not in PLATFORM_ACCOUNT_STATUSES:
        raise HTTPException(400, f"未知账号状态:{body.status}")
    row = db.query(PlatformAccount).filter_by(user_id=user.id, platform=platform).first()
    if row is None:
        row = PlatformAccount(user_id=user.id, platform=platform)
        db.add(row)
    row.status = body.status
    row.note = body.note
    db.commit()
    db.refresh(row)
    return row


# ---------- 可发布章节 ----------
def _expire_due_tasks(db: Session, novel_id: int | None = None):
    """惰性到期:排期时间已过的 scheduled 任务转为 need_manual,提醒人工发布。"""
    q = db.query(PublishTask).filter(PublishTask.status == "scheduled", PublishTask.scheduled_at <= _now() - timedelta(minutes=1))
    if novel_id is not None:
        q = q.filter(PublishTask.novel_id == novel_id)
    q.update({"status": "need_manual"}, synchronize_session=False)


def recompute_chapter_publish_status(db: Session, chapter: Chapter):
    """根据任务状态收敛章节状态机(approved ↔ scheduled ↔ published)。"""
    if chapter.status in ("draft", "pending_review", "rejected"):
        return  # 审核流状态优先
    tasks = db.query(PublishTask).filter_by(chapter_id=chapter.id).all()
    active = [t for t in tasks if t.status in ACTIVE_TASK_STATUSES]
    if tasks and any(t.status == "success" for t in tasks) and not active:
        chapter.status = "published"
    elif tasks:
        if chapter.status == "approved":
            chapter.status = "scheduled"
    else:
        if chapter.status == "scheduled":
            chapter.status = "approved"


def recompute_platform_progress(db: Session, novel_id: int, platform: str):
    """平台已发到第几章 = 该平台上已成功发布的最大章节序号。"""
    max_sort = (
        db.query(Chapter.sort)
        .join(PublishTask, PublishTask.chapter_id == Chapter.id)
        .filter(
            PublishTask.novel_id == novel_id,
            PublishTask.platform == platform,
            PublishTask.status == "success",
        )
        .order_by(Chapter.sort.desc())
        .limit(1)
        .scalar()
    )
    link = db.query(NovelPlatform).filter_by(novel_id=novel_id, platform=platform).first()
    if link is None:
        link = NovelPlatform(novel_id=novel_id, platform=platform)
        db.add(link)
    link.published_chapter_no = int(max_sort or 0)


@router.get("/chapters", response_model=list[PublishChapterOut])
def publishable_chapters(
    novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    _expire_due_tasks(db, novel_id)
    db.commit()
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel_id, Chapter.status.in_(("approved", "scheduled", "published")))
        .order_by(Chapter.sort, Chapter.id)
        .all()
    )
    out = []
    for c in chapters:
        tasks = db.query(PublishTask).filter_by(chapter_id=c.id).order_by(PublishTask.updated_at.desc()).all()
        out.append(
            PublishChapterOut(
                id=c.id,
                title=c.title,
                status=c.status,
                word_count=c.word_count,
                sort=c.sort,
                tasks=[{"platform": t.platform, "status": t.status} for t in tasks[:8]],
            )
        )
    return out


# ---------- 发布任务 ----------
@router.get("/tasks", response_model=list[PublishTaskOut])
def list_tasks(
    novel_id: int | None = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    novel_ids = [n.id for n in db.query(Novel).filter_by(owner_id=user.id).all()]
    if not novel_ids:
        return []
    _expire_due_tasks(db)
    db.commit()
    q = db.query(PublishTask).filter(PublishTask.novel_id.in_(novel_ids))
    if novel_id is not None:
        q = q.filter(PublishTask.novel_id == novel_id)
    tasks = q.order_by(PublishTask.updated_at.desc()).limit(min(limit, 200)).all()
    titles = {c.id: c.title for c in db.query(Chapter).filter(Chapter.id.in_([t.chapter_id for t in tasks])).all()} if tasks else {}
    return [
        PublishTaskOut(
            id=t.id,
            novel_id=t.novel_id,
            chapter_id=t.chapter_id,
            chapter_title=titles.get(t.chapter_id, ""),
            platform=t.platform,
            status=t.status,
            scheduled_at=t.scheduled_at,
            platform_chapter_id=t.platform_chapter_id,
            error=t.error,
            created_at=t.created_at,
        )
        for t in tasks
    ]


@router.post("/tasks", response_model=list[PublishTaskOut])
def create_tasks(
    body: PublishCreateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if not body.chapter_ids:
        raise HTTPException(400, "请选择章节")
    if not body.platforms:
        raise HTTPException(400, "请选择发布平台")
    for platform in body.platforms:
        _check_platform(platform)

    created = []
    for chapter_id in dict.fromkeys(body.chapter_ids):
        chapter = db.query(Chapter).filter_by(id=chapter_id).first()
        if chapter is None:
            raise HTTPException(400, f"章节 {chapter_id} 不存在")
        get_owned_novel(chapter.novel_id, user, db)
        if chapter.status not in ("approved", "scheduled", "published"):
            raise HTTPException(400, f"《{chapter.title}》未审核通过,不能发布")
        for platform in dict.fromkeys(body.platforms):
            existing = (
                db.query(PublishTask)
                .filter_by(chapter_id=chapter_id, platform=platform, status="success")
                .first()
            )
            if existing:
                continue  # 该平台已发布过,跳过
            active = (
                db.query(PublishTask)
                .filter(
                    PublishTask.chapter_id == chapter_id,
                    PublishTask.platform == platform,
                    PublishTask.status.in_(ACTIVE_TASK_STATUSES),
                )
                .first()
            )
            if active:
                if body.scheduled_at:  # 更新排期
                    active.status = "scheduled"
                    active.scheduled_at = body.scheduled_at
                    created.append(active)
                else:
                    created.append(active)
                continue
            task = PublishTask(
                novel_id=chapter.novel_id,
                chapter_id=chapter_id,
                platform=platform,
                status="scheduled" if body.scheduled_at else "queued",
                scheduled_at=body.scheduled_at,
            )
            db.add(task)
            created.append(task)
        recompute_chapter_publish_status(db, chapter)
    db.commit()
    for t in created:
        db.refresh(t)
    titles = {c.id: c.title for c in db.query(Chapter).filter(Chapter.id.in_([t.chapter_id for t in created])).all()}
    return [
        PublishTaskOut(
            id=t.id,
            novel_id=t.novel_id,
            chapter_id=t.chapter_id,
            chapter_title=titles.get(t.chapter_id, ""),
            platform=t.platform,
            status=t.status,
            scheduled_at=t.scheduled_at,
            platform_chapter_id=t.platform_chapter_id,
            error=t.error,
            created_at=t.created_at,
        )
        for t in created
    ]


def _get_task(db: Session, task_id: int, user: User) -> PublishTask:
    task = db.query(PublishTask).filter_by(id=task_id).first()
    if task is None:
        raise HTTPException(404, "任务不存在")
    get_owned_novel(task.novel_id, user, db)
    return task


@router.post("/tasks/{task_id}/success", response_model=PublishTaskOut)
def mark_success(
    task_id: int,
    body: MarkSuccessIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """半自动兜底:作者在平台后台发布完成后,手动标记成功。"""
    task = _get_task(db, task_id, user)
    task.status = "success"
    if body and body.platform_chapter_id:
        task.platform_chapter_id = body.platform_chapter_id
    chapter = db.get(Chapter, task.chapter_id)
    if chapter:
        recompute_chapter_publish_status(db, chapter)
    recompute_platform_progress(db, task.novel_id, task.platform)
    db.commit()
    db.refresh(task)
    return PublishTaskOut(
        id=task.id,
        novel_id=task.novel_id,
        chapter_id=task.chapter_id,
        chapter_title=chapter.title if chapter else "",
        platform=task.platform,
        status=task.status,
        scheduled_at=task.scheduled_at,
        platform_chapter_id=task.platform_chapter_id,
        error=task.error,
        created_at=task.created_at,
    )


@router.post("/tasks/{task_id}/fail", response_model=PublishTaskOut)
def mark_failed(
    task_id: int, body: dict | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    task = _get_task(db, task_id, user)
    task.status = "failed"
    task.error = (body or {}).get("error") or "人工标记失败"
    chapter = db.get(Chapter, task.chapter_id)
    if chapter:
        recompute_chapter_publish_status(db, chapter)
    db.commit()
    db.refresh(task)
    return _task_out(db, task, chapter)


@router.post("/tasks/{task_id}/retry", response_model=PublishTaskOut)
def retry_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user)
    task.status = "queued"
    task.error = None
    chapter = db.get(Chapter, task.chapter_id)
    if chapter:
        recompute_chapter_publish_status(db, chapter)
    db.commit()
    db.refresh(task)
    return _task_out(db, task, chapter)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user)
    chapter = db.get(Chapter, task.chapter_id)
    db.delete(task)
    if chapter:
        recompute_chapter_publish_status(db, chapter)
    db.commit()
    return {"ok": True}


# ---------- L1 半自动:打开平台作者后台(Playwright 可选) ----------
PLATFORM_HOME_URLS = {
    "fanqie": "https://zonghe.fanqienz.com/",
    "qidian": "https://write.qidian.com/",
    "qimao": "https://zuojia.qimao.com/",
    "feilu": "https://www.faloo.com/",
    "zongheng": "http://www.zongheng.com/",
    "jjwxc": "http://www.jjwxc.net/",
}


@router.post("/tasks/{task_id}/open-platform")
def open_platform(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """L1 半自动辅助:在本机打开平台作者后台(带持久化登录目录),作者粘贴发布后回来点「标记已发布」。

    需要 backend 环境安装 playwright 并执行 `playwright install chromium`;
    未安装时返回明确提示,不影响半自动手动流程。
    """
    task = _get_task(db, task_id, user)
    chapter = db.get(Chapter, task.chapter_id)
    url = PLATFORM_HOME_URLS.get(task.platform)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "message": "后端未安装 playwright(自动化可选组件)。请先粘贴复制文本到平台后台手动发布,或安装:pip install playwright && playwright install chromium",
        }
    import tempfile
    from pathlib import Path

    profile_dir = Path(tempfile.gettempdir()) / "xiaohuo_playwright_profile" / task.platform
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                str(profile_dir), headless=False, viewport={"width": 1440, "height": 900}
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto(url or "about:blank")
            return {
                "ok": True,
                "message": f"已在浏览器打开 {PLATFORM_LABELS.get(task.platform, task.platform)} 作者后台,登录并发布后回到系统点「标记已发布」。该平台登录态已持久化,下次免登录。",
            }
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0][:200] if str(e) else "启动失败"
        if "Executable doesn't exist" in str(e) or "browserType.launch" in msg:
            return {
                "ok": False,
                "message": "未找到 Chromium,请执行:playwright install chromium",
            }
        return {"ok": False, "message": f"打开平台页面失败:{msg}"}


def _task_out(db: Session, task: PublishTask, chapter: Chapter | None = None) -> PublishTaskOut:
    if chapter is None:
        chapter = db.get(Chapter, task.chapter_id)
    return PublishTaskOut(
        id=task.id,
        novel_id=task.novel_id,
        chapter_id=task.chapter_id,
        chapter_title=chapter.title if chapter else "",
        platform=task.platform,
        status=task.status,
        scheduled_at=task.scheduled_at,
        platform_chapter_id=task.platform_chapter_id,
        error=task.error,
        created_at=task.created_at,
    )
