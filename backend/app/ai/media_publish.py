"""自媒体一键发布适配器。

优先走账号配置的 publish_api_base(自建桥接/开放平台网关);
公众号在填写 app_id + app_secret(或 access_token) 时走微信草稿箱 API;
其余平台无可用凭证时返回 need_manual,提示去配置 Token。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from ..crypto import decrypt_secret
from ..models import MEDIA_PLATFORM_LABELS, MediaAccount, MediaArticle


@dataclass
class PublishResult:
    status: str  # success / failed / need_manual
    platform_post_id: str | None = None
    result_url: str | None = None
    error: str | None = None
    raw_response: str | None = None


def _plain(account: MediaAccount, field: str) -> str:
    enc = getattr(account, field, None) or ""
    if not enc:
        return ""
    try:
        return decrypt_secret(enc)
    except Exception:
        return ""


def has_publish_credentials(account: MediaAccount) -> bool:
    return bool(
        _plain(account, "access_token_encrypted")
        or (_plain(account, "app_secret_encrypted") and (account.app_id or "").strip())
        or (account.publish_api_base or "").strip()
    )


def _strip_md(text: str) -> str:
    t = text or ""
    t = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"[*`#_>~]", "", t)
    return t.strip()


def _md_to_html(text: str) -> str:
    lines = (text or "").splitlines()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            out.append("<p><br/></p>")
            continue
        if s.startswith("### "):
            out.append(f"<h3>{s[4:]}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2>{s[3:]}</h2>")
        elif s.startswith("# "):
            out.append(f"<h2>{s[2:]}</h2>")
        else:
            s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
            s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1"/>', s)
            out.append(f"<p>{s}</p>")
    return "\n".join(out) or "<p></p>"


async def _wechat_access_token(account: MediaAccount) -> str:
    token = _plain(account, "access_token_encrypted")
    if token:
        return token
    app_id = (account.app_id or "").strip()
    secret = _plain(account, "app_secret_encrypted")
    if not app_id or not secret:
        raise ValueError("公众号需配置 AppID + AppSecret,或直接填写 access_token")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={"grant_type": "client_credential", "appid": app_id, "secret": secret},
        )
    data = resp.json()
    if data.get("access_token"):
        return str(data["access_token"])
    raise ValueError(f"获取公众号 access_token 失败:{data.get('errmsg') or data}")


async def publish_wechat_mp(account: MediaAccount, article: MediaArticle) -> PublishResult:
    try:
        access_token = await _wechat_access_token(account)
    except ValueError as e:
        return PublishResult(status="need_manual", error=str(e))

    body = {
        "articles": [
            {
                "title": (article.title or "未命名")[:64],
                "author": (account.name or "")[:20],
                "digest": _strip_md(article.content)[:120],
                "content": _md_to_html(article.content),
                "content_source_url": "",
                "need_open_comment": 0,
                "only_fans_can_comment": 0,
            }
        ]
    }
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=body)
    try:
        data = resp.json()
    except ValueError:
        return PublishResult(
            status="failed",
            error=f"微信响应非 JSON HTTP {resp.status_code}",
            raw_response=resp.text[:800],
        )
    mid = data.get("media_id")
    if mid:
        return PublishResult(
            status="success",
            platform_post_id=str(mid),
            result_url="https://mp.weixin.qq.com/",
            raw_response=json.dumps(data, ensure_ascii=False)[:2000],
        )
    err = data.get("errmsg") or f"HTTP {resp.status_code}"
    code = data.get("errcode")
    hint = f"微信草稿箱失败[{code}]:{err}"
    if code in (40001, 42001, 41001):
        hint += "。Token 无效或过期,请更新 AppSecret / access_token"
    return PublishResult(
        status="failed",
        error=hint,
        raw_response=json.dumps(data, ensure_ascii=False)[:2000],
    )


async def publish_via_gateway(account: MediaAccount, article: MediaArticle) -> PublishResult:
    base = (account.publish_api_base or "").strip().rstrip("/")
    if not base:
        return PublishResult(status="need_manual", error="未配置发布 API 地址")
    token = _plain(account, "access_token_encrypted")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "platform": account.platform,
        "account_name": account.name,
        "title": article.title,
        "content": article.content,
        "content_text": _strip_md(article.content),
        "tags": article.tags or [],
        "word_count": article.word_count,
    }
    url = base if base.rstrip("/").endswith("publish") or "/publish?" in base else f"{base}/publish"
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as e:
        return PublishResult(status="failed", error=f"网关网络错误:{e.__class__.__name__}")
    raw = (resp.text or "")[:2000]
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": raw}
    if resp.status_code in (200, 201, 202) and data.get("ok", True) is not False:
        return PublishResult(
            status="success",
            platform_post_id=str(data.get("id") or data.get("post_id") or data.get("media_id") or "") or None,
            result_url=data.get("url") or data.get("result_url"),
            raw_response=raw,
        )
    return PublishResult(
        status="failed",
        error=f"网关 HTTP {resp.status_code}: {data.get('error') or data.get('message') or raw[:200]}",
        raw_response=raw,
    )


async def publish_article(account: MediaAccount, article: MediaArticle) -> PublishResult:
    platform = account.platform or "other"
    label = MEDIA_PLATFORM_LABELS.get(platform, platform)

    if (account.publish_api_base or "").strip():
        return await publish_via_gateway(account, article)

    if platform == "wechat_mp":
        return await publish_wechat_mp(account, article)

    if not has_publish_credentials(account):
        return PublishResult(
            status="need_manual",
            error=(
                f"「{label}」尚未配置发布凭证。请到账号编辑页填写 access_token,"
                "或配置自定义发布 API 地址(自建桥接/开放平台网关)后重试。"
            ),
        )

    return PublishResult(
        status="need_manual",
        error=(
            f"「{label}」已保存 Token,但官方开放接口因平台限制无法统一直连。"
            "请填写「发布 API 地址」指向自建桥接(POST /publish,Bearer Token),"
            "即可完成一键发布。"
        ),
    )
