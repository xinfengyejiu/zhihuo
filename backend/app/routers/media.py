"""自媒体产品:账号 / 选题 / 成稿 / 聊天。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai.extract import extract_json_object
from ..ai.gateway import log_call_error, log_usage, resolve_any_purpose, stream_chat
from ..ai.media_gen import (
    autofill_placeholders_with_images,
    extract_image_placeholders,
    is_pure_image_request,
    is_pure_video_request,
    resolve_chat_modality,
    save_bytes,
    stream_media_generation,
)
from ..ai.media_publish import has_publish_credentials, publish_article
from ..ai.product_context import build_media_system_prompt
from ..ai.vision import (
    VISION_OKR_PROMPT,
    build_vision_user_content,
    format_user_display_content,
    looks_like_okr_request,
)
from ..crypto import encrypt_secret, mask_secret, decrypt_secret
from ..database import SessionLocal, get_db
from ..models import (
    MEDIA_PLATFORM_LABELS,
    MEDIA_PLATFORMS,
    ChatMessage,
    ChatSession,
    MediaAccount,
    MediaArticle,
    MediaPublishTask,
    TopicItem,
    TopicRssSource,
    User,
    now,
)
from ..security import get_current_user

router = APIRouter(prefix="/media", tags=["media"])


# ---------- schemas ----------
class AccountIn(BaseModel):
    name: str = Field(max_length=128)
    platform: str = "other"
    persona: str | None = None
    audience: str | None = None
    forbidden: str | None = None


class AccountOut(BaseModel):
    id: int
    name: str
    platform: str
    persona: str | None
    audience: str | None
    forbidden: str | None
    app_id: str | None = None
    publish_api_base: str | None = None
    token_note: str | None = None
    token_status: str = "not_configured"
    access_token_masked: str | None = None
    has_app_secret: bool = False
    has_access_token: bool = False
    has_refresh_token: bool = False
    token_updated_at: datetime | None = None

    class Config:
        from_attributes = True


class AccountTokenIn(BaseModel):
    app_id: str | None = None
    app_secret: str | None = None  # 空=不修改
    access_token: str | None = None
    refresh_token: str | None = None
    publish_api_base: str | None = None
    token_note: str | None = None
    clear_access_token: bool = False
    clear_app_secret: bool = False


class PublishIn(BaseModel):
    article_id: int
    account_id: int | None = None  # 默认用成稿所属账号


class PublishTaskOut(BaseModel):
    id: int
    account_id: int
    article_id: int
    platform: str
    status: str
    platform_post_id: str | None
    result_url: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _account_out(acc: MediaAccount) -> AccountOut:
    at = acc.access_token_encrypted or ""
    secret = acc.app_secret_encrypted or ""
    rt = acc.refresh_token_encrypted or ""
    masked = None
    if at:
        try:
            masked = mask_secret(decrypt_secret(at))
        except Exception:
            masked = "****"
    return AccountOut(
        id=acc.id,
        name=acc.name,
        platform=acc.platform,
        persona=acc.persona,
        audience=acc.audience,
        forbidden=acc.forbidden,
        app_id=acc.app_id,
        publish_api_base=acc.publish_api_base,
        token_note=acc.token_note,
        token_status=acc.token_status or "not_configured",
        access_token_masked=masked,
        has_app_secret=bool(secret),
        has_access_token=bool(at),
        has_refresh_token=bool(rt),
        token_updated_at=acc.token_updated_at,
    )


class TopicIn(BaseModel):
    title: str
    summary: str | None = None
    source: str = "manual"
    source_url: str | None = None
    platform_hint: str | None = None
    account_id: int | None = None
    score: float = 0


class TopicOut(BaseModel):
    id: int
    title: str
    summary: str | None
    source: str
    source_url: str | None
    platform_hint: str | None
    account_id: int | None
    score: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ArticleIn(BaseModel):
    account_id: int
    title: str
    content: str = ""
    tags: list[str] = []
    topic_id: int | None = None
    status: str = "draft"


class ArticleOut(BaseModel):
    id: int
    account_id: int
    topic_id: int | None
    title: str
    content: str
    tags: list
    status: str
    word_count: int
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatIn(BaseModel):
    content: str = ""
    topic_id: int | None = None
    skill_id: int | None = None
    provider_id: int | None = None
    model_id: str | None = None
    account_id: int | None = None
    mode: str = "write"  # write | brainstorm
    auto_images: bool = True  # 文案占位图自动调用图片模型填充
    # 已上传的本地图路径 /gen-assets/{uid}/xxx.png,或 data:image/...;base64,...
    images: list[str] = []


class SaveArticleFromChatIn(BaseModel):
    title: str | None = None
    topic_id: int | None = None
    status: str = "draft"


class SaveTopicsFromChatIn(BaseModel):
    account_id: int | None = None
    session_id: int | None = None
    titles: list[str] = []
    """若为空,则从最近一条助手回复里用 AI/启发式抽取标题。"""


class MediaSessionCreateIn(BaseModel):
    title: str | None = None
    account_id: int | None = None


class MediaSessionRenameIn(BaseModel):
    title: str


class FillImagesIn(BaseModel):
    message_id: int | None = None
    provider_id: int | None = None
    model_id: str | None = None


class RssIn(BaseModel):
    name: str
    url: str
    platform_hint: str | None = None


class ImportTopicsIn(BaseModel):
    items: list[TopicIn]
    account_id: int | None = None


class ExpandTopicIn(BaseModel):
    account_id: int | None = None
    count: int = 5


def _account(db: Session, user: User, account_id: int) -> MediaAccount:
    acc = db.query(MediaAccount).filter_by(id=account_id, user_id=user.id).first()
    if not acc:
        raise HTTPException(404, "账号不存在")
    return acc


# ---------- accounts ----------
@router.get("/platforms")
def list_media_platforms():
    return [{"key": k, "label": MEDIA_PLATFORM_LABELS.get(k, k)} for k in MEDIA_PLATFORMS]


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(MediaAccount).filter_by(user_id=user.id).order_by(MediaAccount.id.desc()).all()
    return [_account_out(a) for a in rows]


@router.post("/accounts", response_model=AccountOut)
def create_account(body: AccountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.platform not in MEDIA_PLATFORMS:
        raise HTTPException(400, "不支持的平台")
    acc = MediaAccount(
        user_id=user.id,
        name=body.name.strip()[:128],
        platform=body.platform,
        persona=body.persona,
        audience=body.audience,
        forbidden=body.forbidden,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return _account_out(acc)


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    body: AccountIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    acc = _account(db, user, account_id)
    if body.platform not in MEDIA_PLATFORMS:
        raise HTTPException(400, "不支持的平台")
    acc.name = body.name.strip()[:128]
    acc.platform = body.platform
    acc.persona = body.persona
    acc.audience = body.audience
    acc.forbidden = body.forbidden
    db.commit()
    db.refresh(acc)
    return _account_out(acc)


CREATOR_HOME_URLS = {
    "xhs": "https://creator.xiaohongshu.com/publish/publish",
    "wechat_mp": "https://mp.weixin.qq.com/",
    "douyin": "https://creator.douyin.com/",
    "toutiao": "https://mp.toutiao.com/",
    "other": "about:blank",
}


@router.post("/accounts/{account_id}/open-creator")
def open_creator_backend(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """在本机打开平台创作者后台(持久化浏览器目录),便于在织火流程里完成登录。

    登录态保存在本机临时目录,下次免登;发布仍需自建桥接或公众号官方 API。
    需要: pip install playwright && playwright install chromium
    """
    acc = _account(db, user, account_id)
    url = CREATOR_HOME_URLS.get(acc.platform) or "about:blank"
    label = MEDIA_PLATFORM_LABELS.get(acc.platform, acc.platform)
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return {
            "ok": False,
            "url": url,
            "message": (
                "后端未安装 playwright。可手动打开创作者后台登录,或执行:"
                "pip install playwright && playwright install chromium"
            ),
        }
    import threading
    import tempfile
    from pathlib import Path

    profile_dir = Path(tempfile.gettempdir()) / "xiaohuo_media_creator" / (acc.platform or "other")
    profile_dir.mkdir(parents=True, exist_ok=True)
    err_box: list[str] = []

    def _launch() -> None:
        try:
            from playwright.sync_api import sync_playwright as _sp

            with _sp() as p:
                ctx = p.chromium.launch_persistent_context(
                    str(profile_dir), headless=False, viewport={"width": 1440, "height": 900}
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url)
                # 保持窗口直到用户关闭(最多 8 小时)
                try:
                    page.wait_for_timeout(8 * 3600 * 1000)
                except Exception:
                    pass
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            err_box.append(str(e))

    threading.Thread(target=_launch, daemon=True).start()
    # 给启动一点时间,便于返回明确错误(Chromium 未安装等)
    import time

    time.sleep(1.2)
    if err_box:
        msg = err_box[0].splitlines()[0][:200]
        if "Executable doesn't exist" in err_box[0] or "browserType.launch" in msg:
            return {
                "ok": False,
                "url": url,
                "message": "未找到 Chromium,请执行: playwright install chromium",
            }
        return {"ok": False, "url": url, "message": f"打开失败:{msg}"}
    return {
        "ok": True,
        "url": url,
        "message": (
            f"已打开「{label}」创作者后台。请在弹出的浏览器里完成登录;"
            "登录态已持久化。一键发布请另配发布 API 地址(自建桥接)或公众号 AppSecret。"
        ),
    }


@router.put("/accounts/{account_id}/token", response_model=AccountOut)
def update_account_token(
    account_id: int,
    body: AccountTokenIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """配置一键发布凭证(Token / AppSecret 加密存储)。"""
    acc = _account(db, user, account_id)
    if body.app_id is not None:
        acc.app_id = (body.app_id or "").strip()[:128] or None
    if body.publish_api_base is not None:
        acc.publish_api_base = (body.publish_api_base or "").strip()[:500] or None
    if body.token_note is not None:
        acc.token_note = (body.token_note or "").strip()[:255] or None
    if body.clear_app_secret:
        acc.app_secret_encrypted = None
    elif body.app_secret:
        acc.app_secret_encrypted = encrypt_secret(body.app_secret.strip())
    if body.clear_access_token:
        acc.access_token_encrypted = None
    elif body.access_token:
        acc.access_token_encrypted = encrypt_secret(body.access_token.strip())
    if body.refresh_token:
        acc.refresh_token_encrypted = encrypt_secret(body.refresh_token.strip())
    acc.token_status = "ready" if has_publish_credentials(acc) else "not_configured"
    acc.token_updated_at = now()
    db.commit()
    db.refresh(acc)
    return _account_out(acc)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acc = _account(db, user, account_id)
    db.delete(acc)
    db.commit()
    return {"ok": True}


@router.post("/publish", response_model=PublishTaskOut)
async def one_click_publish(
    body: PublishIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """成稿一键发布到账号绑定平台(使用已存 Token)。"""
    art = db.query(MediaArticle).filter_by(id=body.article_id, user_id=user.id).first()
    if art is None:
        raise HTTPException(404, "成稿不存在")
    account_id = body.account_id or art.account_id
    acc = _account(db, user, account_id)
    if not (art.title or "").strip() or not (art.content or "").strip():
        raise HTTPException(400, "成稿标题或正文为空,无法发布")

    task = MediaPublishTask(
        user_id=user.id,
        account_id=acc.id,
        article_id=art.id,
        platform=acc.platform,
        status="publishing",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    result = await publish_article(acc, art)
    task.status = result.status
    task.platform_post_id = result.platform_post_id
    task.result_url = result.result_url
    task.error = (result.error or "")[:800] or None
    task.raw_response = result.raw_response
    if result.status == "success" and art.status == "draft":
        art.status = "ready"
    db.commit()
    db.refresh(task)
    return task


@router.get("/publish/tasks", response_model=list[PublishTaskOut])
def list_publish_tasks(
    account_id: int | None = None,
    article_id: int | None = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(MediaPublishTask).filter_by(user_id=user.id)
    if account_id:
        q = q.filter_by(account_id=account_id)
    if article_id:
        q = q.filter_by(article_id=article_id)
    return q.order_by(MediaPublishTask.id.desc()).limit(min(limit, 200)).all()


# ---------- topics ----------
@router.get("/topics", response_model=list[TopicOut])
def list_topics(
    account_id: int | None = None,
    status: str = "active",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(TopicItem).filter_by(user_id=user.id)
    if status:
        q = q.filter(TopicItem.status == status)
    if account_id:
        q = q.filter((TopicItem.account_id == account_id) | (TopicItem.account_id.is_(None)))
    return q.order_by(TopicItem.score.desc(), TopicItem.id.desc()).limit(200).all()


@router.post("/topics", response_model=TopicOut)
def create_topic(body: TopicIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.account_id:
        _account(db, user, body.account_id)
    t = TopicItem(
        user_id=user.id,
        account_id=body.account_id,
        title=body.title.strip()[:255],
        summary=body.summary,
        source=body.source or "manual",
        source_url=body.source_url,
        platform_hint=body.platform_hint,
        score=body.score,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.post("/topics/import")
def import_topics(body: ImportTopicsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.account_id:
        _account(db, user, body.account_id)
    created = 0
    for item in body.items[:500]:
        db.add(
            TopicItem(
                user_id=user.id,
                account_id=body.account_id or item.account_id,
                title=item.title.strip()[:255],
                summary=item.summary,
                source=item.source or "import",
                source_url=item.source_url,
                platform_hint=item.platform_hint,
                score=item.score,
            )
        )
        created += 1
    db.commit()
    return {"ok": True, "created": created}


@router.put("/topics/{topic_id}/status")
def set_topic_status(
    topic_id: int,
    status: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if status not in ("active", "used", "ignored"):
        raise HTTPException(400, "状态无效")
    t = db.query(TopicItem).filter_by(id=topic_id, user_id=user.id).first()
    if not t:
        raise HTTPException(404, "选题不存在")
    t.status = status
    db.commit()
    return {"ok": True}


@router.post("/topics/{topic_id}/expand")
def expand_topic(
    topic_id: int,
    body: ExpandTopicIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.query(TopicItem).filter_by(id=topic_id, user_id=user.id).first()
    if not t:
        raise HTTPException(404, "选题不存在")
    persona = ""
    if body.account_id:
        acc = _account(db, user, body.account_id)
        persona = acc.persona or ""
    purpose = resolve_any_purpose(db, user.id, ["media.topic_expand", "topic_expand", "chat", "novel.chat", "outline"])
    count = max(1, min(body.count, 10))
    data = extract_json_object(
        db,
        user.id,
        (
            "你是选题策划。根据原始选题与账号人设,输出 JSON:"
            '{"angles":[{"title":"标题","summary":"一句话角度"}]}。'
            f"生成 {count} 个差异化角度。只输出 JSON。"
        ),
        f"## 原选题\n{t.title}\n{t.summary or ''}\n\n## 人设\n{persona or '通用自媒体'}",
    )
    angles = data.get("angles") or []
    created = []
    for a in angles[:count]:
        title = str(a.get("title") or "").strip()
        if not title:
            continue
        row = TopicItem(
            user_id=user.id,
            account_id=body.account_id or t.account_id,
            title=title[:255],
            summary=str(a.get("summary") or "")[:500] or None,
            source="ai",
            platform_hint=t.platform_hint,
            score=t.score,
        )
        db.add(row)
        created.append(title)
    db.commit()
    return {"ok": True, "created": len(created), "titles": created, "purpose": purpose}


# ---------- RSS ----------
@router.get("/rss")
def list_rss(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(TopicRssSource).filter_by(user_id=user.id).order_by(TopicRssSource.id.desc()).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "url": r.url,
            "platform_hint": r.platform_hint,
            "enabled": r.enabled,
            "last_fetched_at": r.last_fetched_at,
        }
        for r in rows
    ]


@router.post("/rss")
def create_rss(body: RssIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = TopicRssSource(user_id=user.id, name=body.name[:128], url=body.url[:500], platform_hint=body.platform_hint)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "url": row.url}


@router.post("/rss/{rss_id}/fetch")
def fetch_rss(rss_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    src = db.query(TopicRssSource).filter_by(id=rss_id, user_id=user.id).first()
    if not src:
        raise HTTPException(404, "RSS 不存在")
    try:
        resp = httpx.get(src.url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        raise HTTPException(400, f"拉取失败:{e}") from e

    items = []
    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if title:
            items.append((title, link, desc))
    # Atom
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            desc = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()
            if title:
                items.append((title, link, desc))

    created = 0
    for title, link, desc in items[:50]:
        exists = (
            db.query(TopicItem)
            .filter_by(user_id=user.id, title=title[:255], source="rss")
            .first()
        )
        if exists:
            continue
        db.add(
            TopicItem(
                user_id=user.id,
                title=title[:255],
                summary=(desc[:500] if desc else None),
                source="rss",
                source_url=link[:500] if link else None,
                platform_hint=src.platform_hint,
            )
        )
        created += 1
    src.last_fetched_at = now()
    db.commit()
    return {"ok": True, "created": created, "seen": len(items)}


# ---------- articles ----------
@router.get("/articles", response_model=list[ArticleOut])
def list_articles(
    account_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(MediaArticle).filter_by(user_id=user.id)
    if account_id:
        q = q.filter_by(account_id=account_id)
    return q.order_by(MediaArticle.updated_at.desc()).limit(100).all()


@router.post("/articles", response_model=ArticleOut)
def create_article(body: ArticleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _account(db, user, body.account_id)
    art = MediaArticle(
        user_id=user.id,
        account_id=body.account_id,
        topic_id=body.topic_id,
        title=body.title.strip()[:255],
        content=body.content or "",
        tags=body.tags or [],
        status=body.status if body.status in ("draft", "ready", "archived") else "draft",
        word_count=len((body.content or "").strip()),
    )
    db.add(art)
    db.commit()
    db.refresh(art)
    return art


@router.put("/articles/{article_id}", response_model=ArticleOut)
def update_article(
    article_id: int,
    body: ArticleIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    art = db.query(MediaArticle).filter_by(id=article_id, user_id=user.id).first()
    if not art:
        raise HTTPException(404, "成稿不存在")
    art.title = body.title.strip()[:255]
    art.content = body.content or ""
    art.tags = body.tags or []
    art.topic_id = body.topic_id
    art.status = body.status if body.status in ("draft", "ready", "archived") else art.status
    art.word_count = len(art.content.strip())
    db.commit()
    db.refresh(art)
    return art


@router.delete("/articles/{article_id}")
def delete_article(article_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    art = db.query(MediaArticle).filter_by(id=article_id, user_id=user.id).first()
    if not art:
        raise HTTPException(404, "成稿不存在")
    db.delete(art)
    db.commit()
    return {"ok": True}


# ---------- chat ----------
GENERAL_MEDIA_WORKSPACE = 0  # 不绑账号的选题/灵感会话


def _media_session(db: Session, user: User, workspace_id: int, title: str | None = None) -> ChatSession:
    """账号工作台仍「一账号一会话」;通用多会话请用 /media/sessions。"""
    sess = (
        db.query(ChatSession)
        .filter_by(user_id=user.id, product_key="media", workspace_id=workspace_id)
        .order_by(ChatSession.id.desc())
        .first()
    )
    if sess:
        return sess
    sess = ChatSession(
        user_id=user.id,
        product_key="media",
        workspace_id=workspace_id,
        title=(title or "自媒体聊天")[:64],
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def _get_media_chat_session(db: Session, user: User, session_id: int) -> ChatSession:
    sess = (
        db.query(ChatSession)
        .filter_by(id=session_id, user_id=user.id, product_key="media")
        .first()
    )
    if sess is None:
        raise HTTPException(404, "对话不存在")
    return sess


def _session_item(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "updated_at": s.updated_at,
        "skill_id": s.skill_id,
        "workspace_id": s.workspace_id,
        "account_id": s.workspace_id if s.workspace_id and s.workspace_id != GENERAL_MEDIA_WORKSPACE else None,
    }


def _chat_payload(sess: ChatSession, db: Session) -> dict:
    msgs = (
        db.query(ChatMessage)
        .filter_by(session_id=sess.id)
        .order_by(ChatMessage.id.asc())
        .limit(200)
        .all()
    )
    return {
        "session_id": sess.id,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "model_name": m.model_name,
            }
            for m in msgs
            if m.role in ("user", "assistant")
        ],
    }


async def _stream_media_chat(
    *,
    user: User,
    sess: ChatSession,
    body: ChatIn,
    account: MediaAccount | None,
    mode: str,
    workspace_id: int,
):
    topic_hint = ""
    if body.topic_id:
        with SessionLocal() as db0:
            t = db0.query(TopicItem).filter_by(id=body.topic_id, user_id=user.id).first()
            if t:
                topic_hint = f"\n\n【当前选题】{t.title}\n{t.summary or ''}"
                t.status = "used"
                db0.commit()

    image_urls = [u.strip() for u in (body.images or []) if u and u.strip()][:8]
    # data URL 先落盘,便于预览与复用
    normalized_images: list[str] = []
    for u in image_urls:
        if u.startswith("data:image/"):
            try:
                from ..ai.media_gen import save_data_url

                normalized_images.append(save_data_url(user.id, u, "png"))
            except Exception:
                continue
        elif u.startswith("/gen-assets/"):
            normalized_images.append(u)
        elif u.startswith("http://") or u.startswith("https://"):
            normalized_images.append(u)

    raw_text = (body.content or "").strip()
    if not raw_text and not normalized_images:
        raise HTTPException(400, "请输入文字或上传图片")
    if not raw_text and normalized_images:
        raw_text = "请识别并分析这些图片。若含 OKR / 目标与关键结果,请结构化整理。"

    user_text = raw_text + topic_hint
    display_content = format_user_display_content(user_text, normalized_images)
    session_id = sess.id
    db = SessionLocal()
    try:
        sess_row = db.get(ChatSession, session_id)
        if sess_row is None:
            raise HTTPException(404, "对话不存在")
        user_msg = ChatMessage(session_id=session_id, role="user", content=display_content)
        db.add(user_msg)
        if body.skill_id is not None:
            sess_row.skill_id = body.skill_id
        if sess_row.title in ("", "新对话", "选题灵感", "自媒体聊天") and not topic_hint:
            title_src = body.content.strip() or ("图片识别" if normalized_images else "新对话")
            sess_row.title = title_src[:20] or sess_row.title
        db.commit()
        db.refresh(user_msg)
        last_user_id = user_msg.id
        purpose = resolve_any_purpose(
            db,
            user.id,
            ["media.chat", "media.topic_expand", "chat", "novel.chat", "outline", "chapter"],
        )
        modality = resolve_chat_modality(db, user.id, purpose, body.provider_id, body.model_id)
        messages = None
        # 有上传图时强制走文本/视觉对话,不走纯生图接口
        if normalized_images:
            modality = "text"
        elif modality == "text" and is_pure_image_request(body.content.strip()):
            modality = "image"
        elif modality == "text" and is_pure_video_request(body.content.strip()):
            modality = "video"
        if modality == "text":
            system = build_media_system_prompt(
                db,
                user.id,
                account,
                user_message=user_text,
                extra_skill_id=body.skill_id,
                mode=mode,
            )
            if normalized_images or looks_like_okr_request(user_text):
                system = f"{system}\n\n{VISION_OKR_PROMPT}"
            history = (
                db.query(ChatMessage)
                .filter_by(session_id=session_id)
                .order_by(ChatMessage.id.desc())
                .limit(30)
                .all()
            )
            history = list(reversed(history))
            messages = [{"role": "system", "content": system}]
            for m in history:
                if m.role not in ("user", "assistant"):
                    continue
                if m.id == last_user_id and normalized_images:
                    messages.append(
                        {
                            "role": "user",
                            "content": build_vision_user_content(
                                user_text, normalized_images, user.id
                            ),
                        }
                    )
                else:
                    messages.append({"role": m.role, "content": m.content})
    finally:
        db.close()

    user_id = user.id
    prompt = raw_text
    auto_images = body.auto_images if body.auto_images is not None else True

    async def gen():
        full: list[str] = []
        model_name = None
        usage = None
        s = SessionLocal()
        try:
            if modality in ("image", "video"):
                stream = stream_media_generation(
                    s,
                    user_id,
                    purpose,
                    prompt,
                    provider_id=body.provider_id,
                    model_id=body.model_id,
                    modality=modality,
                )
            else:
                stream = stream_chat(
                    s,
                    user_id,
                    None,
                    purpose,
                    messages or [],
                    provider_id=body.provider_id,
                    model_id=body.model_id,
                    product_key="media",
                    workspace_id=workspace_id,
                )
            async for event in stream:
                et = event.get("type")
                if et == "delta":
                    full.append(event.get("content") or "")
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif et == "model":
                    model_name = event.get("model_name") or event.get("model")
                    yield f"data: {json.dumps({**event, 'model_name': model_name}, ensure_ascii=False)}\n\n"
                elif et == "usage":
                    usage = event
                elif et in ("reasoning", "notice", "image", "video", "media_status", "content_replace"):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            text = "".join(full).strip()

            # 文本成稿后:自动把 ![说明](images/xxx.png) 占位换成真实图片
            if (
                modality == "text"
                and auto_images
                and text
                and extract_image_placeholders(text)
            ):
                async for ev in autofill_placeholders_with_images(
                    s,
                    user_id,
                    purpose,
                    text,
                    provider_id=body.provider_id,
                    model_id=body.model_id,
                ):
                    et = ev.get("type")
                    if et == "content_replace" and ev.get("content"):
                        text = str(ev["content"])
                    if et == "usage":
                        # 保留文本用量为主,图片用量另记一条
                        try:
                            log_usage(
                                s,
                                user_id,
                                None,
                                purpose,
                                ev,
                                product_key="media",
                                workspace_id=workspace_id,
                            )
                        except Exception:
                            pass
                    else:
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

            if text:
                s.add(
                    ChatMessage(
                        session_id=session_id,
                        role="assistant",
                        content=text,
                        model_name=model_name,
                    )
                )
                sess2 = s.get(ChatSession, session_id)
                if sess2:
                    sess2.updated_at = now()
                s.commit()
            if usage:
                log_usage(
                    s,
                    user_id,
                    None,
                    purpose,
                    usage,
                    product_key="media",
                    workspace_id=workspace_id,
                )
            yield f"data: {json.dumps({'type': 'done', 'content': text}, ensure_ascii=False)}\n\n"
        except Exception as e:
            detail = getattr(e, "detail", None) or str(e)
            try:
                log_call_error(
                    s,
                    user_id,
                    purpose,
                    str(detail),
                    product_key="media",
                    workspace_id=workspace_id,
                    provider_name=None,
                    model_name=body.model_id,
                    status="failed",
                )
            except Exception:
                pass
            yield f"data: {json.dumps({'type': 'error', 'detail': str(detail)}, ensure_ascii=False)}\n\n"
        finally:
            s.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/fill-images")
async def fill_session_images(
    session_id: int,
    body: FillImagesIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把某条助手消息(默认最近一条)里的配图占位自动生成并写回,SSE 推送预览。"""
    sess = _get_media_chat_session(db, user, session_id)
    message_id = body.message_id if body else None
    provider_id = body.provider_id if body else None
    model_id = body.model_id if body else None

    q = db.query(ChatMessage).filter_by(session_id=sess.id, role="assistant")
    if message_id:
        row = q.filter_by(id=int(message_id)).first()
    else:
        row = q.order_by(ChatMessage.id.desc()).first()
    if row is None or not (row.content or "").strip():
        raise HTTPException(400, "没有可填充配图的助手消息")
    if not extract_image_placeholders(row.content):
        raise HTTPException(400, "该消息没有待生成的配图占位")

    purpose = resolve_any_purpose(
        db,
        user.id,
        ["media.chat", "media.topic_expand", "chat", "novel.chat", "outline", "chapter"],
    )
    user_id = user.id
    workspace_id = sess.workspace_id if sess.workspace_id is not None else GENERAL_MEDIA_WORKSPACE
    original = row.content
    mid = row.id

    async def gen():
        text = original
        s = SessionLocal()
        try:
            yield f"data: {json.dumps({'type': 'media_status', 'detail': '开始生成全部配图…'}, ensure_ascii=False)}\n\n"
            async for ev in autofill_placeholders_with_images(
                s,
                user_id,
                purpose,
                text,
                provider_id=provider_id,
                model_id=model_id,
                max_images=8,
            ):
                et = ev.get("type")
                if et == "content_replace" and ev.get("content"):
                    text = str(ev["content"])
                if et == "usage":
                    try:
                        log_usage(
                            s,
                            user_id,
                            None,
                            purpose,
                            ev,
                            product_key="media",
                            workspace_id=workspace_id,
                        )
                    except Exception:
                        pass
                else:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            msg = s.get(ChatMessage, mid)
            if msg:
                msg.content = text
                sess2 = s.get(ChatSession, session_id)
                if sess2:
                    sess2.updated_at = now()
                s.commit()
            yield f"data: {json.dumps({'type': 'done', 'content': text, 'message_id': mid}, ensure_ascii=False)}\n\n"
        except Exception as e:
            detail = getattr(e, "detail", None) or str(e)
            yield f"data: {json.dumps({'type': 'error', 'detail': str(detail)}, ensure_ascii=False)}\n\n"
        finally:
            s.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/upload-image")
async def upload_chat_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """对话图片上传,返回可预览的 /gen-assets/... 路径。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "图片过大(上限 12MB)")
    content_type = (file.content_type or "").lower()
    name = (file.filename or "upload.png").lower()
    if content_type not in ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif") and not name.endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif")
    ):
        raise HTTPException(400, "仅支持 png / jpg / webp / gif")
    ext = "png"
    if "jpeg" in content_type or "jpg" in content_type or name.endswith((".jpg", ".jpeg")):
        ext = "jpg"
    elif "webp" in content_type or name.endswith(".webp"):
        ext = "webp"
    elif "gif" in content_type or name.endswith(".gif"):
        ext = "gif"
    url = save_bytes(user.id, raw, ext)
    return {"ok": True, "url": url, "bytes": len(raw)}


@router.get("/sessions")
def list_media_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """自媒体多会话列表(含不绑账号与绑账号)。"""
    rows = (
        db.query(ChatSession)
        .filter_by(user_id=user.id, product_key="media")
        .order_by(ChatSession.updated_at.desc())
        .limit(100)
        .all()
    )
    return [_session_item(s) for s in rows]


@router.post("/sessions")
def create_media_session(
    body: MediaSessionCreateIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account_id = body.account_id if body else None
    workspace_id = GENERAL_MEDIA_WORKSPACE
    title = (body.title if body and body.title else "新对话")[:64]
    if account_id:
        acc = _account(db, user, account_id)
        workspace_id = acc.id
        if not (body and body.title):
            title = f"{acc.name}创作"[:64]
    sess = ChatSession(
        user_id=user.id,
        product_key="media",
        workspace_id=workspace_id,
        title=title,
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return _session_item(sess)


@router.put("/sessions/{session_id}")
def rename_media_session(
    session_id: int,
    body: MediaSessionRenameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sess = _get_media_chat_session(db, user, session_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    sess.title = title[:64]
    db.commit()
    return {"ok": True, "title": sess.title}


@router.delete("/sessions/{session_id}")
def delete_media_session(
    session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    sess = _get_media_chat_session(db, user, session_id)
    db.delete(sess)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def list_media_session_messages(
    session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    sess = _get_media_chat_session(db, user, session_id)
    return _chat_payload(sess, db)["messages"]


@router.post("/sessions/{session_id}/chat")
async def media_session_chat(
    session_id: int,
    body: ChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not (body.content or "").strip() and not (body.images or []):
        raise HTTPException(400, "请输入文字或上传图片")
    sess = _get_media_chat_session(db, user, session_id)
    account = None
    account_id = body.account_id
    if account_id is None and sess.workspace_id and sess.workspace_id != GENERAL_MEDIA_WORKSPACE:
        account_id = sess.workspace_id
    if account_id:
        account = _account(db, user, account_id)
    mode = body.mode if body.mode in ("write", "brainstorm") else ("write" if account else "brainstorm")
    if account is None:
        mode = "brainstorm"
    return await _stream_media_chat(
        user=user,
        sess=sess,
        body=body,
        account=account,
        mode=mode,
        workspace_id=sess.workspace_id if sess.workspace_id is not None else GENERAL_MEDIA_WORKSPACE,
    )


@router.get("/chat")
def get_general_media_chat(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """兼容旧入口:返回最近一条通用会话(无则创建)。"""
    sess = (
        db.query(ChatSession)
        .filter_by(user_id=user.id, product_key="media", workspace_id=GENERAL_MEDIA_WORKSPACE)
        .order_by(ChatSession.updated_at.desc())
        .first()
    )
    if sess is None:
        sess = _media_session(db, user, GENERAL_MEDIA_WORKSPACE, title="选题灵感")
    return _chat_payload(sess, db)


@router.post("/chat")
async def general_media_chat(
    body: ChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """兼容旧入口:写入最近通用会话。"""
    if not (body.content or "").strip() and not (body.images or []):
        raise HTTPException(400, "请输入文字或上传图片")
    account = None
    if body.account_id:
        account = _account(db, user, body.account_id)
    sess = (
        db.query(ChatSession)
        .filter_by(user_id=user.id, product_key="media", workspace_id=GENERAL_MEDIA_WORKSPACE)
        .order_by(ChatSession.updated_at.desc())
        .first()
    )
    if sess is None:
        sess = _media_session(db, user, GENERAL_MEDIA_WORKSPACE, title="选题灵感")
    mode = body.mode if body.mode in ("write", "brainstorm") else "brainstorm"
    if account is None:
        mode = "brainstorm"
    return await _stream_media_chat(
        user=user,
        sess=sess,
        body=body,
        account=account,
        mode=mode,
        workspace_id=GENERAL_MEDIA_WORKSPACE,
    )


@router.post("/chat/save-topics")
def save_topics_from_general_chat(
    body: SaveTopicsFromChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把聊天里的选题标题批量写入选题库(可不绑账号)。"""
    if body.account_id:
        _account(db, user, body.account_id)
    titles = [t.strip() for t in (body.titles or []) if t and t.strip()]
    if not titles:
        if body.session_id:
            sess = _get_media_chat_session(db, user, body.session_id)
        else:
            sess = _media_session(db, user, GENERAL_MEDIA_WORKSPACE, title="选题灵感")
        last = (
            db.query(ChatMessage)
            .filter_by(session_id=sess.id, role="assistant")
            .order_by(ChatMessage.id.desc())
            .first()
        )
        if not last or not (last.content or "").strip():
            raise HTTPException(400, "没有可提取的助手回复,请先聊天或手动传入 titles")
        data = extract_json_object(
            db,
            user.id,
            '从助手回复中提取选题。只输出 JSON:{"items":[{"title":"标题","summary":"一句话角度"}]}。最多 12 条。',
            last.content[:6000],
        )
        created_titles = []
        for item in data.get("items") or []:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            created_titles.append(title)
            db.add(
                TopicItem(
                    user_id=user.id,
                    account_id=body.account_id,
                    title=title[:255],
                    summary=(str(item.get("summary") or "").strip()[:500] or None),
                    source="ai",
                )
            )
        if not created_titles:
            raise HTTPException(400, "未能从回复中解析出选题")
        db.commit()
        return {"ok": True, "created": len(created_titles), "titles": created_titles}

    for title in titles[:30]:
        db.add(
            TopicItem(
                user_id=user.id,
                account_id=body.account_id,
                title=title[:255],
                source="ai",
            )
        )
    db.commit()
    return {"ok": True, "created": len(titles[:30]), "titles": titles[:30]}


@router.get("/accounts/{account_id}/chat")
def get_media_chat(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acc = _account(db, user, account_id)
    sess = _media_session(db, user, account_id, title=f"{acc.name}创作")
    return _chat_payload(sess, db)


@router.post("/accounts/{account_id}/chat")
async def media_chat(
    account_id: int,
    body: ChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    acc = _account(db, user, account_id)
    if not (body.content or "").strip() and not (body.images or []):
        raise HTTPException(400, "请输入文字或上传图片")
    sess = _media_session(db, user, account_id, title=f"{acc.name}创作")
    mode = body.mode if body.mode in ("write", "brainstorm") else "write"
    return await _stream_media_chat(
        user=user,
        sess=sess,
        body=body,
        account=acc,
        mode=mode,
        workspace_id=account_id,
    )


@router.post("/accounts/{account_id}/chat/save-article", response_model=ArticleOut)
def save_article_from_chat(
    account_id: int,
    body: SaveArticleFromChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _account(db, user, account_id)
    sess = _media_session(db, user, account_id)
    last = (
        db.query(ChatMessage)
        .filter_by(session_id=sess.id, role="assistant")
        .order_by(ChatMessage.id.desc())
        .first()
    )
    if not last or not (last.content or "").strip():
        raise HTTPException(400, "没有可保存的助手回复")
    content = last.content.strip()
    title = (body.title or "").strip()
    if not title:
        title = content.split("\n", 1)[0].strip()[:80] or "未命名成稿"
    art = MediaArticle(
        user_id=user.id,
        account_id=account_id,
        topic_id=body.topic_id,
        title=title[:255],
        content=content,
        status=body.status if body.status in ("draft", "ready", "archived") else "draft",
        word_count=len(content),
    )
    db.add(art)
    db.commit()
    db.refresh(art)
    return art
