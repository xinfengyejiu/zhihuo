"""根据模型 ID 推断能力:text / image / video。"""

from __future__ import annotations

import re

Modality = str  # text | image | video

_VIDEO_RE = re.compile(
    r"(sora|veo|kling|runway|luma|pika|hailuo|minimax[-_]?video|wan[-_]?video|"
    r"vidu|hunyuan[-_]?video|cogvideo|gen-3|gen3|dream[-_]?machine|"
    r"video[-_]?gen|t2v|text[-_]?to[-_]?video|\bvideo\b)",
    re.I,
)

_IMAGE_RE = re.compile(
    r"(dall-?e|gpt-?image|flux|stable[-_]?diffusion|sdxl|sd3|midjourney|"
    r"imagen|kolors|wanx|wanxiang|cogview|ideogram|playground|"
    r"recraft|seedream|jimeng|image[-_]?gen|t2i|text[-_]?to[-_]?image|"
    r"\bimage\b|\bimg\b)",
    re.I,
)


def infer_modality(model_id: str | None, explicit: str | None = None) -> Modality:
    """优先用显式 modality,否则按模型名启发式推断。"""
    if explicit in ("text", "image", "video"):
        return explicit
    mid = (model_id or "").strip()
    if not mid:
        return "text"
    if _VIDEO_RE.search(mid):
        return "video"
    if _IMAGE_RE.search(mid):
        return "image"
    return "text"


def modality_label(mod: Modality) -> str:
    return {"text": "文本", "image": "图片", "video": "视频"}.get(mod, "文本")
