"""网文智能分段:优先 AI 按小说版式重排;失败时回退到短段落规则切分。

硬约束:最终结果去掉空白后须与原文一致(只插换行)。
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

_SENT_END = set("。！？!?…")
_TRAIL = set("”』」）)\"'")
_DIALOG_OPEN = set("「『“\"")

# 单次送给模型的字数(按无空白计)。常见单章 3～6 千字一次打完,避免串行两次等模型
_AI_CHUNK_CHARS = 8000

SYSTEM_PROMPT = """你是中文网络小说排版编辑。只做「分段」,绝不改写。

硬性规则:
1. 禁止增删改任何汉字、标点、数字、字母与符号;只能插入换行或空行。
2. 必须按「手机网文」版式,禁止排成报纸/散文/议论文那种大段:
   - 一句对话(「」『』“”)单独成段;前后短促的「他说/她追问」可与该句对话同段,多句对话绝不可糊在一段。
   - 叙述、动作、心理:一句一段为佳,最多两句;超过两句必须拆段。
   - 环境/回忆描写:最多两句一段,绝不要三句以上挤在一块。
   - 目标观感:手机屏上多数段落只有一行到两行,读起来有呼吸感,而不是一整块「文章」。
3. 段与段之间用一个空行分隔(即段落之间空一行)。
4. 直接输出排版后的完整正文,不要前言、后记、Markdown、代码围栏或解释。

反例(禁止):把对话嵌进很长叙述里,弄成 80～200 字的大段。
正例风格:对话单独空行;动作一两句后空行;再接下一段。"""


def _norm_ws_key(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _collapse_blank_lines(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _split_long_block(block: str, *, max_para: int, min_para: int) -> list[str]:
    s = re.sub(r"\s+", "", block)
    if not s:
        return []
    if len(s) <= max_para:
        return [s]

    paras: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(s)

    def flush():
        nonlocal buf
        if buf:
            paras.append("".join(buf))
            buf = []

    while i < n:
        ch = s[i]
        buf.append(ch)
        size = len(buf)

        if ch in _DIALOG_OPEN and size > 1 and size - 1 >= max(8, min_para // 2):
            open_ch = buf.pop()
            flush()
            buf = [open_ch]
            i += 1
            continue

        if ch in _SENT_END:
            j = i + 1
            while j < n and s[j] in _TRAIL:
                buf.append(s[j])
                j += 1
            i = j
            if len(buf) >= min_para:
                flush()
            continue

        if size >= max_para:
            joined = "".join(buf)
            cut = -1
            for k in range(len(joined) - 1, min_para - 1, -1):
                if joined[k] in _SENT_END:
                    cut = k + 1
                    while cut < len(joined) and joined[cut] in _TRAIL:
                        cut += 1
                    break
            if cut > 0:
                paras.append(joined[:cut])
                rest = joined[cut:]
                buf = list(rest) if rest else []
            elif size >= int(max_para * 1.5):
                paras.append(joined[:max_para])
                buf = list(joined[max_para:])
            i += 1
            continue

        i += 1

    flush()
    return [p for p in paras if p]


def smart_reparagraph(
    text: str,
    *,
    max_para_chars: int = 42,
    min_para_chars: int = 12,
) -> str:
    """规则回退:偏网文短段落(对话易独立、手机一行感)。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    key_before = _norm_ws_key(raw)
    collapsed = _collapse_blank_lines(raw)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", collapsed) if b.strip()] or [collapsed]
    parts: list[str] = []
    for b in blocks:
        parts.extend(_split_long_block(b, max_para=max_para_chars, min_para=min_para_chars))
    out = "\n\n".join(p for p in parts if p)
    if _norm_ws_key(out) != key_before:
        parts2 = _split_long_block(key_before, max_para=max_para_chars, min_para=min_para_chars)
        out2 = "\n\n".join(parts2)
        if _norm_ws_key(out2) == key_before:
            return out2
        return _collapse_blank_lines(raw)
    return out


def _chunk_for_ai(text: str, limit: int = _AI_CHUNK_CHARS) -> list[str]:
    """按空白去除后的长度切片,尽量在句末断开。"""
    key = _norm_ws_key(text)
    if len(key) <= limit:
        return [key] if key else []
    chunks: list[str] = []
    i = 0
    n = len(key)
    while i < n:
        end = min(i + limit, n)
        if end < n:
            cut = end
            for k in range(end, i + limit // 2, -1):
                if key[k - 1] in _SENT_END:
                    cut = k
                    break
            end = cut
        chunks.append(key[i:end])
        i = end
    return chunks


def _accept_ai_output(original: str, ai_text: str) -> str | None:
    """校验 AI 输出:去空白后必须与原文一致。"""
    if not ai_text or not ai_text.strip():
        return None
    # 去掉可能的代码围栏
    t = ai_text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:\w+)?\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
        t = t.strip()
    t = _collapse_blank_lines(t)
    if _norm_ws_key(t) == _norm_ws_key(original):
        return t
    return None


def _already_novel_dense(text: str) -> bool:
    """已是短段网文版式时跳过模型,秒回。"""
    collapsed = _collapse_blank_lines(text)
    key = _norm_ws_key(collapsed)
    if len(key) < 80:
        return True
    paras = [p.strip() for p in collapsed.split("\n\n") if p.strip()]
    if len(paras) < max(4, len(key) // 100):
        return False
    avg = len(key) / max(1, len(paras))
    longish = sum(1 for p in paras if len(_norm_ws_key(p)) > 80)
    return avg <= 50 and longish / len(paras) <= 0.15


async def _reparagraph_one_chunk(
    chunk: str,
    idx: int,
    total: int,
    complete: Callable[[list[dict]], Awaitable[str]],
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"请将以下正文按手机网文格式重新分段"
                f"(第 {idx + 1}/{total} 片):对话尽量独立成段,"
                f"叙述/动作一句或两句一段,段间空一行。只插换行,勿改字。\n\n"
                f"{chunk}"
            ),
        },
    ]
    try:
        ai_out = await complete(messages)
    except Exception:
        return smart_reparagraph(chunk)
    accepted = _accept_ai_output(chunk, ai_out or "")
    return accepted if accepted is not None else smart_reparagraph(chunk)


async def ai_reparagraph_novel(
    text: str,
    *,
    complete: Callable[[list[dict]], Awaitable[str]],
) -> tuple[str, str]:
    """
    AI 网文分段。
    complete(messages) -> 模型完整输出字符串。
    返回 (结果正文, 来源: ai|rules|skip)。
    """
    raw = (text or "").strip()
    if not raw:
        return "", "empty"

    # 已是短段网文 → 只做空白规整,不调模型
    if _already_novel_dense(raw):
        return _collapse_blank_lines(raw), "skip"

    chunks = _chunk_for_ai(raw)
    n = len(chunks)
    if n == 1:
        out_parts = [await _reparagraph_one_chunk(chunks[0], 0, 1, complete)]
    else:
        # 超长章多片并行,避免串行翻倍等
        out_parts = list(
            await asyncio.gather(
                *[_reparagraph_one_chunk(c, i, n, complete) for i, c in enumerate(chunks)]
            )
        )

    merged = "\n\n".join(p.strip() for p in out_parts if p.strip())
    if _norm_ws_key(merged) != _norm_ws_key(raw):
        return smart_reparagraph(raw), "rules"
    rules = smart_reparagraph(raw)
    # AI 段太稀(仍像文章大块) → 用更密的短段规则补刀
    ai_breaks = merged.count("\n\n")
    need_breaks = max(2, len(_norm_ws_key(raw)) // 80)
    if ai_breaks < need_breaks and rules.count("\n\n") > ai_breaks:
        return rules, "rules"
    if merged != rules:
        return merged, "ai"
    return merged, "ai" if out_parts else "rules"
