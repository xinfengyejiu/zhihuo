"""章节正文规则自检(镜子,非闸门)。

纯规则、零模型调用,保存后即时返回:点名问题位置与改法。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# AI 写作高频套话/虚词(可后续做成可配置)
AI_WORDS: list[str] = [
    "深吸一口气",
    "倒吸一口凉气",
    "嘴角微微上扬",
    "目光深邃",
    "心中一凛",
    "脑海中闪过",
    "不可置信",
    "微微一怔",
    "缓缓道",
    "淡淡道",
    "冷冷道",
    "轻声道",
    "不由得",
    "下意识",
    "仿佛",
    "不禁",
    "玩味",
    "悄然",
    "一抹",
    "眸光",
    "握紧拳头",
    "与此同时",
    "值得一提",
    "令人感到",
    "这一刻",
    "他意识到",
    "她意识到",
]

CLICHE_OPENINGS: list[str] = [
    "夜色如墨",
    "夜深人静",
    "月光如水",
    "阳光明媚",
    "清晨的阳光",
    "夕阳西下",
    "寒风呼啸",
    "时光荏苒",
    "岁月如梭",
    "在一个",
    "话说",
    "却说",
]

META_PHRASES: list[str] = [
    "综上所述",
    "总而言之",
    "由此可见",
    "以上就是",
    "简单来说",
    "换句话说",
    "不难看出",
    "本章小结",
    "总结一下",
]

SENTENCE_END = set("。！？…!?；;")
# 用于统计「连续无句末标点」的字符:汉字/字母/数字
CONTENT_CHAR = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


@dataclass
class SelfCheckIssue:
    kind: str
    title: str
    detail: str
    advice: str
    excerpt: str | None = None
    count: int | None = None
    hits: list[str] = field(default_factory=list)


@dataclass
class SelfCheckResult:
    score: int
    word_count: int
    para_count: int
    avg_para_chars: int
    issue_count: int
    issues: list[SelfCheckIssue]
    summary: str

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def build_fix_instruction(issues: list[dict]) -> str:
    """把勾选的自检项拼成定点修改指令(给 polish / 生成接口)。"""
    if not issues:
        return ""
    lines = [
        "【自检定点修改】请只改下列点名问题,其余原文尽量不动。输出完整正文。",
        "",
    ]
    for i, issue in enumerate(issues, 1):
        title = str(issue.get("title") or issue.get("kind") or f"问题{i}").strip()
        detail = str(issue.get("detail") or "").strip()
        advice = str(issue.get("advice") or "").strip()
        excerpt = str(issue.get("excerpt") or "").strip()
        hits = issue.get("hits") or []
        lines.append(f"{i}. {title}")
        if detail:
            lines.append(f"   明细:{detail}")
        if hits:
            lines.append(f"   命中:{'、'.join(str(h) for h in hits[:16])}")
        if excerpt:
            lines.append(f"   原文片段:{excerpt[:120]}")
        if advice:
            lines.append(f"   修改要求:{advice}")
        lines.append("")
    return "\n".join(lines).strip()


def _paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n+", (text or "").replace("\r\n", "\n").strip())
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 单换行也视作段内;若全文只有单换行分段则再拆
        out.append(p)
    if len(out) <= 1 and "\n" in (text or ""):
        lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
        if len(lines) > 1:
            return lines
    return out or ([] if not (text or "").strip() else [(text or "").strip()])


def _count_words(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text or ""))


def _longest_no_punct_run(text: str) -> tuple[int, str]:
    best_len = 0
    best_excerpt = ""
    run = 0
    start = 0
    for i, ch in enumerate(text or ""):
        if ch in SENTENCE_END:
            if run > best_len:
                best_len = run
                best_excerpt = text[start:i][:80]
            run = 0
            start = i + 1
            continue
        if CONTENT_CHAR.match(ch):
            if run == 0:
                start = i
            run += 1
        # 空白/逗号等不打断「无句末标点」计数(与样本一致:逗号不算句末)
    if run > best_len:
        best_len = run
        best_excerpt = (text or "")[start : start + 80]
    return best_len, best_excerpt


def _find_ai_words(text: str) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for w in sorted(AI_WORDS, key=len, reverse=True):
        n = text.count(w)
        if n:
            hits.append((w, n))
    return hits


def run_self_check(text: str, character_names: list[str] | None = None) -> SelfCheckResult:
    raw = (text or "").strip()
    paras = _paragraphs(raw)
    word_count = _count_words(raw)
    para_count = len(paras)
    avg = int(round(word_count / para_count)) if para_count else 0
    issues: list[SelfCheckIssue] = []
    score = 100

    if word_count < 30:
        return SelfCheckResult(
            score=0,
            word_count=word_count,
            para_count=para_count,
            avg_para_chars=avg,
            issue_count=0,
            issues=[],
            summary="正文过短,暂不做自检。写完一章保存后会自动出现结果。",
        )

    # 1) AI 味禁用词
    ai_hits = _find_ai_words(raw)
    if ai_hits:
        total = sum(n for _, n in ai_hits)
        score -= min(25, 3 * total)
        issues.append(
            SelfCheckIssue(
                kind="ai_words",
                title=f"AI 味禁用词 {total} 处",
                detail="、".join(f"{w}×{n}" for w, n in ai_hits[:12]),
                advice="这些词是 AI 写作的高频特征。换个更具体的动词,或者直接删掉,多半更好。",
                count=total,
                hits=[f"{w}×{n}" for w, n in ai_hits],
            )
        )

    # 2) 开头套话
    head = paras[0] if paras else ""
    head_flat = re.sub(r"\s+", "", head)[:40]
    for phrase in CLICHE_OPENINGS:
        if head_flat.startswith(phrase) or phrase in head_flat[:24]:
            score -= 8
            issues.append(
                SelfCheckIssue(
                    kind="cliche_open",
                    title="开头套话",
                    detail=f"环境/开场套话「{phrase}」",
                    advice="开篇直接进事件或冲突,比铺夜色/阳光更能留住读者。",
                    excerpt=head[:60],
                )
            )
            break

    # 3) 元信息 / 段末总结腔
    for phrase in META_PHRASES:
        if phrase in raw:
            score -= 10
            # 找上下文
            idx = raw.find(phrase)
            excerpt = raw[max(0, idx - 8) : idx + len(phrase) + 24]
            issues.append(
                SelfCheckIssue(
                    kind="meta",
                    title="元信息 / 总结腔",
                    detail=f"正文出现「{phrase}」",
                    advice="正文只讲故事本身,别替读者做总结。删掉总结句,用情节收束。",
                    excerpt=excerpt,
                )
            )

    # 4) 连续无句末标点
    run_len, run_excerpt = _longest_no_punct_run(raw)
    if run_len >= 45:
        score -= 10 if run_len < 70 else 15
        issues.append(
            SelfCheckIssue(
                kind="no_punct",
                title=f"有连续 {run_len} 字没有句末标点",
                detail="正常中文句长多在 15–40 字;一长串不停顿会读得很累。",
                advice="偏长了,试着把这一串拆成两三句。",
                excerpt=run_excerpt,
                count=run_len,
            )
        )

    # 5) 单段过长
    long_paras = [(i, p, _count_words(p)) for i, p in enumerate(paras) if _count_words(p) > 160]
    if long_paras:
        score -= min(15, 5 * len(long_paras))
        i, p, n = max(long_paras, key=lambda x: x[2])
        issues.append(
            SelfCheckIssue(
                kind="long_para",
                title=f"单段过长({n} 字)",
                detail=f"第 {i + 1} 段超过 160 字" + (f",另有 {len(long_paras) - 1} 段偏长" if len(long_paras) > 1 else ""),
                advice="手机阅读建议单段 120–160 字以内,按动作/对话拆段更易读。",
                excerpt=p[:80],
                count=n,
            )
        )

    # 6) 段落人物衔接(轻量):相邻两段各自以不同人名开头,且后段人名未出现在前段
    names = [n for n in (character_names or []) if n and len(n) >= 2]
    names = sorted(set(names), key=len, reverse=True)
    if names and len(paras) >= 2:
        breaks = 0
        examples: list[str] = []
        for i in range(1, min(len(paras), 40)):
            prev, cur = paras[i - 1], paras[i]
            cur_start = None
            for name in names:
                if cur.startswith(name) or cur.lstrip("「『\"“").startswith(name):
                    cur_start = name
                    break
            if not cur_start:
                continue
            if cur_start in prev:
                continue
            prev_names = [n for n in names if n in prev]
            if not prev_names:
                continue
            breaks += 1
            if len(examples) < 2:
                examples.append(f"「{prev_names[0]}…」→「{cur_start}…」")
        if breaks >= 2:
            score -= 6
            issues.append(
                SelfCheckIssue(
                    kind="continuity",
                    title="上下段人物衔接可能断裂",
                    detail=f"约 {breaks} 处相邻段突然换主角开头" + ("：" + "；".join(examples) if examples else ""),
                    advice="换人出场时加一句视线/脚步/声音过渡,避免读者跟丢。",
                    count=breaks,
                )
            )

    score = max(0, min(100, score))
    issue_count = len(issues)
    if issue_count == 0:
        summary = f"{word_count} 字 / {para_count} 段(平均每段 {avg} 字) · 得分 {score}。未发现明显套话或标点问题。"
    else:
        summary = (
            f"{word_count} 字 / {para_count} 段(平均每段 {avg} 字) · 得分 {score}。"
            f"发现 {issue_count} 处可改进。"
        )

    return SelfCheckResult(
        score=score,
        word_count=word_count,
        para_count=para_count,
        avg_para_chars=avg,
        issue_count=issue_count,
        issues=issues,
        summary=summary,
    )
