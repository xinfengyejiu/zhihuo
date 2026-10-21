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

from .extract import extract_json_array, extract_json_object

IMPORT_EXTENSIONS = (".txt", ".text", ".md", ".markdown")

# 正文不足此字数的「章节」视为目录/章名引用,不入库
MIN_CHAPTER_CHARS = 100
# 超过该章数启用线程池并行清洗
_PARALLEL_CHAPTER_THRESHOLD = 24
_MAX_PARSE_WORKERS = max(2, min(8, (os.cpu_count() or 4)))

# 卷标题行:仅短标题(避免「第一卷 卷名 章名」被误当成卷)
VOLUME_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<title>"
    r"第[零〇一二三四五六七八九十百千万两\d]+卷(?:[ \t]+[^\n，。！？；：:]{1,16})?"
    r"|卷[零〇一二三四五六七八九十百千万两\d]+(?:[ \t]+[^\n，。！？；]{1,16})?"
    r"|VOLUME\s*\d+(?:[ \t]+[^\n]{1,20})?"
    r")\s*$",
    re.I | re.M,
)

# 规范章标题:「第一章 星空中的青铜巨棺」;亦兼容 Chapter1：xxx / Chapter1xxx
CHAPTER_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<title>"
    r"第[零〇一二三四五六七八九十百千万两\d]+[章节回话]"
    r"(?:[ \t]+[^\n，。！？；、]{1,40})?"
    r"|Chapter\s*\d+[^\n]{0,50}"
    r")\s*$",
    re.I | re.M,
)

# 「第一卷 第一章 标题」同行
CHAPTER_VOL_CH = re.compile(
    r"(?m)^\s*第[零〇一二三四五六七八九十百千万两\d]+卷\s+"
    r"(?P<title>第[零〇一二三四五六七八九十百千万两\d]+[章节回话][^\n]{0,50})\s*$"
)

# 「第一卷 卷名 小节标题」红袖打包常见(整行当章)
CHAPTER_VOL_PREFIX = re.compile(
    r"(?m)^\s*第[零〇一二三四五六七八九十百千万两\d]+卷[ \t]+(?P<title>[^\n]{2,70})\s*$"
)

# 「荷殇(1)」「心意明（三）」「chapter2一巴掌（二）」
CHAPTER_PAREN = re.compile(
    r"(?m)^\s*(?P<title>(?!作品相关)[^\n「」\"“”]{1,30}"
    r"[（(][一二三四五六七八九十百零〇\d]{1,5}[）)])\s*$"
)

# 红袖「作品相关 章名」(常与下一行重复章名并存,取此更稳)
CHAPTER_ZUOPIN = re.compile(
    r"(?m)^\s*作品相关[ \t]+(?P<title>[^\n]{1,60}?)\s*$"
)

# 「初识1」「妖女新娘2」「集市见闻3」短标题+序号
CHAPTER_TITLE_NUM = re.compile(
    r"(?m)^\s*(?P<title>(?!作品相关)[\u4e00-\u9fffA-Za-z]{1,12}\d{1,3})\s*$"
)

# 「卷一：妖从天降第一章洛纤纤（上）」
CHAPTER_JUAN_CH = re.compile(
    r"(?m)^\s*(?P<title>卷[零〇一二三四五六七八九十百千万两\d]+[：:\s].{0,50}"
    r"第[零〇一二三四五六七八九十百千万两\d]+[章节回话][^\n]{0,40})\s*$"
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

# 网文打包常见:独立成行的「正文 章名 / 正文 第X章 …」(非段落内出现)
CHAPTER_ZHENGWEN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?正文[ \t\u3000\ue5e5]+(?P<title>[^\n]{1,80}?)\s*$"
)

# 【第一章 xxx】整行
CHAPTER_BRACKET = re.compile(
    r"(?m)^\s*【\s*(?P<title>第[零〇一二三四五六七八九十百千万两\d]+[章节回话][^\n】]{0,40})\s*】\s*$"
)

# 纯 Markdown ATX 标题
MD_HEADING = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$", re.M)

# 部分精校源用 PUA 当段首缩进(常见 \ue5e5),导入时规整为空格
_PUA_INDENT = re.compile(r"[\ue000-\uf8ff]+")

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


def _cjk_score(text: str) -> int:
    """粗略估计中文小说正文质量(CJK 越多越好)。"""
    if not text:
        return -10**9
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    bad = text.count("\ufffd")
    # 惩罚替换符,奖励汉字
    return cjk * 3 - bad * 40 - (0 if cjk else 1000)


def decode_txt_bytes(data: bytes) -> str:
    """尽力识别网文 txt 编码(UTF-8 / GBK / GB18030 / UTF-16 等)。

    旧版严格解码导致大量红袖/起点导出 GBK 残缺字节直接失败;
    现改为打分择优,最后仍可用 replace 兜底,避免批量导入大面积失败。
    """
    if not data:
        raise HTTPException(400, "文件为空")

    # BOM 优先
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        enc = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16"
        text = data.decode(enc, errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        if text.strip():
            return text
    if data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        if text.strip():
            return text

    encodings = (
        "utf-8-sig",
        "utf-8",
        "gb18030",
        "gbk",
        "cp936",
        "big5",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
    )
    best_text = ""
    best_score = -10**9

    for enc in encodings:
        # 先严格,再容错
        for errors in ("strict", "replace"):
            try:
                text = data.decode(enc, errors=errors)
            except (UnicodeDecodeError, LookupError):
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if not text.strip():
                continue
            bad = text.count("\ufffd")
            # replace 模式下替换符过多则跳过
            if errors == "replace" and bad > max(50, len(text) // 30):
                continue
            score = _cjk_score(text)
            # 严格解码加分
            if errors == "strict":
                score += 500
            if score > best_score:
                best_score = score
                best_text = text

    if best_text.strip():
        return best_text

    # 最终兜底:按 GB18030 容错,几乎总能出字
    text = data.decode("gb18030", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if text.strip():
        return text
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if text.strip():
        return text
    raise HTTPException(400, "文件无有效文本内容")


def _clean_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    t = t.strip("#*=-—_|　")
    return t[:128] or "未命名章节"


def title_from_batch_novel_filename(filename: str) -> str:
    """批量多书导入:文件名即书名。

    例: ``001.[红袖vip完结]日租:黑街教父送上门--第II部:《特工总裁摸错房》.txt``
    → ``日租:黑街教父送上门--第II部:《特工总裁摸错房》``
    去掉开头编号与方括号/书名号式标签。
    """
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    lower = name.lower()
    for ext in IMPORT_EXTENSIONS:
        if lower.endswith(ext):
            name = name[: -len(ext)]
            break
    name = name.strip()
    # 开头序号: 001. / 12_ / 3- / 0001 /
    name = re.sub(r"^\d{1,6}\s*[._\-、．]?\s*", "", name)
    # 连续剥离 [标签] / 【标签】
    while True:
        m = re.match(r"^\[([^\]]*)\]\s*", name)
        if not m:
            m = re.match(r"^【[^】]*】\s*", name)
        if not m:
            break
        name = name[m.end() :]
    name = re.sub(r"\s+", " ", name).strip(" ._-\u3000")
    return (name or "未命名小说")[:128]


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
        # 批量多书风格文件名优先(去编号/方括号标签)
        batch = title_from_batch_novel_filename(filename)
        if batch and batch != "未命名小说":
            return batch
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
        cleaned = re.sub(r'\s+', '', t)[:40]
        return f"extra:{cleaned}"
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
            title = _normalize_chapter_title(ch.title) or _clean_title(ch.title)
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
    # 「第X章」走规范化;「正文 番外名」等自由标题直接保留
    title = _normalize_chapter_title(job.title) or _clean_title(job.title)
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


def _normalize_novel_text(text: str) -> str:
    """导入前规整:PUA 缩进 → 空格,统一换行。"""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = _PUA_INDENT.sub("  ", t)
    return t


def _freeform_chapter_title(raw: str, *, max_len: int = 60) -> str | None:
    """自由章名(正文/卷前缀/括号节):清洗并过滤明显非标题。"""
    title = _clean_title(raw or "")
    if not title:
        return None
    if title.startswith("作品相关"):
        title = _clean_title(title[len("作品相关") :])
    if not title or len(title) > max_len:
        return None
    if title.endswith(("。", "！", "？", "…", "；")):
        return None
    # 过白的广告/元信息
    if title.startswith(("资源", "本站", "附：", "附:", "作者", "文案", "ps：", "PS：")):
        return None
    return title[:128]


def _parse_by_markers(text: str) -> list[ParsedVolume]:
    """按卷/章标题行切分全文。"""
    raw: list[tuple[int, str, str]] = []
    for m in VOLUME_LINE.finditer(text):
        raw.append((m.start(), "volume", _clean_title(m.group("title"))))
    seen_pos: set[int] = set()
    for rx in (CHAPTER_LINE, CHAPTER_COMPACT, CHAPTER_AUTHOR_NOTE, CHAPTER_BRACKET, CHAPTER_VOL_CH):
        for m in rx.finditer(text):
            if m.start() in seen_pos:
                continue
            title = _normalize_chapter_title(m.group("title")) or _clean_title(m.group("title"))
            if not title:
                continue
            seen_pos.add(m.start())
            raw.append((m.start(), "chapter", title))
    # 「正文 章名」/「第一卷 … 小节」/「标题（一）」/「作品相关」等自由标题
    for rx in (
        CHAPTER_ZHENGWEN,
        CHAPTER_VOL_PREFIX,
        CHAPTER_PAREN,
        CHAPTER_ZUOPIN,
        CHAPTER_TITLE_NUM,
        CHAPTER_JUAN_CH,
    ):
        for m in rx.finditer(text):
            if m.start() in seen_pos:
                continue
            # 若同行已被识别为短卷名,跳过(避免卷行再当章)
            nl = text.find("\n", m.start())
            line = text[m.start() : nl if nl >= 0 else len(text)]
            if rx is CHAPTER_VOL_PREFIX and VOLUME_LINE.match(line):
                continue
            title = _freeform_chapter_title(m.group("title"), max_len=70)
            if not title:
                continue
            # 卷前缀行若其实是「第X章」已在上面处理过
            if rx is CHAPTER_VOL_PREFIX and CHAPTER_VOL_CH.match(line):
                continue
            # 标题+数字过短且像年份/纯数字跳过
            if rx is CHAPTER_TITLE_NUM and re.fullmatch(r"\d{1,4}", title):
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
    text = _normalize_novel_text(text or "").strip()
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


def _ai_split_sample(text: str, *, max_windows: int = 20) -> str:
    """给模型的抽样正文:文首 + 均匀窗口,带 pos 便于对齐。"""
    t = text or ""
    parts = [f"【pos=0】\n{t[:2800]}"]
    if len(t) <= 3200:
        return parts[0]
    usable = len(t) - 2800
    n = max(4, min(max_windows, usable // 500))
    step = max(400, usable // n)
    for k in range(n):
        pos = 2800 + k * step
        if pos >= len(t):
            break
        parts.append(f"【pos={pos}】\n{t[pos : pos + 420]}")
    blob = "\n\n".join(parts)
    return blob[:11500]


def _chapters_from_ai_items(text: str, items: list[dict]) -> list[ParsedChapter]:
    """根据 AI 返回的 title+anchor 在原文定位并切章。"""
    hits: list[tuple[int, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _clean_title(str(item.get("title") or item.get("name") or ""))
        anchor = str(item.get("anchor") or item.get("start") or item.get("quote") or "").strip()
        if len(_norm_body(anchor)) < 10:
            continue
        # 在去空白键上找,再映射回原文位置
        key = _norm_body(text)
        akey = _norm_body(anchor)
        if not akey or akey not in key:
            # 缩短 anchor 再试
            found = False
            for L in (28, 20, 14):
                if len(akey) >= L and akey[:L] in key:
                    akey = akey[:L]
                    found = True
                    break
            if not found:
                continue
        idx = key.find(akey)
        if idx < 0:
            continue
        # 空白映射:key 下标 → 原文下标
        pos = 0
        seen = 0
        while pos < len(text) and seen < idx:
            if not text[pos].isspace():
                seen += 1
            pos += 1
        hits.append((pos, (title or f"第{len(hits) + 1}章")[:128]))

    if not hits:
        return []
    # 按位置去重(50 字内合并)
    hits.sort(key=lambda x: x[0])
    merged: list[tuple[int, str]] = []
    for pos, title in hits:
        if merged and abs(pos - merged[-1][0]) < 50:
            # 保留更像样的标题
            if _title_quality(title) > _title_quality(merged[-1][1]):
                merged[-1] = (merged[-1][0], title)
            continue
        merged.append((pos, title))
    if len(merged) < 2:
        return []

    # 若首章不在文首,把文首并入第一章
    if merged[0][0] > 80:
        merged.insert(0, (0, "开篇"))

    out: list[ParsedChapter] = []
    for i, (pos, title) in enumerate(merged):
        end = merged[i + 1][0] if i + 1 < len(merged) else len(text)
        body = text[pos:end].strip()
        # 去掉开头可能重复的标题行
        body = _strip_leading_title(title, body)
        if _chapter_len(body) < MIN_CHAPTER_CHARS:
            continue
        out.append(
            ParsedChapter(
                title=title[:128],
                content=body,
                outline=_outline_from_content(body),
                pos=pos,
            )
        )
    return out


def ai_split_chapters(
    db: Session,
    user_id: int,
    text: str,
    *,
    novel_id: int | None = None,
    title_hint: str = "",
) -> list[ParsedChapter] | None:
    """规则无法分章时,用大模型根据抽样推断目录并用 anchor 回切原文。"""
    raw = _normalize_novel_text(text or "").strip()
    if _chapter_len(raw) < MIN_CHAPTER_CHARS * 4:
        return None

    sample = _ai_split_sample(raw)
    system = (
        "你是中文网文分章编辑。文本没有规范「第X章」标记,请根据剧情/场景切换推断章节。"
        "只输出 JSON 对象:"
        '{"chapters":[{"title":"章名","anchor":"该章开头在下文片段中出现的原文连续句子(至少14字,必须一字不差复制)}]}。'
        "要求:章数尽量完整(常见 10～200);anchor 必须从提供的【pos=】片段里原样复制;"
        "不要广告行;不要解释。"
    )
    user_msg = (
        f"书名提示:{title_hint or '(无)'}\n全文约 {len(raw)} 字。\n"
        f"## 抽样正文\n{sample}"
    )
    try:
        data = extract_json_object(db, user_id, system, user_msg, novel_id=novel_id)
    except Exception:
        data = {}
    items = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = []
    chapters = _chapters_from_ai_items(raw, items)
    if len(chapters) >= 2:
        return chapters

    # 抽样不够时分段请求(控制调用次数)
    window = 14000
    overlap = 600
    max_calls = 8
    acc: list[dict] = []
    pos = 0
    calls = 0
    while pos < len(raw) and calls < max_calls:
        chunk = raw[pos : pos + window]
        try:
            part = extract_json_array(
                db,
                user_id,
                (
                    "从这段小说正文中找出章节起始。输出 JSON 数组:"
                    '[{"title":"章名","anchor":"章开头原文连续≥14字(必须从本段复制)"}]。'
                    "若本段没有新章开始则输出 []。只输出 JSON 数组。"
                ),
                f"## 正文片段(全局起点约 {pos})\n{chunk[:11000]}",
                novel_id=novel_id,
            )
            acc.extend(part)
        except Exception:
            pass
        calls += 1
        nxt = pos + window - overlap
        if nxt <= pos:
            break
        pos = nxt
        if pos + MIN_CHAPTER_CHARS * 2 >= len(raw):
            break

    chapters = _chapters_from_ai_items(raw, acc)
    return chapters if len(chapters) >= 2 else None


def maybe_ai_split_parsed(
    db: Session,
    user_id: int,
    parsed: ParsedNovel,
    full_text: str,
    *,
    novel_id: int | None = None,
) -> ParsedNovel:
    """若当前仍是整本一章,调用大模型分章并替换。"""
    ch_count = sum(len(v.chapters) for v in parsed.volumes)
    if ch_count > 1:
        return parsed
    if _chapter_len(full_text) < MIN_CHAPTER_CHARS * 4:
        return parsed
    try:
        chapters = ai_split_chapters(
            db,
            user_id,
            full_text,
            novel_id=novel_id,
            title_hint=parsed.title,
        )
    except Exception:
        return parsed
    if not chapters or len(chapters) < 2:
        return parsed
    return ParsedNovel(
        title=parsed.title,
        summary=parsed.summary,
        volumes=[ParsedVolume(title="第一卷", chapters=chapters, pos=0)],
        source="ai-split",
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
