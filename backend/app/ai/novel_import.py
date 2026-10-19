"""小说导入解析(TXT / Markdown):规则拆卷章为主,AI 补书名/简介。

流水线:先按标记拆分 → 并行清洗/提要 → 按原文顺序合并去重。
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .extract import extract_json_object

IMPORT_EXTENSIONS = (".txt", ".text", ".md", ".markdown")

# 正文不足此字数的「章节」视为目录/章名引用,不入库
MIN_CHAPTER_CHARS = 100
# 超过该章数启用线程池并行清洗
_PARALLEL_CHAPTER_THRESHOLD = 24
_MAX_PARSE_WORKERS = max(2, min(8, (os.cpu_count() or 4)))

# 卷标题行(可带 Markdown #)
VOLUME_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<title>"
    r"第[零〇一二三四五六七八九十百千万两\d]+卷[^\n]{0,40}"
    r"|卷[零〇一二三四五六七八九十百千万两\d]+[^\n]{0,40}"
    r"|VOLUME\s*\d+[^\n]{0,40}"
    r")\s*$",
    re.I | re.M,
)

# 规范章标题:「第一章 星空中的青铜巨棺」(副标题前须空白,且不含句读)
CHAPTER_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<title>"
    r"第[零〇一二三四五六七八九十百千万两\d]+[章节回]"
    r"(?:[ \t]+[^\n，。！？；、]{1,40})?"
    r"|Chapter\s*\d+(?:[ \t]+[^\n，。！？；]{1,40})?"
    r"|CHAPTER\s*\d+(?:[ \t]+[^\n，。！？；]{1,40})?"
    r")\s*$",
    re.I | re.M,
)

# 作者说切分点(窄匹配,避免正文里误切)
CHAPTER_AUTHOR_NOTE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<title>第[零〇一二三四五六七八九十百千万两\d]+[章节回]"
    r"(?:到|了|来啦?|奉上)[^\n]{0,70})\s*$",
    re.M,
)
# 无空格副标题:「第一章星空中的青铜巨棺」
CHAPTER_COMPACT = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<title>第[零〇一二三四五六七八九十百千万两\d]+[章节回]"
    r"[^\s\n，。！？；到了啦][^\n，。！？；]{1,39})\s*$",
    re.M,
)

# 纯 Markdown ATX 标题
MD_HEADING = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$", re.M)

# 织火导出格式:====\n标题\n====
EXPORT_SEP = re.compile(r"^\s*={3,}\s*$", re.M)


@dataclass
class ParsedChapter:
    title: str
    content: str
    outline: str | None = None
    pos: int = 0  # 原文偏移,用于保序


@dataclass
class ParsedVolume:
    title: str
    outline: str | None = None
    chapters: list[ParsedChapter] = field(default_factory=list)
    pos: int = 0


@dataclass
class ParsedNovel:
    title: str
    summary: str | None
    volumes: list[ParsedVolume]
    source: str  # regex | export | ai-assist | whole


def decode_txt_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5"):
        try:
            text = data.decode(enc)
            if text.count("\ufffd") > max(20, len(text) // 50):
                continue
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "无法识别文本编码,请另存为 UTF-8 或 GBK 后再导入")


def _clean_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    t = t.strip("#*=-—_|　")
    return t[:128] or "未命名章节"


def _guess_title_from_text(text: str, filename: str | None) -> str:
    head = text[:2000]
    m = re.search(r"[《『「]([^》』」]{1,40})[》』」]", head)
    if m:
        return m.group(1).strip()[:128]
    hm = re.search(r"^#\s+(.+?)\s*$", head, re.M)
    if hm:
        t = _clean_title(hm.group(1))
        if 2 <= len(t) <= 40 and not CHAPTER_LINE.match(t) and not VOLUME_LINE.match(t):
            return t
    for line in head.split("\n")[:12]:
        s = line.strip().strip("#*=-—_|　")
        if 2 <= len(s) <= 40 and not CHAPTER_LINE.match(s) and not VOLUME_LINE.match(s):
            if not re.fullmatch(r"[\d\s\.]+", s):
                return s[:128]
    if filename:
        name = re.sub(r"\.(txt|text|md|markdown)$", "", filename, flags=re.I)
        name = re.sub(r"[_\-]+", " ", name).strip()
        if name:
            return name[:128]
    return "导入小说"


def _is_md_file(filename: str | None) -> bool:
    if not filename:
        return False
    return filename.lower().endswith((".md", ".markdown"))


def _looks_like_volume_title(title: str) -> bool:
    t = title.strip()
    return bool(VOLUME_LINE.match(t) or re.match(r"^(卷|部|篇|辑)\s*", t))


def _is_plausible_chapter_title(title: str) -> bool:
    """真正的章名(短、无句读);排除作者说到/求票等。"""
    t = (title or "").strip()
    if not t or len(t) > 40:
        return False
    if re.search(r"[，。！？；…]", t):
        return False
    if re.search(
        r"(到了?|来了|来啦|奉上|还有|继续写|晚间|晚上|更新|求票|订阅|谢谢|字数|几个小时|最后)",
        t,
    ):
        return False
    if re.match(r"^(序章|序言|楔子|引子|前言|后记|番外|尾声)([ \t]+\S.{0,28})?$", t):
        return True
    if CHAPTER_LINE.match(t) or CHAPTER_LINE.match(f"# {t}"):
        return True
    return bool(
        re.fullmatch(
            r"第[零〇一二三四五六七八九十百千万两\d]+[章节回](?:[ \t]+[^\n，。！？；、]{1,40})?",
            t,
        )
    )


def _normalize_chapter_title(raw: str) -> str | None:
    """规范章标题;作者说→「第N章」;无空格副标题补空格;完全不像则 None。"""
    t = _clean_title(raw)
    if not t:
        return None
    if _is_plausible_chapter_title(t):
        return t

    m = re.match(r"^(第[零〇一二三四五六七八九十百千万两\d]+[章节回])(.*)$", t)
    if not m:
        m = re.match(r"^(Chapter\s*\d+)(.*)$", t, re.I)
        if not m:
            return None
        head, rest = m.group(1), (m.group(2) or "").strip()
    else:
        head, rest = m.group(1), (m.group(2) or "").strip()

    # 作者说 / 口头禅
    if rest and (
        re.match(r"^[到了啦]", rest)
        or re.search(r"[，。！？；]|还有|晚间|晚上|更新|求票|继续写|几个小时|不要等|早上起来", rest)
        or len(t) > 28
    ):
        return _clean_title(head)

    # 「第一章星空中的青铜巨棺」无空格副标题 → 补空格
    if rest and not re.search(r"[，。！？；]", rest) and 2 <= len(rest) <= 40:
        if not re.match(r"^[到了啦]", rest):
            spaced = f"{head} {rest}"
            if _is_plausible_chapter_title(spaced) or len(spaced) <= 40:
                return spaced[:128]

    if not rest:
        return _clean_title(head)
    return None


def _looks_like_chapter_title(title: str) -> bool:
    return _normalize_chapter_title(title) is not None


def _strip_md_noise(content: str) -> str:
    if not content:
        return ""
    # 大正文快路径:无 markdown 分隔线则只做 strip
    if "---" not in content and "***" not in content and "___" not in content:
        return content.strip()
    lines = []
    for line in content.split("\n"):
        if re.fullmatch(r"\s*[-*_]{3,}\s*", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _chapter_len(content: str) -> int:
    return len((content or "").strip())


_CN_NUM = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10000,
}


def _cn_to_int(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    # 一…九 / 十 / 十一…十九 / 二十…九十九
    m = re.fullmatch(
        r"(?:(?P<w>[一二三四五六七八九两])?十(?P<u>[零〇一二三四五六七八九])?)|(?P<d>[零〇一二三四五六七八九两])",
        s,
    )
    if not m:
        return None
    if m.group("d"):
        return _CN_NUM[m.group("d")]
    tens = _CN_NUM[m.group("w")] if m.group("w") else 1
    unit = _CN_NUM.get(m.group("u") or "", 0)
    return tens * 10 + unit


def _chapter_ordinal_key(title: str) -> str | None:
    """同一卷内用于去重的章序号键,如 ch:1 / special:序章。"""
    t = (title or "").strip()
    if not t:
        return None
    for name in ("序章", "序言", "楔子", "引子", "前言", "后记", "尾声"):
        if t.startswith(name):
            return f"special:{name}"
    m = re.search(r"第([零〇一二三四五六七八九十百千万两\d]+)[章节回]", t)
    if m:
        n = _cn_to_int(m.group(1))
        if n is not None:
            return f"ch:{n}"
        return f"ch:{m.group(1)}"
    m = re.search(r"Chapter\s*(\d+)", t, re.I)
    if m:
        return f"ch:{int(m.group(1))}"
    if re.match(r"番外", t):
        return f"extra:{re.sub(r'\s+', '', t)[:40]}"
    return None


def _norm_title_key(title: str) -> str:
    return re.sub(r"\s+", "", title or "")


def _norm_body(content: str) -> str:
    return re.sub(r"\s+", "", content or "")


def _content_fingerprint(content: str) -> str:
    """正文指纹(用开头,忽略小幅字数差,便于合并近重复章)。"""
    s = _norm_body(content)
    if len(s) < MIN_CHAPTER_CHARS:
        return ""
    return s[:240]


def _title_quality(title: str) -> int:
    """标题质量:规范章名优先于「第N章」裸序号/作者说残留。"""
    t = (title or "").strip()
    if not t:
        return 0
    if not _is_plausible_chapter_title(t):
        return 1
    # 有副标题的规范名更好
    if re.search(r"[章节回][ \t]+\S", t) or re.match(r"Chapter\s*\d+\s+\S", t, re.I):
        return 100 + min(len(t), 40)
    return 50


def _strip_leading_title(title: str, content: str) -> str:
    """去掉正文开头重复的章标题行(常见于 MD 标题 + 纯文本标题各出现一次)。"""
    body = (content or "").lstrip("\n")
    if not body:
        return ""
    first, _, rest = body.partition("\n")
    a = _norm_title_key(_clean_title(first.lstrip("#").strip()))
    b = _norm_title_key(title)
    if a and b and (a == b or a.endswith(b) or b.endswith(a)):
        return rest.lstrip("\n")
    return content


def _near_duplicate_content(a: str, b: str) -> bool:
    """正文高度重合(一份多含标题行等)视为同一章。"""
    sa, sb = _norm_body(a), _norm_body(b)
    if not sa or not sb:
        return False
    short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(short) < MIN_CHAPTER_CHARS:
        return False
    if short == long:
        return True
    if short in long and (len(long) - len(short)) <= max(80, len(long) // 20):
        return True
    if len(short) >= 200 and long.startswith(short[:200]) and abs(len(long) - len(short)) <= 80:
        return True
    return False


def _dedupe_key(ch: ParsedChapter) -> str:
    ord_key = _chapter_ordinal_key(ch.title)
    if ord_key:
        return ord_key
    return f"title:{_norm_title_key(ch.title)}"


def _chapter_order_key(ch: ParsedChapter) -> tuple:
    """阅读顺序:序章 → 第N章 → 后记/番外;同序再按原文位置。"""
    ok = _chapter_ordinal_key(ch.title)
    if ok and ok.startswith("special:"):
        name = ok.split(":", 1)[1]
        prologue = {"楔子": -4, "序章": -3, "序言": -2, "引子": -1, "前言": 0}
        if name in prologue:
            return (0, prologue[name], ch.pos)
        if name in ("后记", "尾声"):
            return (2, 0 if name == "后记" else 1, ch.pos)
    if ok and ok.startswith("ch:"):
        try:
            return (1, int(ok.split(":", 1)[1]), ch.pos)
        except ValueError:
            return (1, 10**9, ch.pos)
    if ok and ok.startswith("extra:"):
        return (3, ch.pos, 0)
    return (1, 10**9 + max(ch.pos, 0), ch.pos)


def _merge_chapter(prev: ParsedChapter, cur: ParsedChapter) -> ParsedChapter:
    if _title_quality(cur.title) > _title_quality(prev.title):
        title = cur.title
    elif _title_quality(cur.title) < _title_quality(prev.title):
        title = prev.title
    else:
        title = cur.title if len(cur.title or "") >= len(prev.title or "") else prev.title
    if _chapter_len(cur.content) > _chapter_len(prev.content):
        content, outline = cur.content, cur.outline or prev.outline
    else:
        content, outline = prev.content, prev.outline or cur.outline
    return ParsedChapter(title=title, content=content, outline=outline, pos=min(prev.pos, cur.pos))


def _finalize_volumes(volumes: list[ParsedVolume], *, already_cleaned: bool = False) -> list[ParsedVolume]:
    """O(n) 清洗:短章丢弃 + 序号去重 + 正文指纹去重 + 按章序排序。"""
    out: list[ParsedVolume] = []
    for v in volumes:
        by_key: dict[str, ParsedChapter] = {}
        key_order: list[str] = []
        fp_to_key: dict[str, str] = {}

        for ch in v.chapters:
            title = _normalize_chapter_title(ch.title)
            if not title:
                continue
            if already_cleaned:
                content = (ch.content or "").strip()
            else:
                content = _strip_leading_title(title, _strip_md_noise(ch.content))
                content = _strip_leading_author_note(content)
            if _chapter_len(content) < MIN_CHAPTER_CHARS:
                continue
            outline = ch.outline if (already_cleaned and ch.outline) else (ch.outline or _outline_from_content(content))
            cur = ParsedChapter(title=title, content=content, outline=outline, pos=ch.pos)

            ord_key = _chapter_ordinal_key(title)
            fp = _content_fingerprint(content)
            key = ord_key or (f"fp:{fp}" if fp else f"title:{_norm_title_key(title)}")

            # 正文指纹命中更优先合并(同一段正文被切成多个标题)
            if fp and fp in fp_to_key and fp_to_key[fp] in by_key:
                old_key = fp_to_key[fp]
                merged = _merge_chapter(by_key[old_key], cur)
                new_key = _chapter_ordinal_key(merged.title) or old_key
                if new_key != old_key:
                    by_key.pop(old_key, None)
                    if old_key in key_order:
                        key_order[key_order.index(old_key)] = new_key
                    key = new_key
                else:
                    key = old_key
                by_key[key] = merged
                fp_to_key[_content_fingerprint(merged.content) or fp] = key
                continue

            if key in by_key:
                merged = _merge_chapter(by_key[key], cur)
                by_key[key] = merged
                mfp = _content_fingerprint(merged.content)
                if mfp:
                    fp_to_key[mfp] = key
            else:
                by_key[key] = cur
                key_order.append(key)
                if fp:
                    fp_to_key[fp] = key

        kept = [by_key[k] for k in key_order if k in by_key]
        kept.sort(key=_chapter_order_key)
        if kept:
            out.append(ParsedVolume(title=v.title, outline=v.outline, chapters=kept, pos=v.pos))
    out.sort(key=lambda vol: (vol.pos, vol.title))
    return out


def sanitize_parsed_novel(parsed: ParsedNovel, *, already_cleaned: bool = False) -> ParsedNovel:
    """入库前关卡(O(n)):短章/重复/作者说清理 + 章序重排。"""
    parsed.volumes = _finalize_volumes(parsed.volumes, already_cleaned=already_cleaned)
    return parsed


def _strip_leading_author_note(content: str) -> str:
    """去掉正文开头的作者说/求票行。"""
    body = (content or "").lstrip("\n")
    if not body:
        return ""
    lines = body.split("\n")
    while lines:
        s = lines[0].strip().lstrip("#").strip()
        if not s:
            lines.pop(0)
            continue
        if _normalize_chapter_title(s) and not _is_plausible_chapter_title(s):
            lines.pop(0)
            continue
        if re.match(r"^(作者|说两句|感谢|求票|求订阅)", s):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


@dataclass
class _ChapterJob:
    """拆分阶段产出的待清洗任务。"""

    seq: int
    vol_title: str
    vol_pos: int
    title: str
    pos: int
    body: str


def _refine_chapter_job(job: _ChapterJob) -> tuple[int, str, int, str, int, str, str | None] | None:
    """并行阶段:清洗正文 + 章纲;过短则丢弃。"""
    title = _normalize_chapter_title(job.title)
    if not title:
        return None
    content = _strip_leading_title(title, _strip_md_noise(job.body))
    content = _strip_leading_author_note(content)
    if _chapter_len(content) < MIN_CHAPTER_CHARS:
        return None
    outline = _outline_from_content(content)
    return (job.seq, job.vol_title, job.vol_pos, title, job.pos, content, outline)


def _run_parallel_refine(jobs: list[_ChapterJob]) -> list[tuple[int, str, int, str, int, str, str | None]]:
    if not jobs:
        return []
    if len(jobs) < _PARALLEL_CHAPTER_THRESHOLD:
        refined = [_refine_chapter_job(j) for j in jobs]
    else:
        workers = min(_MAX_PARSE_WORKERS, max(2, len(jobs) // 16))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            refined = list(pool.map(_refine_chapter_job, jobs, chunksize=max(4, len(jobs) // (workers * 4) or 4)))
    out = [r for r in refined if r is not None]
    out.sort(key=lambda x: x[0])
    return out


def _merge_refined(
    refined: list[tuple[int, str, int, str, int, str, str | None]],
) -> list[ParsedVolume]:
    """按拆分序号合并为卷章(保持先后)。"""
    volumes: list[ParsedVolume] = []
    current: ParsedVolume | None = None
    for _seq, vol_title, vol_pos, title, pos, content, outline in refined:
        if current is None or current.title != vol_title or current.pos != vol_pos:
            current = ParsedVolume(title=vol_title, pos=vol_pos)
            volumes.append(current)
        current.chapters.append(ParsedChapter(title=title, content=content, outline=outline, pos=pos))
    return volumes


def _jobs_from_marker_volumes(volumes: list[ParsedVolume]) -> list[_ChapterJob]:
    jobs: list[_ChapterJob] = []
    seq = 0
    for v in volumes:
        for ch in v.chapters:
            jobs.append(
                _ChapterJob(
                    seq=seq,
                    vol_title=v.title,
                    vol_pos=v.pos,
                    title=ch.title,
                    pos=ch.pos,
                    body=ch.content or "",
                )
            )
            seq += 1
    return jobs


def _parse_pipeline_from_volumes(raw_volumes: list[ParsedVolume]) -> list[ParsedVolume]:
    """拆分结果 → 并行清洗 → 按序合并 → 去重排序。"""
    if not raw_volumes:
        return []
    jobs = _jobs_from_marker_volumes(raw_volumes)
    refined = _run_parallel_refine(jobs)
    merged = _merge_refined(refined)
    return _finalize_volumes(merged, already_cleaned=True)


def _collapse_marker_matches(
    matches: list[tuple[int, str, str]], text: str
) -> list[tuple[int, int, str, str]]:
    """合并紧邻重复章标题。返回 (order_pos, split_pos, kind, title)。"""
    if not matches:
        return []
    # order_pos=首次出现(保序); split_pos=最后标题行(正文起点)
    out: list[tuple[int, int, str, str]] = [
        (matches[0][0], matches[0][0], matches[0][1], matches[0][2])
    ]
    for pos, kind, title in matches[1:]:
        order_pos, split_pos, prev_kind, prev_title = out[-1]
        if kind == "chapter" and prev_kind == "chapter":
            gap = text[split_pos:pos]
            gap_body = gap.split("\n", 1)[1] if "\n" in gap else ""
            same = (
                _chapter_ordinal_key(prev_title)
                and _chapter_ordinal_key(prev_title) == _chapter_ordinal_key(title)
            ) or _norm_title_key(prev_title) == _norm_title_key(title)
            if same and _chapter_len(gap_body) < MIN_CHAPTER_CHARS:
                better_title = title if len(title) >= len(prev_title) else prev_title
                out[-1] = (order_pos, pos, kind, better_title)
                continue
        out.append((pos, pos, kind, title))
    return out


def _parse_export_format(text: str) -> list[ParsedChapter] | None:
    lines = text.split("\n")
    chapters: list[ParsedChapter] = []
    i = 0
    while i < len(lines):
        if EXPORT_SEP.match(lines[i] or ""):
            if i + 2 < len(lines) and EXPORT_SEP.match(lines[i + 2] or ""):
                title = _clean_title(lines[i + 1])
                j = i + 3
                body: list[str] = []
                while j < len(lines):
                    if EXPORT_SEP.match(lines[j] or "") and j + 2 < len(lines) and EXPORT_SEP.match(lines[j + 2] or ""):
                        break
                    body.append(lines[j])
                    j += 1
                content = "\n".join(body).strip()
                if title and content:
                    # 用行号近似位置,保证导出格式也有先后
                    chapters.append(ParsedChapter(title=title, content=content, pos=i))
                i = j
                continue
        i += 1
    return chapters if len(chapters) >= 1 else None


def _parse_by_markers(text: str) -> list[ParsedVolume]:
    """按卷/章标题行切分全文。"""
    raw: list[tuple[int, str, str]] = []
    for m in VOLUME_LINE.finditer(text):
        raw.append((m.start(), "volume", _clean_title(m.group("title"))))
    seen_pos: set[int] = set()
    for rx in (CHAPTER_LINE, CHAPTER_COMPACT, CHAPTER_AUTHOR_NOTE):
        for m in rx.finditer(text):
            if m.start() in seen_pos:
                continue
            title = _normalize_chapter_title(m.group("title"))
            if not title:
                continue
            seen_pos.add(m.start())
            raw.append((m.start(), "chapter", title))
    raw.sort(key=lambda x: x[0])
    matches = _collapse_marker_matches(raw, text)

    if not any(k == "chapter" for _, _, k, _ in matches):
        return []

    volumes: list[ParsedVolume] = []
    current_vol = ParsedVolume(title="第一卷", pos=0)
    volumes.append(current_vol)

    for idx, (order_pos, split_pos, kind, title) in enumerate(matches):
        # 正文截止到下一标记的首次标题位置,避免吃进下一章标题行
        end = matches[idx + 1][0] if idx + 1 < len(matches) else len(text)
        if kind == "volume":
            if not current_vol.chapters and current_vol.title == "第一卷" and len(volumes) == 1:
                current_vol.title = title
                current_vol.pos = order_pos
            else:
                current_vol = ParsedVolume(title=title, pos=order_pos)
                volumes.append(current_vol)
            continue

        block = text[split_pos:end]
        first_nl = block.find("\n")
        body = block[first_nl + 1 :].strip() if first_nl >= 0 else ""
        current_vol.chapters.append(ParsedChapter(title=title, content=body, pos=order_pos))

    return [v for v in volumes if v.chapters]


def _parse_markdown_headings(text: str) -> list[ParsedVolume]:
    """按 Markdown 标题层级拆卷章。"""
    from collections import Counter

    matches = [(m.start(), len(m.group("level")), _clean_title(m.group("title"))) for m in MD_HEADING.finditer(text)]
    if len(matches) < 2:
        return []

    start_idx = 0
    if matches[0][1] == 1 and len(matches) >= 2 and not _looks_like_chapter_title(matches[0][2]):
        start_idx = 1

    levels = [lv for _, lv, _ in matches[start_idx:]]
    if not levels:
        return []

    volume_level = None
    for _, lv, title in matches[start_idx:]:
        if _looks_like_volume_title(title):
            volume_level = lv
            break

    if volume_level is None:
        cnt = Counter(levels)
        chapter_level = max(cnt.items(), key=lambda x: (x[1], -x[0]))[0]
        volumes = [ParsedVolume(title="第一卷", pos=0)]
        for idx in range(start_idx, len(matches)):
            pos, lv, title = matches[idx]
            if lv != chapter_level:
                continue
            end = matches[idx + 1][0] if idx + 1 < len(matches) else len(text)
            block = text[pos:end]
            first_nl = block.find("\n")
            body = _strip_md_noise(block[first_nl + 1 :].strip() if first_nl >= 0 else "")
            volumes[0].chapters.append(ParsedChapter(title=title, content=body, pos=pos))
        return volumes if volumes[0].chapters else []

    chapter_candidates = [lv for lv in sorted(set(levels)) if lv > volume_level]
    chapter_level = min(chapter_candidates) if chapter_candidates else volume_level + 1
    volumes: list[ParsedVolume] = []
    current: ParsedVolume | None = None

    for idx in range(start_idx, len(matches)):
        pos, lv, title = matches[idx]
        end = matches[idx + 1][0] if idx + 1 < len(matches) else len(text)
        block = text[pos:end]
        first_nl = block.find("\n")
        body = _strip_md_noise(block[first_nl + 1 :].strip() if first_nl >= 0 else "")

        if lv == volume_level or _looks_like_volume_title(title):
            current = ParsedVolume(
                title=title,
                outline=_outline_from_content(body) if body and len(body) < 200 else None,
                pos=pos,
            )
            volumes.append(current)
            continue
        if lv >= chapter_level or _looks_like_chapter_title(title):
            if current is None:
                current = ParsedVolume(title="第一卷", pos=pos)
                volumes.append(current)
            current.chapters.append(ParsedChapter(title=title, content=body, pos=pos))

    return [v for v in volumes if v.chapters]


def _outline_from_content(content: str, limit: int = 60) -> str | None:
    s = re.sub(r"\s+", "", content or "")
    if len(s) < 8:
        return None
    return (s[:limit] + ("…" if len(s) > limit else ""))


def parse_novel_txt(text: str, filename: str | None = None) -> ParsedNovel:
    text = (text or "").strip()
    if len(text) < 20:
        raise HTTPException(400, "文本太短,无法导入")

    title = _guess_title_from_text(text, filename)
    summary = None
    is_md = _is_md_file(filename) or bool(re.search(r"(?m)^#{1,6}\s+\S", text[:3000]))

    # 1) 织火导出格式 — 先拆再并行清洗
    export_chs = _parse_export_format(text)
    if export_chs and len(export_chs) >= 1:
        head = text.split("=" * 8)[0] if "=" in text else text[:800]
        hm = re.search(r"[《『「]([^》』」]{1,40})[》』」]", head)
        if hm:
            title = hm.group(1).strip()[:128]
        for line in head.split("\n"):
            s = line.strip().lstrip("#").strip()
            if s and "《" not in s and len(s) > 8 and not EXPORT_SEP.match(s):
                summary = s[:200]
                break
        volumes = _parse_pipeline_from_volumes([ParsedVolume(title="第一卷", chapters=export_chs, pos=0)])
        if volumes:
            return ParsedNovel(title=title, summary=summary, volumes=volumes, source="export")

    # 2) 常规「第X章 / 第X卷」标记(TXT/MD 通用)
    volumes = _parse_pipeline_from_volumes(_parse_by_markers(text))
    if volumes:
        return ParsedNovel(title=title, summary=summary, volumes=volumes, source="regex")

    # 3) Markdown 标题层级
    if is_md:
        md_volumes = _parse_pipeline_from_volumes(_parse_markdown_headings(text))
        if md_volumes:
            return ParsedNovel(title=title, summary=summary, volumes=md_volumes, source="markdown")

    # 4) 无标记:整篇作为一章
    whole = _strip_md_noise(text[:500000])
    if _chapter_len(whole) < MIN_CHAPTER_CHARS:
        raise HTTPException(400, f"未能解析出有效章节(正文需至少 {MIN_CHAPTER_CHARS} 字)")
    return ParsedNovel(
        title=title,
        summary=summary,
        volumes=[
            ParsedVolume(
                title="第一卷",
                pos=0,
                chapters=[
                    ParsedChapter(
                        title="第一章",
                        content=whole,
                        outline=_outline_from_content(text),
                        pos=0,
                    )
                ],
            )
        ],
        source="whole",
    )


def ai_enrich_import(
    db: Session,
    user_id: int,
    parsed: ParsedNovel,
    sample_text: str,
) -> ParsedNovel:
    """用 AI 补书名/简介;若规则只拆出 1 章且文本很长,尝试让 AI 给出目录再二次切分提示。"""
    sample = sample_text[:8000]
    ch_count = sum(len(v.chapters) for v in parsed.volumes)
    try:
        data = extract_json_object(
            db,
            user_id,
            (
                "你是网文整理助手。根据小说文本样例,输出 JSON:"
                '{"title":"书名","summary":"一句话简介50字内","note":"结构说明"}。'
                "只输出 JSON。"
            ),
            (
                f"## 当前猜测书名\n{parsed.title}\n\n"
                f"## 已识别章节数\n{ch_count}\n解析方式:{parsed.source}\n\n"
                f"## 文本样例\n{sample}"
            ),
        )
        t = str(data.get("title") or "").strip()
        if 2 <= len(t) <= 40:
            parsed.title = t[:128]
        s = str(data.get("summary") or "").strip()
        if s:
            parsed.summary = s[:200]
    except Exception:
        pass
    return parsed


def ensure_unique_titles(volumes: list[ParsedVolume]) -> None:
    seen: set[str] = set()
    for v in volumes:
        base_v = v.title
        n = 2
        while v.title in seen:
            v.title = f"{base_v}({n})"[:128]
            n += 1
        seen.add(v.title)
        ch_seen: set[str] = set()
        for ch in v.chapters:
            base = ch.title
            k = 2
            while ch.title in ch_seen:
                ch.title = f"{base}({k})"[:128]
                k += 1
            ch_seen.add(ch.title)
