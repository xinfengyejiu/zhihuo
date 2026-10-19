"""多模态消息组装:本地配图 → data URL,供 OpenAI 兼容 vision 接口。"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from .media_gen import GEN_ROOT, is_loadable_media_url

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

OKR_HINT_RE = re.compile(
    r"(okr|OKR|目标与关键结果|关键结果|绩效目标|季度目标|KPI|目标管理)",
    re.I,
)

VISION_OKR_PROMPT = """## 图片理解与 OKR 识别
用户可能上传截图/照片。请先看图再回答。
若图中包含 OKR、目标与关键结果、KPI、绩效/季度目标表格或白板:
1. OCR 识别图中全部可读文字(保持原用语)
2. 结构化整理为:
   - Objective(目标)
   - Key Results(关键结果,尽量量化)
   - 周期/负责人(若图中有)
3. 指出不清晰、不可衡量、缺基线的条目,并给出可执行改写建议
4. 若用户在做自媒体,可附 3 个内容选题角度
若图中不是 OKR,则按用户问题正常看图回答;用户未说明时默认「识别并整理图中目标/OKR」。"""


def looks_like_okr_request(text: str) -> bool:
    return bool(OKR_HINT_RE.search(text or ""))


def asset_to_data_url(user_id: int, url: str) -> str | None:
    """把 /gen-assets/{uid}/{file} 或 data: 转成模型可用的 data URL。"""
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith("data:"):
        return u
    # /gen-assets/2/xxx.png
    m = re.match(r"^(?:/api)?/gen-assets/(\d+)/([^/?#]+)$", u)
    if not m:
        if u.startswith("http://") or u.startswith("https://"):
            return u
        return None
    owner, name = int(m.group(1)), Path(m.group(2)).name
    if owner != user_id:
        return None
    path = (GEN_ROOT / str(owner) / name).resolve()
    root = (GEN_ROOT / str(owner)).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        return None
    ext = path.suffix.lstrip(".").lower()
    mime = _MIME.get(ext, "image/png")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_vision_user_content(text: str, image_urls: list[str], user_id: int) -> str | list[dict]:
    """组装 user content:有图则用多模态数组,否则纯文本。"""
    parts: list[dict] = []
    t = (text or "").strip()
    if t:
        parts.append({"type": "text", "text": t})
    for url in image_urls:
        data = asset_to_data_url(user_id, url)
        if not data:
            continue
        parts.append({"type": "image_url", "image_url": {"url": data}})
    if not parts:
        return t or "(空消息)"
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"]
    if not t:
        parts.insert(0, {"type": "text", "text": "请识别并分析这些图片。若含 OKR/目标与关键结果,请结构化整理。"})
    return parts


def format_user_display_content(text: str, image_urls: list[str]) -> str:
    """写入聊天记录的可读内容(含可预览 Markdown 图)。"""
    chunks = []
    if (text or "").strip():
        chunks.append(text.strip())
    for i, url in enumerate(image_urls, 1):
        if is_loadable_media_url(url) or url.startswith("/gen-assets/"):
            chunks.append(f"![上传图片{i}]({url})")
    return "\n\n".join(chunks) if chunks else "(图片)"
