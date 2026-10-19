"""上下文组装:长篇百万字场景下按「压缩记忆」注入,绝不灌全文。

生成一章正文时只注入:
  1. 全书规则(小说宪法)
  2. 本书信息 + 本章出场人物卡
  3. 滚动前情提要(短)
  4. 邻近章纲窗口(前几章要点 + 本章 + 后几章预告) + 本卷大纲
  5. 上一章结尾短尾巴(衔接语气,非全文)
  6. 续写/润色/重写时:当前章已有正文(有上限)
  7. 作者本次指令

总大纲文档只在「本章章纲为空」时给一小段蓝图摘录,避免每次塞整本大纲。
"""

from sqlalchemy.orm import Session

from ..models import (
    Character,
    Chapter,
    Foreshadow,
    Novel,
    NovelRule,
    OutlineDoc,
    RollingSummary,
    Volume,
)

# ---- 长篇上下文预算(字,约等于中文 token) ----
SUMMARY_CAP = 1200
PREV_ENDING_CAP = 900
NEARBY_BEFORE = 3
NEARBY_AFTER = 2
BLUEPRINT_CAP = 1600  # 仅章纲缺失时启用
CURRENT_CONTINUE_CAP = 2500
CURRENT_POLISH_CAP = 8000
RULES_CAP = 4000
CHAR_LIMIT = 8

PERSONA = (
    "你是一位资深中文长篇小说作者,正在为一部可能长达百万字的连载网文创作「单章正文」。"
    "你只能依据消息里给出的规则、人物、前情提要、邻近章纲与上章结尾短尾巴推进,"
    "禁止假设自己读过全书全文,也禁止复述或扩写前文大段内容。"
    "本章必须完成「本章目标/剧情要点」,与前后章纲衔接,文风统一、人设连贯。"
    "直接输出正文,不要标题编号、作者注或任何元信息。"
)


def _character_card(c: Character) -> str:
    parts = [f"【{c.name}】"]
    if c.aliases:
        parts.append(f"别名:{c.aliases}")
    role_label = {"protagonist": "主角", "support": "配角", "villain": "反派", "minor": "龙套"}.get(c.role, c.role)
    parts.append(f"定位:{role_label}")
    if c.occupation:
        parts.append(f"职业:{c.occupation}")
    if c.organization:
        parts.append(f"组织:{c.organization}")
    if c.appearance:
        parts.append(f"外貌:{c.appearance}")
    if c.personality:
        parts.append(f"性格:{c.personality}")
    if c.goal:
        parts.append(f"目标:{c.goal}")
    if c.notes:
        parts.append(f"备注:{c.notes}")
    return ";".join(parts)


def _clip(text: str | None, cap: int) -> str:
    t = (text or "").strip()
    if len(t) <= cap:
        return t
    return t[: cap - 1] + "…"


def _nearby_chapter_window(db: Session, novel_id: int, chapter: Chapter) -> str:
    """只给邻近章节的「标题+剧情要点」,不给正文。"""
    rows = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel_id)
        .order_by(Chapter.sort, Chapter.id)
        .all()
    )
    if not rows:
        return "(暂无章节目录)"
    idx = next((i for i, c in enumerate(rows) if c.id == chapter.id), 0)
    start = max(0, idx - NEARBY_BEFORE)
    end = min(len(rows), idx + NEARBY_AFTER + 1)
    lines: list[str] = []
    for i in range(start, end):
        c = rows[i]
        mark = "←本章" if c.id == chapter.id else ("已过" if i < idx else "预告")
        outline = (c.outline or "").strip() or "(未填要点)"
        lines.append(f"- [{mark}] {c.title}: {_clip(outline, 80)}")
    return "\n".join(lines)


def _blueprint_excerpt(db: Session, novel_id: int) -> str | None:
    """总大纲摘录:仅章纲缺失时使用,严格封顶。"""
    docs = db.query(OutlineDoc).filter_by(novel_id=novel_id).order_by(OutlineDoc.sort, OutlineDoc.id).all()
    if not docs:
        return None
    parts: list[str] = []
    total = 0
    for d in docs:
        piece = f"### {d.title}\n{_clip(d.content, BLUEPRINT_CAP)}"
        parts.append(piece)
        total += len(piece)
        if total >= BLUEPRINT_CAP:
            break
    text = "\n\n".join(parts)
    return _clip(text, BLUEPRINT_CAP) or None


def _open_foreshadows(db: Session, novel_id: int, limit: int = 8) -> str:
    rows = (
        db.query(Foreshadow)
        .filter(Foreshadow.novel_id == novel_id, Foreshadow.status.in_(("planted", "overdue")))
        .order_by(Foreshadow.id.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return ""
    return "\n".join(f"- ({r.status}) {_clip(r.description, 60)}" for r in rows)


def build_chapter_messages(
    db: Session,
    novel: Novel,
    chapter: Chapter,
    mode: str,
    instruction: str | None,
    previous_chapter: Chapter | None = None,
    outline_text: str | None = None,
    current_content: str | None = None,
) -> list[dict]:
    # ---- 规则层(封顶) ----
    rules = (
        db.query(NovelRule)
        .filter_by(novel_id=novel.id, enabled=True)
        .order_by(NovelRule.priority.desc())
        .all()
    )
    rule_lines: list[str] = []
    used = 0
    for r in rules:
        line = f"- {_clip(r.content, 200)}"
        if used + len(line) > RULES_CAP:
            break
        rule_lines.append(line)
        used += len(line)
    rule_text = "\n".join(rule_lines) or "(暂无)"

    # ---- 人物层:优先本章出场,否则取少量主角卡 ----
    involved_ids = chapter.characters_involved or []
    if involved_ids:
        chars = db.query(Character).filter(Character.novel_id == novel.id, Character.id.in_(involved_ids)).all()
    else:
        chars = (
            db.query(Character)
            .filter_by(novel_id=novel.id)
            .order_by(Character.id)
            .limit(CHAR_LIMIT)
            .all()
        )
    char_text = "\n".join(_character_card(c) for c in chars[:CHAR_LIMIT]) or "(暂无人物设定)"

    # ---- 滚动前情(短记忆,支撑百万字连载) ----
    summary_row = db.get(RollingSummary, novel.id)
    summary_text = _clip(
        summary_row.content if summary_row and summary_row.content else "(这是开篇或暂无前情提要;请按章纲开写)",
        SUMMARY_CAP,
    )

    # ---- 上章结尾短尾巴(只为语气/场景衔接) ----
    prev_ending = ""
    if previous_chapter and previous_chapter.current_version_id:
        prev_version = next(
            (v for v in previous_chapter.versions if v.id == previous_chapter.current_version_id), None
        )
        if prev_version and prev_version.content:
            raw = prev_version.content.strip()
            prev_ending = raw[-PREV_ENDING_CAP:] if len(raw) > PREV_ENDING_CAP else raw

    # ---- 卷纲 + 邻近章纲窗口 ----
    volume = db.get(Volume, chapter.volume_id) if chapter.volume_id else None
    chapter_outline = (chapter.outline or "").strip()
    outline_parts = [
        f"邻近章纲窗口(仅要点,无正文):\n{_nearby_chapter_window(db, novel.id, chapter)}",
    ]
    if volume:
        outline_parts.insert(0, f"本卷《{volume.title}》大纲:{_clip(volume.outline, 400) or '(未填)'}")
    outline_parts.append(f"本章目标/剧情要点(必须完成):{chapter_outline or '(未填,请按邻近章纲与前情合理推进)'}")
    if chapter.foreshadow_note:
        outline_parts.append(f"本章伏笔任务(必须体现):{chapter.foreshadow_note}")
    foreshadows = _open_foreshadows(db, novel.id)
    if foreshadows:
        outline_parts.append(f"未回收伏笔(可呼应,勿堆砌):\n{foreshadows}")

    novel_info = f"书名《{novel.title}》"
    if novel.summary:
        novel_info += f",简介:{_clip(novel.summary, 200)}"

    mode_directives = {
        "chapter": "请撰写本章完整正文,约 2000-3500 字,完成章纲要点,结尾留钩子。不要复述前情提要。",
        "continue": "请从「当前正文」末尾自然续写 800-1500 字,不重复已写内容,继续完成章纲。",
        "polish": "请润色「当前正文」:提升文笔与节奏,不改变剧情、人物与事实。输出润色后的完整正文。",
        "rewrite": "请按指令重写本章;若有「当前正文」可作参考骨架,仍须符合章纲。",
    }
    directive = mode_directives.get(mode, mode_directives["chapter"])
    # 自检定点修改:只动点名几处,其余尽量原样
    if instruction and str(instruction).lstrip().startswith("【自检定点修改】"):
        directive = (
            "这是「自检定点修改」任务:只改正文中作者指令列出的那几处问题,"
            "其余句子尽量逐字保留,不要整体重写、不要扩写情节、不要改人名地名。"
            "直接输出修改后的完整正文,不要解释、不要列修改清单。"
        )
    # 按审核驳回意见改稿:优先消化驳回点,保留可用情节
    elif instruction and str(instruction).lstrip().startswith("【按驳回建议修改】"):
        directive = (
            "这是「按驳回建议修改」任务:对照作者指令中的「审核驳回意见」修改「当前正文」。"
            "优先解决驳回意见里点名的问题(如人设、节奏、AI味、逻辑、章纲未完成等);"
            "未点名的段落尽量保留原意与可用对白,不要无故整章重写或另起炉灶。"
            "直接输出修改后的完整正文,不要解释、不要列修改清单。"
        )

    system = (
        f"{PERSONA}\n\n"
        f"## 全书规则(必须遵守)\n{rule_text}\n\n"
        f"## 本书信息\n{novel_info}\n\n"
        f"## 人物设定\n{char_text}"
    )

    user_parts = [
        f"## 前情提要(滚动压缩记忆,不是全文)\n{summary_text}",
        "## 章纲推进\n" + "\n".join(outline_parts),
    ]

    # 章纲缺失时才补一小段总大纲蓝图;有章纲则不再塞整本大纲
    if not chapter_outline:
        blueprint = outline_text or _blueprint_excerpt(db, novel.id)
        if blueprint:
            user_parts.append(
                f"## 创作蓝图摘录(本章章纲未填,仅供参考,勿扩写成前文)\n{_clip(blueprint, BLUEPRINT_CAP)}"
            )

    if prev_ending:
        user_parts.append(
            f"## 上一章结尾(仅衔接,勿复述或续抄)\n{prev_ending}"
        )

    body = (current_content or "").strip()
    if mode == "continue" and body:
        user_parts.append(f"## 当前正文(续写起点,取末尾)\n{_clip(body[-CURRENT_CONTINUE_CAP:], CURRENT_CONTINUE_CAP)}")
    elif mode in ("polish", "rewrite") and body:
        user_parts.append(f"## 当前正文\n{_clip(body, CURRENT_POLISH_CAP)}")

    if instruction:
        user_parts.append(f"## 作者本次指令(优先执行)\n{instruction}")
    user_parts.append(f"## 任务\n{directive}\n\n本章标题:{chapter.title}")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


# 章纲生成时已有目录的压缩预算
OUTLINE_EXISTING_FULL = 36  # 少于此数可全量列标题
OUTLINE_EXISTING_TAIL = 24  # 压缩时保留末尾这么多章标题
OUTLINE_EXISTING_CAP = 2800  # 已有章节区块总字数上限
OUTLINE_BLUEPRINT_CATALOG_CAP = 12000
OUTLINE_BLUEPRINT_NEXT_CAP = 6000
OUTLINE_SUMMARY_CAP = 900


def looks_like_continue_outline(instruction: str | None, existing_count: int) -> bool:
    """作者要求续写/往后生成,或已有大量章节时走 next 模式。"""
    text = (instruction or "").strip()
    keys = ("继续", "往后", "接着", "后续", "再生成", "下一", "续写", "补全后面", "从后面", "往下")
    if text and any(k in text for k in keys):
        return True
    return existing_count >= 40 and not text


def compress_existing_chapters(
    chapters: list[Chapter],
    volumes: list[Volume],
    *,
    full_limit: int = OUTLINE_EXISTING_FULL,
    tail: int = OUTLINE_EXISTING_TAIL,
    cap: int = OUTLINE_EXISTING_CAP,
) -> str:
    """章节过多时压缩已有目录:卷统计 + 早期摘要 + 末尾标题清单。"""
    if not chapters:
        return "(还没有章节,请从大纲开头完整拆分)"
    n = len(chapters)
    if n <= full_limit:
        text = "\n".join(f"- {c.title}" for c in chapters)
        return text if len(text) <= cap else text[:cap] + "\n…(已截断)"

    vol_map = {v.id: v.title for v in volumes}
    # 按卷统计
    by_vol: dict[str, list[Chapter]] = {}
    for c in chapters:
        key = vol_map.get(c.volume_id) if c.volume_id else "未分卷"
        by_vol.setdefault(key or "未分卷", []).append(c)

    summary_lines = [f"已有章节共 {n} 章(已自动压缩,勿重复下列标题):"]
    for vtitle, items in by_vol.items():
        first, last = items[0].title, items[-1].title
        mid = items[len(items) // 2].title if len(items) >= 3 else ""
        if mid and mid not in (first, last):
            summary_lines.append(f"- 《{vtitle}》{len(items)}章: {first} → …{mid}… → {last}")
        else:
            summary_lines.append(f"- 《{vtitle}》{len(items)}章: {first} → {last}")

    head_n = max(4, min(8, n // 20))
    head = chapters[:head_n]
    tail_chs = chapters[-tail:]
    summary_lines.append(f"\n开篇抽样({len(head)}章):")
    summary_lines.extend(f"- {c.title}" for c in head)
    summary_lines.append(f"\n最近已有章节(共{len(tail_chs)}章,请从最后一章之后继续):")
    summary_lines.extend(f"- {c.title}" for c in tail_chs)
    text = "\n".join(summary_lines)
    if len(text) > cap:
        text = text[:cap] + "\n…(目录压缩截断)"
    return text


def build_outline_messages(
    db: Session,
    novel: Novel,
    instruction: str | None,
    count: int = 12,
    outline_text: str | None = None,
    mode: str = "catalog",
) -> list[dict]:
    rules = db.query(NovelRule).filter_by(novel_id=novel.id, enabled=True).order_by(NovelRule.priority.desc()).all()
    rule_text = "\n".join(f"- {r.content}" for r in rules) or "(暂无)"
    if len(rule_text) > RULES_CAP:
        rule_text = rule_text[:RULES_CAP] + "…"
    chars = db.query(Character).filter_by(novel_id=novel.id).limit(12).all()
    char_text = "\n".join(_character_card(c) for c in chars) or "(暂无人物设定)"
    volumes = db.query(Volume).filter_by(novel_id=novel.id).order_by(Volume.sort).all()
    volume_text = "\n".join(f"- 《{v.title}》:{_clip(v.outline or '(无大纲)', 80)}" for v in volumes) or "(还没有卷)"
    existing = db.query(Chapter).filter_by(novel_id=novel.id).order_by(Chapter.sort, Chapter.id).limit(800).all()
    existing_count = len(existing)

    # 自动判定续写:避免把几百章标题全塞进上下文
    if mode != "next" and looks_like_continue_outline(instruction, existing_count):
        mode = "next"
    if mode == "next" and count < 8:
        count = 12

    existing_text = compress_existing_chapters(existing, volumes)
    summary_row = db.get(RollingSummary, novel.id)
    summary_text = summary_row.content if summary_row and summary_row.content else "(暂无前情)"
    summary_text = _clip(summary_text, OUTLINE_SUMMARY_CAP)

    blueprint_cap = OUTLINE_BLUEPRINT_NEXT_CAP if mode == "next" else OUTLINE_BLUEPRINT_CATALOG_CAP
    if existing_count >= 80:
        blueprint_cap = min(blueprint_cap, 5000)
    blueprint = _clip(outline_text or "", blueprint_cap) if outline_text else "(未提供)"

    system = (
        "你是资深网文主编,负责把全书大纲拆成可以入库的卷和章节目录。\n"
        "只输出一个 JSON 对象,不要写前言、分析、正文或 Markdown 说明。\n"
        "上下文中的「已有章节」可能是压缩摘要:只保证不重复末尾列出的标题,并接着往后规划。\n\n"
        f"## 全书规则\n{rule_text}\n\n## 人物设定\n{char_text}"
    )
    if mode == "next":
        task = (
            f"请基于全书大纲与已有进度,从「最近已有章节」之后继续规划接下来约 {count} 章"
            f"(可分到合理卷下)。\n"
            "不要重复已有章节标题;不要重写全书目录;本轮只输出新增的卷章 JSON。\n"
            "若作者要求继续往后,就接着最后一章的剧情推进。"
        )
    elif outline_text:
        task = (
            "请把「全书大纲」拆成完整的卷和章节目录,覆盖大纲里的全部剧情阶段。\n"
            "大纲里已经写了卷名或章名的,按原标题提取,不要改名、不要合并、不要漏章。\n"
            "大纲只是故事梗概、没有逐章标题时,按剧情节点拆卷拆章,每章对应一个明确事件。\n"
            "不要只写前十几章。不要重复「已有章节」里的标题。"
            + (f"\n本书已有 {existing_count} 章,优先补全尚未覆盖的后半段,不要从头重列。" if existing_count else "")
        )
    else:
        task = (
            f"本书还没有总大纲。请根据简介、人物和规则,规划开篇约 {count} 章的目录,并分成合理的卷。"
        )
    extra = f"\n作者补充要求:{instruction}" if instruction else ""
    compress_note = (
        f"\n(提示:已有 {existing_count} 章,目录已自动压缩注入,请只续写新增章节。)"
        if existing_count > OUTLINE_EXISTING_FULL
        else ""
    )
    user = (
        f"书名《{novel.title}》\n简介:{_clip(novel.summary or '(未填)', 200)}\n\n"
        f"## 全书大纲(创作蓝图){compress_note}\n{blueprint}\n\n"
        f"## 已有卷\n{volume_text}\n\n"
        f"## 已有章节\n{existing_text}\n\n"
        f"## 前情提要\n{summary_text}\n\n"
        f"## 任务\n{task}{extra}\n\n"
        "只输出如下 JSON(可以用 ```json 代码块包裹)。剧情要点每章一句话,不超过 40 字:\n"
        "{\n"
        '  "volumes": [\n'
        "    {\n"
        '      "title": "第一卷 卷名",\n'
        '      "outline": "本卷一句话概要",\n'
        '      "chapters": [\n'
        '        {"title": "章节标题", "outline": "本章剧情要点", "characters": "出场人物"}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_summary_messages(previous_summary: str, chapter_title: str, chapter_content: str) -> list[dict]:
    system = (
        "你是小说连载助手,负责维护「前情提要」。这是百万字长篇的压缩记忆,不是文摘堆砌。"
        "要求:简明扼要、按时间线、保留关键事件、人物动机变化、新登场人物与重要伏笔,"
        f"总长度控制在 {SUMMARY_CAP} 字以内。"
    )
    user = (
        f"## 已有前情提要\n{previous_summary or '(无)'}\n\n"
        f"## 最新章节《{chapter_title}》正文\n{_clip(chapter_content, 6000)}\n\n"
        "请输出合并更新后的完整前情提要(覆盖全文输出,不要只写增量)。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
