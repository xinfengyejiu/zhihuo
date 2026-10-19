"""把 AI 或粘贴的大纲文本解析成卷 + 章节目录。

优先吃 JSON(模型按约定输出,或被截断时尽量救回已完整的章节);
否则按行解析「卷:」「第N章 标题|要点」以及常见 Markdown 标题。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class CatalogChapter:
    title: str
    outline: str | None = None


@dataclass
class CatalogVolume:
    title: str | None = None
    outline: str | None = None
    chapters: list[CatalogChapter] = field(default_factory=list)


_NUM = "0-9零一二三四五六七八九十百千两"
_CHAPTER_PREFIX = re.compile(
    rf"^(第[{_NUM}]+[章回节]|Chapter\s*\d+)\s*[:：.、\-—\s]*",
    re.IGNORECASE,
)
_VOLUME_LINE = re.compile(
    rf"^(?:卷[:：]\s*(.+)|(第[{_NUM}]+[卷部])\s*[:：]?\s*(.*))$"
)
_DETAIL_LINE = re.compile(
    r"^(剧情要点|剧情|本章目标|本章要点|要点|概要|出场人物|人物|钩子|伏笔任务|伏笔)\s*[:：]\s*(.*)$"
)
_HEADING_HINT = re.compile(rf"(第[{_NUM}]+[卷部章回节]|卷[:：]|Chapter\s*\d+)", re.IGNORECASE)


def normalize_chapter_title(title: str) -> str:
    title = title.strip().strip("*").strip()
    stripped = _CHAPTER_PREFIX.sub("", title).strip()
    return stripped or title


def chapter_key(title: str) -> str:
    return normalize_chapter_title(title).strip()


def _norm_text(text: str) -> str:
    return text.translate(str.maketrans("０１２３４５６７８９｜", "0123456789|"))


def parse_catalog(text: str) -> tuple[list[CatalogVolume], str]:
    """返回 (卷列表, 来源 json|lines)。"""
    raw = _norm_text(text or "")
    if _looks_like_json(raw):
        return _parse_json_catalog(raw), "json"
    return _parse_line_catalog(raw), "lines"


def _looks_like_json(text: str) -> bool:
    if "{" not in text:
        return False
    return '"volumes"' in text or '"chapters"' in text or '"卷"' in text


def _pick(data: dict, *keys: str):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _chapter_outline(item: dict) -> str | None:
    outline = str(_pick(item, "outline", "summary", "plot", "剧情", "概要", "要点") or "").strip()
    characters = str(_pick(item, "characters", "cast", "人物", "出场人物") or "").strip()
    if characters:
        outline = f"{outline}（出场人物:{characters}）" if outline else f"出场人物:{characters}"
    return outline or None


def _parse_json_catalog(text: str) -> list[CatalogVolume]:
    blob = _extract_json_blob(text)
    if not blob:
        return []
    data = _loads_lenient(blob)
    if data is None:
        return []
    volumes_raw = _volume_list(data)
    groups: list[CatalogVolume] = []
    for item in volumes_raw:
        if not isinstance(item, dict):
            continue
        title = str(_pick(item, "title", "name", "卷名", "标题") or "").strip() or None
        outline = str(_pick(item, "outline", "summary", "概要", "剧情") or "").strip() or None
        chapters_raw = _pick(item, "chapters", "章节", "items") or []
        if isinstance(chapters_raw, dict):
            chapters_raw = [chapters_raw]
        chapters: list[CatalogChapter] = []
        if isinstance(chapters_raw, list):
            for ch in chapters_raw:
                if isinstance(ch, str):
                    name = normalize_chapter_title(ch)
                    if name:
                        chapters.append(CatalogChapter(title=name[:128]))
                    continue
                if not isinstance(ch, dict):
                    continue
                name = str(_pick(ch, "title", "name", "章名", "标题") or "").strip()
                name = normalize_chapter_title(name)
                if not name:
                    continue
                chapters.append(CatalogChapter(title=name[:128], outline=_chapter_outline(ch)))
        if chapters:
            groups.append(CatalogVolume(title=title[:128] if title else None, outline=outline, chapters=chapters))
    return groups


def _volume_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("volumes", "卷", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        if any(k in data for k in ("chapters", "章节")):
            return [data]
    return []


def _extract_json_blob(text: str) -> str | None:
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    if start >= 0:
        return text[start:]
    return None


def _relax(blob: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", blob)


def _loads_lenient(blob: str):
    candidates = [_relax(blob), _relax(_close_truncated(blob))]
    last = blob.rfind("}")
    if last > 0:
        candidates.append(_relax(_close_truncated(blob[: last + 1])))
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _close_truncated(blob: str) -> str:
    """截掉未写完的字符串/键,再补上未闭合的括号。"""
    src = blob
    for _ in range(4):
        stack: list[str] = []
        in_str = False
        esc = False
        for ch in src:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch in "{[":
                stack.append(ch)
            elif ch == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "[":
                stack.pop()
        if not in_str:
            src = src.rstrip().rstrip(",")
            src = re.sub(r'[:,]\s*$', "", src)
            src = re.sub(r'"[^"\\]*"\s*:\s*$', "", src)
            src = src.rstrip().rstrip(",")
            closers = []
            for br in reversed(stack):
                closers.append("}" if br == "{" else "]")
            return src + "".join(closers)
        cut = src.rfind('"')
        if cut <= 0:
            return src
        src = src[:cut]
    return src


def _parse_line_catalog(text: str) -> list[CatalogVolume]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        lines.append(line)

    structured = sum(1 for line in lines if _HEADING_HINT.search(line.lstrip("#").strip())) >= 2
    current = CatalogVolume()
    groups = [current]

    for raw in lines:
        cleaned = raw.lstrip("#").strip().strip("*").strip()
        cleaned = re.sub(r"^[-*+]\s+", "", cleaned)
        cleaned = re.sub(r"^\d+[.、)\]]\s*", "", cleaned).strip()
        if not cleaned:
            continue

        volume_match = _VOLUME_LINE.match(cleaned)
        if volume_match and "|" not in cleaned:
            if volume_match.group(1):
                vtitle = volume_match.group(1).strip()
            else:
                prefix = volume_match.group(2)
                rest = (volume_match.group(3) or "").strip()
                vtitle = f"{prefix} {rest}".strip() if rest else prefix
            if not vtitle:
                continue
            current = CatalogVolume(title=vtitle[:128])
            groups.append(current)
            continue

        detail = _DETAIL_LINE.match(cleaned)
        if detail and current.chapters:
            extra = detail.group(2).strip()
            if extra:
                label = detail.group(1)
                prev = current.chapters[-1]
                piece = extra if label in ("剧情", "剧情要点", "要点", "概要", "本章要点", "本章目标") else f"{label}:{extra}"
                prev.outline = f"{prev.outline} {piece}".strip() if prev.outline else piece
            continue

        is_heading = bool(_CHAPTER_PREFIX.match(cleaned)) or "|" in cleaned
        if structured and not is_heading:
            # 已能认出卷/章标题时,丢掉说明文字,避免整段分析被建成章节
            if len(cleaned) > 40:
                continue
            if cleaned.startswith(("好的", "根据", "以下", "说明", "注意", "输出", "综上")):
                continue

        title_part, _, rest = cleaned.partition("|")
        title = normalize_chapter_title(title_part)
        if not title:
            continue
        outline = rest.split("|")[0].strip() if rest else None
        current.chapters.append(CatalogChapter(title=title[:128], outline=outline or None))

    return [g for g in groups if g.chapters]
