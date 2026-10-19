"""本地配图生成(Pillow):无图片模型时也能把占位变成真实 PNG。"""

from __future__ import annotations

import hashlib
import re
import textwrap
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .media_gen import GEN_ROOT, public_asset_path

# 竖版小红书常用比例
W, H = 1080, 1440

_PALETTES = [
    ((45, 55, 95), (120, 90, 160), (255, 245, 235)),
    ((20, 80, 90), (40, 140, 130), (240, 252, 250)),
    ((90, 40, 50), (180, 80, 90), (255, 248, 245)),
    ((35, 50, 70), (70, 110, 160), (245, 248, 255)),
    ((60, 50, 40), (160, 120, 70), (255, 250, 240)),
]


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _palette_for(seed: str):
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return _PALETTES[h % len(_PALETTES)]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for para in (text or "").splitlines() or [""]:
        if not para.strip():
            lines.append("")
            continue
        # 中文按字宽估算
        buf = ""
        for ch in para:
            trial = buf + ch
            if draw.textlength(trial, font=font) <= max_width:
                buf = trial
            else:
                if buf:
                    lines.append(buf)
                buf = ch
        if buf:
            lines.append(buf)
    return lines[:16]


def render_local_card(user_id: int, alt: str, context: str = "") -> str:
    """根据配图说明生成竖版卡片 PNG,返回 /gen-assets/... 路径。"""
    title = (alt or "配图").strip()
    # 去掉「金句卡片：」一类前缀当主标题
    main = re.sub(r"^(金句卡片|对比图|路径图|清单|封面|配图)[:：\s]*", "", title).strip() or title
    subtitle = ""
    if context:
        # 取上下文里较短的一句作副文
        for line in context.splitlines():
            line = line.strip().lstrip("#*- ").strip()
            if 8 <= len(line) <= 40 and line != main:
                subtitle = line
                break

    bg1, bg2, fg = _palette_for(title)
    img = Image.new("RGB", (W, H), bg1)
    draw = ImageDraw.Draw(img)

    # 渐变背景
    for y in range(H):
        t = y / (H - 1)
        r = int(bg1[0] * (1 - t) + bg2[0] * t)
        g = int(bg1[1] * (1 - t) + bg2[1] * t)
        b = int(bg1[2] * (1 - t) + bg2[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # 内容卡片
    pad = 72
    card = (pad, int(H * 0.22), W - pad, int(H * 0.78))
    draw.rounded_rectangle(card, radius=36, fill=(255, 255, 255), outline=(255, 255, 255))

    tag_font = _font(36)
    title_font = _font(64)
    body_font = _font(40)
    foot_font = _font(28)

    # 顶部标签
    tag = "织火 · 配图"
    if "金句" in title:
        tag = "金句卡片"
    elif "路径" in title or "对比" in title:
        tag = "信息图"
    elif "清单" in title or "行动" in title:
        tag = "行动清单"
    draw.text((pad + 48, card[1] + 48), tag, font=tag_font, fill=bg2)

    # 主标题
    lines = _wrap(draw, main, title_font, W - pad * 2 - 96)
    y = card[1] + 140
    for line in lines[:6]:
        draw.text((pad + 48, y), line, font=title_font, fill=(30, 35, 45))
        y += 78

    if subtitle:
        y += 24
        for line in _wrap(draw, subtitle, body_font, W - pad * 2 - 96)[:4]:
            draw.text((pad + 48, y), line, font=body_font, fill=(90, 95, 110))
            y += 56

    draw.text((pad + 48, card[3] - 80), "xiaohuo", font=foot_font, fill=(160, 165, 175))

    out_dir = GEN_ROOT / str(user_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.png"
    path = out_dir / name
    img.save(path, format="PNG", optimize=True)
    return public_asset_path(user_id, name)
