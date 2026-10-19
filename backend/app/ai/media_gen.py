"""图片/视频生成:走 OpenAI 兼容供应商接口,落盘后返回可预览 URL。"""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
import httpx
from fastapi import HTTPException

from ..config import settings
from ..crypto import decrypt_secret
from .gateway import RouteTarget, _provider_base, join_provider_url, resolve_route_or_override
from .modality import infer_modality

GEN_ROOT = Path(settings.skills_root).resolve().parent / "gen"


def asset_dir(user_id: int) -> Path:
    d = GEN_ROOT / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def public_asset_path(user_id: int, filename: str) -> str:
    """写入消息里的相对路径,前端拼 API + token。"""
    return f"/gen-assets/{user_id}/{filename}"


def _guess_ext(url_or_mime: str, default: str) -> str:
    lower = (url_or_mime or "").lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov"):
        if ext in lower:
            return ext.lstrip(".")
    if "jpeg" in lower or "jpg" in lower:
        return "jpg"
    if "webp" in lower:
        return "webp"
    if "gif" in lower:
        return "gif"
    if "mp4" in lower:
        return "mp4"
    if "webm" in lower:
        return "webm"
    return default


def save_bytes(user_id: int, data: bytes, ext: str) -> str:
    name = f"{uuid.uuid4().hex}.{ext.lstrip('.')}"
    path = asset_dir(user_id) / name
    path.write_bytes(data)
    return public_asset_path(user_id, name)


def save_data_url(user_id: int, data_url: str, default_ext: str = "png") -> str:
    # data:image/png;base64,xxxx
    m = re.match(r"data:([^;]+);base64,(.+)", data_url, re.I | re.S)
    if not m:
        raise HTTPException(502, "无法解析 data URL")
    mime, b64 = m.group(1), m.group(2)
    ext = _guess_ext(mime, default_ext)
    return save_bytes(user_id, base64.b64decode(b64), ext)


async def _download_to_asset(client: httpx.AsyncClient, user_id: int, url: str, default_ext: str) -> str:
    resp = await client.get(url, follow_redirects=True)
    if resp.status_code != 200:
        # 远程 URL 仍可直接预览
        return url
    ext = _guess_ext(resp.headers.get("content-type", "") or url, default_ext)
    return save_bytes(user_id, resp.content, ext)


def _extract_urls(payload: Any) -> list[str]:
    """从各种供应商响应里抠出 url / b64_json / video_url。"""
    found: list[str] = []

    def walk(obj: Any):
        if obj is None:
            return
        if isinstance(obj, str):
            s = obj.strip()
            if s.startswith("data:") or s.startswith("http://") or s.startswith("https://"):
                found.append(s)
            return
        if isinstance(obj, dict):
            for key in (
                "url",
                "video_url",
                "image_url",
                "file_url",
                "output_url",
                "download_url",
                "b64_json",
                "b64",
                "base64",
            ):
                if key in obj and obj[key]:
                    val = obj[key]
                    if key in ("b64_json", "b64", "base64") and isinstance(val, str) and not val.startswith("data:"):
                        found.append(f"data:image/png;base64,{val}")
                    else:
                        walk(val)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    walk(v)
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(payload)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _task_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    # Agnes 等:优先 video_id;OpenAI Videos 兼容里 id 也可能是任务号
    for key in ("video_id", "videoId", "id", "task_id", "taskId", "request_id", "requestId"):
        v = payload.get(key)
        if isinstance(v, (str, int)) and str(v) and str(v) not in ("0",):
            # 避免把 data 列表里的 id 误认为任务
            if key == "id" and isinstance(payload.get("data"), list):
                continue
            return str(v)
    data = payload.get("data")
    if isinstance(data, dict):
        return _task_id(data)
    return None


def _provider_api_root(base_url: str) -> str:
    """https://host/v1 -> https://host (Agnes 轮询 /agnesapi 在根域)。"""
    from .gateway import normalize_provider_base
    import re

    base = normalize_provider_base(base_url)
    return re.sub(r"/v\d+$", "", base, flags=re.I)


def _status_done(payload: Any) -> bool | None:
    """True=完成 False=失败 None=进行中/未知。"""
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or payload.get("state") or "").lower()
    if status in ("succeeded", "success", "completed", "complete", "done", "finished"):
        return True
    if status in ("failed", "error", "cancelled", "canceled"):
        return False
    if status in ("queued", "pending", "running", "processing", "in_progress", "submitted"):
        return None
    return None


def _payload_has_ref_image(body: dict) -> bool:
    return any(k in body for k in ("image", "image_url", "ref_images", "reference_images", "images"))


async def generate_image(
    target: RouteTarget,
    prompt: str,
    user_id: int,
    *,
    size: str = "1024x1024",
    n: int = 1,
    reference_images: list[str] | None = None,
) -> list[str]:
    """调用 /images/generations,返回本地或远程可预览路径列表。

    reference_images: 可选 /gen-assets/... 或 data:/http(s) 参考图(定妆设定稿),
    会尝试带图的兼容字段;供应商不支持或参考图过大导致断连时回退纯 prompt,并自动重试。
    """
    import asyncio

    url = join_provider_url(target.provider.base_url, "images/generations")
    headers = {"Authorization": f"Bearer {decrypt_secret(target.provider.api_key_encrypted)}"}

    # 设定稿 data URL 往往很大,超过阈值则跳过参考图,避免 ConnectError
    max_ref_chars = 1_200_000
    ref_data: list[str] = []
    if reference_images:
        from .vision import asset_to_data_url

        for raw in reference_images[:2]:
            data = asset_to_data_url(user_id, raw) or (
                raw if (raw or "").startswith(("http://", "https://", "data:")) else None
            )
            if not data:
                continue
            if len(data) > max_ref_chars:
                continue
            ref_data.append(data)

    text_payloads: list[dict] = [
        {"model": target.model_id, "prompt": prompt, "n": n, "size": size, "response_format": "url"},
        {"model": target.model_id, "prompt": prompt, "n": n, "size": size, "response_format": "b64_json"},
        {"model": target.model_id, "prompt": prompt, "n": n, "size": size},
        {"model": target.model_id, "prompt": prompt},
    ]
    ref_payloads: list[dict] = []
    if ref_data:
        ref0 = ref_data[0]
        # 只试 1~2 种带图格式,失败立刻回退纯文,避免连发超大 body 把连接打爆
        ref_payloads = [
            {
                "model": target.model_id,
                "prompt": prompt,
                "n": n,
                "size": size,
                "image": ref0,
                "response_format": "url",
            },
            {
                "model": target.model_id,
                "prompt": prompt,
                "n": n,
                "size": size,
                "images": [{"url": ref0}],
            },
        ]

    # 先轻量纯文,再可选参考图(参考图失败不阻断)
    payloads = list(text_payloads) + ref_payloads
    # 若明确需要参考图优先:仍把纯文放前面更稳;剧情一致性主要靠 prompt
    last_err = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=30)) as client:
        skip_ref = False
        for body in payloads:
            if skip_ref and _payload_has_ref_image(body):
                continue
            # 纯文请求遇网络抖动时重试
            attempts = 3 if not _payload_has_ref_image(body) else 1
            for attempt in range(attempts):
                try:
                    resp = await client.post(url, headers=headers, json=body)
                except httpx.HTTPError as e:
                    last_err = f"网络错误:{e.__class__.__name__}"
                    if _payload_has_ref_image(body):
                        skip_ref = True
                        break
                    if attempt + 1 < attempts:
                        await asyncio.sleep(1.2 * (attempt + 1))
                        continue
                    break
                if resp.status_code in (400, 404, 422):
                    last_err = resp.text[:240]
                    if _payload_has_ref_image(body):
                        skip_ref = True
                    break
                if resp.status_code in (429, 502, 503, 504) and attempt + 1 < attempts:
                    last_err = f"HTTP {resp.status_code}"
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    # 非末次 payload 时换格式继续;末次才硬失败
                    last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    break
                try:
                    data = resp.json()
                except ValueError:
                    last_err = "图片生成响应不是合法 JSON"
                    break
                urls = _extract_urls(data)
                if not urls:
                    last_err = "响应中无图片地址"
                    break
                out: list[str] = []
                for u in urls[: max(1, n)]:
                    if u.startswith("data:"):
                        out.append(save_data_url(user_id, u, "png"))
                    elif u.startswith("http"):
                        out.append(await _download_to_asset(client, user_id, u, "png"))
                    else:
                        out.append(u)
                return out
    raise HTTPException(502, f"图片生成失败:{last_err or '未知错误'}")


async def generate_video(
    target: RouteTarget,
    prompt: str,
    user_id: int,
) -> AsyncIterator[dict]:
    """调用视频生成接口;支持同步 URL 与异步任务轮询。产出 status / video 事件。

    Agnes: POST /v1/videos → GET /agnesapi?video_id=&model_name=
    智谱等: POST …/videos/generations
    """
    import asyncio

    base = _provider_base(target.provider)
    api_root = _provider_api_root(target.provider.base_url)
    headers = {"Authorization": f"Bearer {decrypt_secret(target.provider.api_key_encrypted)}"}
    mid = target.model_id
    is_agnes = "agnes" in (target.provider.name or "").lower() or "agnes" in (mid or "").lower()

    # 创建任务 URL:Agnes/OpenAI Videos 用 /videos;智谱用 /videos/generations
    create_urls: list[str] = []
    for path in ("videos", "videos/generations", "video/generations"):
        u = join_provider_url(target.provider.base_url, path)
        if u not in create_urls:
            create_urls.append(u)
    if base and base not in create_urls and any(
        base.lower().endswith(s) for s in ("/videos/generations", "/video/generations", "/videos")
    ):
        create_urls.insert(0, base)

    bodies: list[dict] = []
    if is_agnes or "video" in (mid or "").lower():
        # Agnes Video 2.5: mode 必填;seconds 为字符串
        bodies.extend(
            [
                {
                    "model": mid,
                    "prompt": prompt,
                    "mode": "text",
                    "seconds": "5",
                    "size": "720P",
                    "aspect_ratio": "9:16",
                },
                {
                    "model": mid,
                    "prompt": prompt,
                    "mode": "text",
                    "seconds": "5",
                    "size": "720P",
                    "aspect_ratio": "16:9",
                },
                # Agnes Video v2.0
                {
                    "model": mid,
                    "prompt": prompt,
                    "height": 768,
                    "width": 1152,
                    "num_frames": 121,
                    "frame_rate": 24,
                },
            ]
        )
    bodies.extend(
        [
            {"model": mid, "prompt": prompt},
            {"model": mid, "prompt": prompt, "seconds": "5"},
            {"model": mid, "prompt": prompt, "seconds": 5},
            {"model": mid, "input": prompt},
        ]
    )

    last_err = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20)) as client:
        payload = None
        for create_url in create_urls:
            for body in bodies:
                yield {"type": "media_status", "detail": "正在提交视频任务…"}
                try:
                    resp = await client.post(create_url, headers=headers, json=body)
                except httpx.HTTPError as e:
                    last_err = f"网络错误:{e.__class__.__name__}"
                    continue
                if resp.status_code in (404, 405):
                    last_err = f"{create_url} 不可用"
                    break
                if resp.status_code == 429:
                    raise HTTPException(
                        429,
                        "Agnes 免费额度已达速率上限(HTTP 429)。请稍后再试,或到 Agnes 控制台升级 Token Plan。",
                    )
                if resp.status_code in (400, 422):
                    last_err = resp.text[:280]
                    continue
                if resp.status_code not in (200, 201, 202):
                    last_err = f"HTTP {resp.status_code}: {resp.text[:280]}"
                    continue
                try:
                    payload = resp.json()
                except ValueError:
                    last_err = "响应不是合法 JSON"
                    continue
                break
            if payload is not None:
                break

        if payload is None:
            raise HTTPException(502, f"视频生成失败:{last_err or '供应商未提供可用视频接口'}")

        urls = _extract_urls(payload)
        if urls:
            for u in urls[:1]:
                if u.startswith("data:"):
                    path = save_data_url(user_id, u, "mp4")
                elif u.startswith("http"):
                    path = await _download_to_asset(client, user_id, u, "mp4")
                else:
                    path = u
                yield {"type": "video", "url": path}
            return

        tid = _task_id(payload)
        # Agnes 创建响应里 video_id 用于轮询,可能与 id/task_id 不同
        video_id = None
        if isinstance(payload, dict):
            for k in ("video_id", "videoId"):
                if payload.get(k):
                    video_id = str(payload[k])
                    break
        poll_id = video_id or tid
        if not poll_id:
            raise HTTPException(502, f"视频任务已创建但无法解析任务 ID:{str(payload)[:200]}")

        poll_specs: list[tuple[str, dict | None]] = []
        # Agnes 官方轮询
        if api_root:
            poll_specs.append(
                (f"{api_root}/agnesapi", {"video_id": poll_id, "model_name": mid})
            )
            poll_specs.append((f"{api_root}/agnesapi", {"video_id": poll_id}))
        for path in (
            f"videos/{poll_id}",
            f"videos/generations/{poll_id}",
            f"video/generations/{poll_id}",
            f"tasks/{poll_id}",
        ):
            poll_specs.append((join_provider_url(target.provider.base_url, path), None))

        yield {"type": "media_status", "detail": f"视频生成中,任务 {poll_id[:18]}…"}

        for attempt in range(90):
            await asyncio.sleep(2 if attempt < 5 else 4)
            got = None
            for pu, params in poll_specs:
                try:
                    pr = await client.get(pu, headers=headers, params=params)
                except httpx.HTTPError:
                    continue
                if pr.status_code == 404:
                    continue
                if pr.status_code != 200:
                    last_err = f"轮询 HTTP {pr.status_code}: {(pr.text or '')[:160]}"
                    continue
                try:
                    got = pr.json()
                except ValueError:
                    continue
                break
            if got is None:
                yield {"type": "media_status", "detail": f"等待视频结果…({attempt + 1})"}
                continue
            st = _status_done(got)
            if st is False:
                err = got.get("error") or got.get("message") or "视频生成失败"
                if isinstance(err, dict):
                    err = err.get("message") or str(err)
                raise HTTPException(502, str(err)[:280])
            urls = _extract_urls(got)
            # Agnes: metadata.url
            meta = got.get("metadata") if isinstance(got, dict) else None
            if isinstance(meta, dict) and meta.get("url"):
                urls = [str(meta["url"])] + urls
            if urls or st is True:
                if not urls:
                    yield {"type": "media_status", "detail": "任务完成但未返回视频地址"}
                    raise HTTPException(502, "视频生成完成但无可用地址")
                u = urls[0]
                if u.startswith("http"):
                    path = await _download_to_asset(client, user_id, u, "mp4")
                elif u.startswith("data:"):
                    path = save_data_url(user_id, u, "mp4")
                else:
                    path = u
                yield {"type": "video", "url": path}
                return
            yield {"type": "media_status", "detail": f"视频生成中…({attempt + 1}/90)"}

        raise HTTPException(502, "视频生成超时,请稍后在供应商控制台查看任务状态")


def format_media_markdown(kind: str, urls: list[str], prompt: str) -> str:
    lines = []
    if kind == "image":
        lines.append("已生成图片预览:")
        for i, u in enumerate(urls, 1):
            lines.append(f"![图片{i}]({u})")
    else:
        lines.append("已生成视频预览:")
        for i, u in enumerate(urls, 1):
            lines.append(f"🎥 [视频{i}]({u})")
    lines.append("")
    lines.append(f"提示词:{prompt}")
    return "\n".join(lines)


async def stream_media_generation(
    db,
    user_id: int,
    purpose: str,
    prompt: str,
    *,
    provider_id: int | None = None,
    model_id: str | None = None,
    modality: str | None = None,
) -> AsyncIterator[dict]:
    """统一入口:按模型 modality 生成图片或视频;当前模型不支持时自动回退。"""
    want = modality if modality in ("image", "video") else None
    if want is None and provider_id and model_id:
        want = resolve_model_modality(db, user_id, provider_id, model_id)
    if want not in ("image", "video"):
        want = "image"

    mod = want
    used_fallback = False
    target = None
    if provider_id and model_id and resolve_model_modality(db, user_id, provider_id, model_id) == want:
        target = resolve_route_or_override(
            db, user_id, purpose, provider_id=provider_id, model_id=model_id
        )
    elif find_modality_model(db, user_id, want):
        target = resolve_modality_route(
            db, user_id, purpose, want, provider_id=provider_id, model_id=model_id
        )
        used_fallback = True

    if mod == "image":
        if target is not None:
            label = f"{target.provider.name}/{target.model_id}"
            yield {"type": "model", "model_name": label, "model": label, "modality": mod}
            if used_fallback:
                yield {"type": "notice", "detail": f"当前模型不支持图片生成,已自动切换到 {label}"}
            yield {"type": "media_status", "detail": "正在生成图片…"}
            try:
                urls = await generate_image(target, prompt, user_id)
            except Exception:
                urls = []
        else:
            urls = []
        if not urls:
            from .local_image import render_local_card

            yield {"type": "model", "model_name": "local/pillow", "model": "local/pillow", "modality": "image"}
            yield {"type": "notice", "detail": "未配置图片模型,已用本地渲染生成配图并保存到 data/gen"}
            yield {"type": "media_status", "detail": "正在本地生成图片…"}
            urls = [render_local_card(user_id, prompt[:80], context=prompt)]
        for u in urls:
            yield {"type": "image", "url": u}
        text = format_media_markdown("image", urls, prompt)
        yield {"type": "delta", "content": text}
        yield {
            "type": "usage",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "provider_name": (target.provider.name if target else "local"),
            "model_name": (target.model_id if target else "pillow"),
        }
        return

    if target is None:
        raise HTTPException(
            400,
            f"未找到可用的{('图片' if mod == 'image' else '视频')}模型。请到个人中心同步并启用对应模型。",
        )

    label = f"{target.provider.name}/{target.model_id}"
    yield {"type": "model", "model_name": label, "model": label, "modality": mod}
    if used_fallback:
        yield {"type": "notice", "detail": f"当前模型不支持视频生成,已自动切换到 {label}"}

    if mod == "video":
        urls: list[str] = []
        async for ev in generate_video(target, prompt, user_id):
            if ev.get("type") == "video":
                urls.append(ev["url"])
            yield ev
        if not urls:
            raise HTTPException(502, "未生成任何视频")
        text = format_media_markdown("video", urls, prompt)
        yield {"type": "delta", "content": text}
        yield {
            "type": "usage",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "provider_name": target.provider.name,
            "model_name": target.model_id,
        }
        return

    raise HTTPException(400, "当前模型不是图片/视频生成模型")


def resolve_model_modality(db, user_id: int, provider_id: int | None, model_id: str | None) -> str:
    """查库显式 modality,否则推断。"""
    if not provider_id or not model_id:
        return "text"
    from ..models import AiModel, AiProvider

    p = db.query(AiProvider).filter_by(id=provider_id, user_id=user_id).first()
    if not p:
        return infer_modality(model_id)
    row = db.query(AiModel).filter_by(provider_id=provider_id, model_id=model_id).first()
    explicit = getattr(row, "modality", None) if row else None
    return infer_modality(model_id, explicit)


def resolve_chat_modality(
    db,
    user_id: int,
    purpose: str,
    provider_id: int | None,
    model_id: str | None,
) -> str:
    """请求覆盖优先;否则看用途路由上的模型是否为图片/视频。"""
    if provider_id and model_id:
        return resolve_model_modality(db, user_id, provider_id, model_id)
    try:
        target = resolve_route_or_override(db, user_id, purpose)
    except Exception:
        return "text"
    return resolve_model_modality(db, user_id, target.provider.id, target.model_id)


_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_IMAGE_INTENT_RE = re.compile(
    r"(生成配图|生成图片|画一张|画一幅|生图|文生图|生成封面|配图[:：]|请生成.{0,12}图|做一张图)",
    re.I,
)
_VIDEO_INTENT_RE = re.compile(r"(生成视频|生视频|文生视频|短视频生成|做一段视频)", re.I)


def is_loadable_media_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    return u.startswith(("http://", "https://", "data:", "blob:")) or "/gen-assets/" in u


def extract_image_placeholders(text: str) -> list[tuple[str, str]]:
    """提取文案中的不可加载 Markdown 图片占位 (alt, url)。"""
    out: list[tuple[str, str]] = []
    for m in _MD_IMG_RE.finditer(text or ""):
        alt, url = m.group(1), (m.group(2) or "").strip()
        if url and not is_loadable_media_url(url):
            out.append((alt or "配图", url))
    return out


def is_pure_image_request(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _IMAGE_INTENT_RE.search(t):
        return False
    return len(t) < 240 or bool(
        re.match(r"^(生成配图|生成图片|画一|生图|文生图|生成封面|配图)", t)
    )


def is_pure_video_request(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _VIDEO_INTENT_RE.search(t):
        return False
    return len(t) < 240 or t.startswith(("生成视频", "生视频", "文生视频"))


def find_modality_model(db, user_id: int, want: str) -> tuple[int, str] | None:
    """在用户已启用模型中找第一个指定 modality。"""
    from ..models import AiModel, AiProvider

    providers = (
        db.query(AiProvider)
        .filter_by(user_id=user_id, enabled=True)
        .order_by(AiProvider.id.asc())
        .all()
    )
    for p in providers:
        rows = (
            db.query(AiModel)
            .filter_by(provider_id=p.id, enabled=True)
            .order_by(AiModel.id.asc())
            .all()
        )
        for m in rows:
            if infer_modality(m.model_id, m.modality) == want:
                return p.id, m.model_id
    return None


def resolve_modality_route(
    db,
    user_id: int,
    purpose: str,
    want: str,
    *,
    provider_id: int | None = None,
    model_id: str | None = None,
) -> RouteTarget:
    """优先用请求指定模型(若 modality 匹配),否则自动回退到可用的图片/视频模型。"""
    if provider_id and model_id:
        if resolve_model_modality(db, user_id, provider_id, model_id) == want:
            return resolve_route_or_override(
                db, user_id, purpose, provider_id=provider_id, model_id=model_id
            )
    found = find_modality_model(db, user_id, want)
    if not found:
        label = "图片" if want == "image" else "视频"
        raise HTTPException(
            400,
            f"未找到可用的{label}模型。请到个人中心 → AI 供应商「同步模型」,并启用名称含 "
            f"{'dall-e / flux / gpt-image / wanx' if want == 'image' else 'sora / kling / veo'} 的模型。",
        )
    return resolve_route_or_override(
        db, user_id, purpose, provider_id=found[0], model_id=found[1]
    )


def build_image_prompt(alt: str, context: str = "") -> str:
    base = (alt or "自媒体配图").strip()
    ctx = (context or "").strip().replace("\n", " ")
    if len(ctx) > 180:
        ctx = ctx[:180]
    parts = [
        base,
        "竖版构图,适合小红书/公众号配图,画面干净,信息清晰,高质量插画或信息图风格",
    ]
    if ctx:
        parts.append(f"文章上下文:{ctx}")
    return "。".join(parts)


async def _generate_one_image(
    db,
    user_id: int,
    purpose: str,
    alt: str,
    context: str,
    *,
    provider_id: int | None = None,
    model_id: str | None = None,
) -> tuple[str, str]:
    """生成一张图,返回 (public_path, source_label)。优先图片模型 API,否则本地 Pillow。"""
    from .local_image import render_local_card

    prompt = build_image_prompt(alt, context=context)
    found = find_modality_model(db, user_id, "image")
    if found or (
        provider_id
        and model_id
        and resolve_model_modality(db, user_id, provider_id, model_id) == "image"
    ):
        try:
            target = resolve_modality_route(
                db, user_id, purpose, "image", provider_id=provider_id, model_id=model_id
            )
            urls = await generate_image(target, prompt, user_id)
            if urls:
                return urls[0], f"{target.provider.name}/{target.model_id}"
        except Exception:
            pass
    # 本地落盘:backend/data/gen/{user_id}/*.png
    path = render_local_card(user_id, alt, context=context)
    return path, "local/pillow"


async def autofill_placeholders_with_images(
    db,
    user_id: int,
    purpose: str,
    text: str,
    *,
    provider_id: int | None = None,
    model_id: str | None = None,
    max_images: int = 8,
) -> AsyncIterator[dict]:
    """把文案里的图片占位自动换成真实可预览地址(API 或本地 PNG)。"""
    placeholders = extract_image_placeholders(text)
    if not placeholders:
        return

    use_api = bool(find_modality_model(db, user_id, "image"))
    yield {
        "type": "media_status",
        "detail": (
            f"文案含 {len(placeholders)} 处配图,正在自动生成…"
            + ("(图片模型)" if use_api else "(本地渲染并保存)")
        ),
    }
    yield {
        "type": "notice",
        "detail": "配图将自动生成并保存到服务器 data/gen 目录,无需手动点击",
    }

    updated = text
    total = min(len(placeholders), max_images)
    source_label = "local/pillow"
    for i, (alt, old_url) in enumerate(placeholders[:max_images], 1):
        yield {"type": "media_status", "detail": f"正在生成配图 {i}/{total}: {alt}"}
        try:
            new_url, source_label = await _generate_one_image(
                db,
                user_id,
                purpose,
                alt,
                text[:500],
                provider_id=provider_id,
                model_id=model_id,
            )
        except Exception as e:
            yield {
                "type": "media_status",
                "detail": f"配图「{alt}」生成失败:{getattr(e, 'detail', None) or e}",
            }
            continue
        old_md = f"![{alt}]({old_url})"
        new_md = f"![{alt}]({new_url})"
        if old_md in updated:
            updated = updated.replace(old_md, new_md, 1)
        else:
            updated = updated.replace(f"]({old_url})", f"]({new_url})", 1)
        yield {"type": "image", "url": new_url, "alt": alt, "placeholder": old_url}
        yield {"type": "content_replace", "content": updated}

    yield {"type": "content_replace", "content": updated}
    yield {
        "type": "usage",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "provider_name": source_label.split("/")[0] if "/" in source_label else source_label,
        "model_name": source_label,
    }
