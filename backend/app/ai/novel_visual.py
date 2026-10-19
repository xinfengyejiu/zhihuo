"""小说分镜:节拍规划 + 一致性 prompt 拼装(复用 media_gen 生图/生视频)。"""

from __future__ import annotations

import json
import re
from typing import Any

from ..models import Character, Chapter, Novel, NovelScene

ROLE_CN = {"protagonist": "主角", "villain": "反派", "support": "配角", "minor": "龙套"}


def _split_beats(text: str, n: int) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return [f"节拍 {i + 1}" for i in range(n)]
    # 优先按空行分段,其次按句号
    paras = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if len(paras) < 2:
        paras = [s.strip() for s in re.split(r"(?<=[。！？…])", raw) if s.strip()]
    if not paras:
        paras = [raw]
    if len(paras) >= n:
        step = len(paras) / n
        # 取连续 2 句拼成更完整节拍,便于写细分镜
        out: list[str] = []
        for i in range(n):
            idx = min(len(paras) - 1, int(i * step))
            chunk = paras[idx]
            if idx + 1 < len(paras):
                chunk = f"{chunk}{paras[idx + 1]}"
            out.append(chunk[:480])
        return out
    out = list(paras)
    while len(out) < n:
        chunk = raw[len(out) * max(40, len(raw) // n) : (len(out) + 1) * max(40, len(raw) // n)]
        out.append((chunk or paras[-1])[:480])
    return out[:n]


def character_visual_bible(character: Character, *, max_len: int = 900) -> str:
    """可复用的定妆文字圣经:分镜/设定稿/生图共用,越细越稳。"""
    name = (character.name or "角色").strip()
    role_cn = ROLE_CN.get((character.role or "").strip(), character.role or "角色")
    look = (character.visual_prompt or "").strip()
    appearance = (character.appearance or "").strip()
    vibe = (character.personality or "").strip()
    goal = (character.goal or "").strip()
    extra = _char_field(character, "occupation", "organization", "notes")

    # 已有详尽定妆词时优先全文锁定
    if len(look) >= 80:
        body = look
    else:
        parts: list[str] = []
        if look:
            parts.append(look)
        if appearance and appearance not in look:
            parts.append(f"外貌原文:{appearance}")
        if vibe:
            parts.append(f"气质性格:{vibe}")
        if goal:
            parts.append(f"目标动机(影响表情姿态):{goal}")
        if extra:
            parts.append(f"身份补充:{extra}")
        if not parts:
            parts.append(
                "原文外貌不足:请按角色名与定位设计一套可复用的固定外形"
                "(物种/年龄感/头身比/脸型/瞳色/发型发色/标志性器官如耳朵尾巴/"
                "常服配色与材质/鞋履配饰),设计后永久锁定,禁止每次重抽"
            )
        body = "；".join(parts)

    return (
        f"{name}({role_cn})定妆锁定:"
        f"{body}。"
        "必须写死并全程一致:物种或种族、年龄观感、头身比、脸型、瞳色与眼型、"
        "发型发色长度、标志性器官颜色与形态(如耳朵/尾巴)、肤色或皮毛主色、"
        "常服款式层次与主辅色、鞋履与标志配饰。"
        "禁止临时改脸改色改装;与设定稿正面全身一致。"
    )[:max_len]


def build_stable_visual_prompt(character: Character) -> str:
    """生成/刷新写入人物卡的定妆词,供跨章复用。"""
    name = (character.name or "角色").strip()
    role_cn = ROLE_CN.get((character.role or "").strip(), character.role or "角色")
    appearance = (character.appearance or "").strip()
    look = (character.visual_prompt or "").strip()
    vibe = (character.personality or "").strip()
    extra = _char_field(character, "occupation", "organization", "notes", "goal")

    seed = look if len(look) >= len(appearance) else (appearance or look)
    return (
        f"【{name}·永久定妆】定位{role_cn}。"
        f"固定外形:{seed or '按人设一次性设计并永久锁定'}。"
        f"气质:{vibe or '从人设推断并固定'}。"
        f"身份补充:{extra or '无'}。"
        "细化并固定以下字段(未写明的按人设补全后不得再改):"
        "物种/种族;年龄观感;头身比;脸型轮廓;瞳色眼型;发型发色与长度;"
        "标志性器官(耳朵/尾巴等)颜色与形态;肤色或皮毛主色与斑纹;"
        "常服(外衣+内着+鞋履)款式与主辅色;标志配饰;标志动作习惯。"
        "后续所有分镜必须复用本定妆,禁止换脸换装换物种。"
    )[:1500]


def character_look_brief(character: Character, *, max_len: int = 220) -> str:
    """剧情生图用的短外貌锁:只锁形象,不写设定稿清单。"""
    name = (character.name or "角色").strip()
    look = (character.visual_prompt or "").strip()
    appearance = (character.appearance or "").strip()
    if "细化并固定以下字段" in look or "永久定妆" in look:
        m = re.search(r"固定外形[:：]([^。；\n]+)", look)
        core = (m.group(1).strip() if m else "") or appearance or look[:120]
    else:
        core = look or appearance
    if not core:
        core = _char_field(character, "personality", "notes") or "保持稳定可辨识外形"
    # 去掉易诱发多格设定稿的措辞
    core = re.sub(r"(三视图|A-?[Pp]ose|设定稿|四分区|多格|拆分|表情差分)", "", core)
    return f"{name}外形锁定:{core.strip('；;,.。 ')}"[:max_len]


def format_characters_for_plan(characters: list[Character]) -> str:
    """规划分镜时的短外貌提示(仅供一致,不要写进画面结构)。"""
    if not characters:
        return "无"
    lines: list[str] = []
    for c in characters[:8]:
        lines.append(f"- id={c.id} {character_look_brief(c, max_len=180)}")
    return "\n".join(lines)


def strip_design_sheet_noise(text: str) -> str:
    """去掉分镜文案里会诱发「画成设定稿/多人分身」的定妆拼版段落,保留剧情句。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    # 只删【定妆…】段落,直到下一个【小节】
    raw = re.sub(r"【定妆[^】]*】[^【]*", "", raw)
    raw = re.sub(r"(?:^|[。；\n])[^【\n]{0,30}定妆锁定[^【。\n]*。?", "。", raw)
    raw = re.sub(r"必须写死并全程一致[^【。\n]*。?", "", raw)
    raw = re.sub(r"(?:三视图|A-?[Pp]ose|四分区|表情差分墙|服装拆分平铺)", "", raw)
    raw = re.sub(r"[。；]{2,}", "。", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip(" 。；\n")


def enrich_shot_with_look(
    shot_prompt: str,
    characters: list[Character],
    *,
    scene: NovelScene | None = None,
    caption: str = "",
) -> str:
    """补强分镜文案:剧情/镜头/环境优先;定妆不进正文(生图时另锁外貌)。"""
    raw = strip_design_sheet_noise(shot_prompt)
    names = [((c.name or "").strip() or "角色") for c in characters]
    cast = f"本镜出场{len(names)}人:{'、'.join(names)}。" if names else "本镜出场人物按剧情,每人只出现一次。"
    scene_line = ""
    if scene:
        bits = [f"场景「{scene.title}」"]
        if scene.description:
            bits.append(scene.description[:180])
        if scene.atmosphere:
            bits.append(f"氛围:{scene.atmosphere}")
        scene_line = "【场景】" + "；".join(bits)

    # 抽出已有剧情正文
    m = re.search(r"【剧情瞬间】([^【]+)", raw)
    story = (m.group(1).strip() if m else "") or ""
    if not story:
        story = re.sub(r"^本镜出场[^。]*。\s*", "", raw).strip()
        story = re.sub(r"【(?:镜头|环境与道具|光影情绪|画面禁令|场景)】[^【]*", "", story).strip()
    if len(story) < 8:
        story = (caption or "").strip() or "承接章节剧情的关键瞬间"

    # 已是剧情向长文案:去噪后保留结构
    if len(raw) >= 160 and "【剧情" in raw and "【镜头" in raw:
        if cast not in raw:
            raw = cast + "\n" + raw
        return strip_design_sheet_noise(raw)[:1400]

    blocks = [
        cast,
        f"【剧情瞬间】{story}",
        "【镜头】写明景别(远/全/中/近/特写)、机位、主体占位与视线;必须是单幅剧情画面,不要设定稿。",
        "【环境与道具】空间结构、关键道具、前后景。",
        "【光影情绪】主光方向、色温、情绪。",
        "【画面禁令】禁止角色设定稿/三视图/四宫格/多表情拼版;每个角色只画一次;不要画成多个人。",
    ]
    if scene_line:
        blocks.insert(1, scene_line)
    return "\n".join(b for b in blocks if b).strip()[:1400]


def plan_frames_from_text(
    *,
    title: str,
    outline: str | None,
    content: str,
    frame_count: int,
    characters: list[Character],
    scenes: list[NovelScene],
) -> list[dict[str, Any]]:
    """无模型时的分镜规划:从章纲+正文切 N 个节拍,并写成详细分镜。"""
    n = max(1, min(12, frame_count))
    base = (outline or "").strip() or content
    beats = _split_beats(base, n)
    primary = characters[:1]
    scene = scenes[0] if scenes else None
    scene_id = scene.id if scene else None
    frames: list[dict[str, Any]] = []
    for i, beat in enumerate(beats):
        caption = f"镜头 {i + 1}"
        first = re.split(r"[。！？\n]", beat)[0].strip()
        if 4 <= len(first) <= 36:
            caption = first
        # 按节拍点名绑定人物,否则用主角
        resolved = resolve_characters_for_shot(
            all_characters=characters,
            character_ids=[],
            text=beat,
            fallback_ids=[c.id for c in primary],
        ) or primary
        prefix = "开场" if i == 0 else ("章末收束" if i == n - 1 else f"节拍{i + 1}")
        shot = enrich_shot_with_look(
            f"《{title}》{prefix}:{beat}",
            list(resolved),
            scene=scene,
            caption=caption,
        )
        frames.append(
            {
                "sort": i,
                "caption": caption,
                "shot_prompt": shot,
                "character_ids": [c.id for c in resolved],
                "scene_id": scene_id,
            }
        )
    return frames


# 未填写全书画风时的兜底(一旦用户保存了画风,则不得再用这套默认美学抢戏)
_DEFAULT_STYLE = "中国动画风格,二维赛璐璐上色,清晰轮廓线,影视分镜感"


def novel_style(novel: Novel) -> str:
    return (getattr(novel, "visual_style_prompt", None) or "").strip()


def style_lock_block(novel: Novel, *, for_sheet: bool = False) -> str:
    """全书画风硬锁:置顶注入,压制模型默认写实/3D/欧美漫风。"""
    style = novel_style(novel) or _DEFAULT_STYLE
    layout = (
        "版式仅限「四分区角色设定稿」信息图排版;渲染介质与上色方式必须服从上述画风,不得擅自改成宣纸线稿、照片或写实油画。"
        if for_sheet
        else "构图可为电影分镜,但渲染介质、线条、上色、光影气质必须服从上述画风。"
    )
    return (
        "【最高优先级·全书画风·强制锁定】严格按设定库已保存的全书画风绘制,一字不差地遵守:"
        f"「{style}」。"
        "禁止漂移到照片写实、真人摄影、超写实3D、欧美美漫、日系厚涂(除非画风原文明确要求);"
        f"禁止用与全书画风冲突的默认滤镜或材质。{layout}"
    )


def _char_field(character: Character, *names: str) -> str:
    bits: list[str] = []
    for n in names:
        t = (getattr(character, n, None) or "").strip()
        if t:
            bits.append(t)
    return "；".join(bits)


def build_character_portrait_prompt(novel: Novel, character: Character) -> str:
    """角色设定稿(概念设计图) prompt:四分区、A-pose、中文手写标注。

    强制输出可复用的细粒度定妆特征,避免每次生成漂成另一个角色。
    """
    role_cn = ROLE_CN.get((character.role or "").strip(), character.role or "角色")
    bible = character_visual_bible(character, max_len=1100)
    vibe = _char_field(character, "personality", "goal")
    extra = _char_field(character, "occupation", "organization", "notes")
    style = novel_style(novel) or _DEFAULT_STYLE
    stable = build_stable_visual_prompt(character)

    return f"""{style_lock_block(novel, for_sheet=True)}

一张横向宽构图的「角色设定稿」概念设计图,信息图排版清晰,标题手写体中文:角色设定稿 · {character.name}
画风锁定(整图所有分区同一画风,禁止混用其他画风):{style}

【永久定妆圣经·必须画进并保持一致】
{bible}
补充气质:{vibe[:360] or '从人设推断并固定'}。身份:{extra[:360] or '无'}。
文字定妆卡(请在画面标注区体现关键关键词):{stable[:700]}

Step1 人体特征(一次性设计后永久锁定,禁止模糊、禁止留白给下次重抽):
角色「{character.name}」,定位{role_cn}。
必须明确画出并在图中一致:物种/种族、年龄观感、头身比、脸型、瞳色眼型、发型发色长度、
标志性器官(耳朵/尾巴等)的准确颜色与形态、肤色或皮毛主色与斑纹。
若原文只写「粉耳朵/白耳朵」等关键词,颜色与形状必须画死,全图所有小图不得改色。

Step2 着装(按人物描述与身份设计一套常服,禁止泳装/比基尼/现代泳镜):
完整内外搭:外衣层次、内着结构、鞋履、头饰与随身物;主色+辅色写死;材质服从全书画风。

Step3 画面分为四个区域,手写体中文小字标注(全部中文):
左侧「1.三视图 A-Pose」:同一套服装正面/侧面/背面全身,双臂微张自然站立,三视图同一张脸同一配色。
中上「2.身体结构拆分」:头颈肩、躯干、四肢比例,标出头身比与标志器官位置。
中下「3.内着与服装拆分」:外衣/内着/配件平铺,标主辅色。
右侧「4.细节特写」:至少5个表情差分 + 眼部/发型/耳朵或标志器官/配饰/手部特写,颜色与主视图一致。

Step4 生成要求:同一张脸、同一套配色贯穿全图;禁止英文水印、现代UI、随机改设;
全部小图必须与全书画风「{style}」一致,作为后续分镜唯一角色本体参考。""".strip()[:3200]


def character_lock_line(character: Character) -> str:
    """剧情生图用的外貌短锁:定妆/设定稿只作参考,不复制其构图。"""
    bits: list[str] = [
        f"【外貌参考·{character.name}】{character_look_brief(character, max_len=240)}",
        "只统一脸与服装配色,按剧情姿势动作来画,不要画设定稿姿势",
    ]
    if character.reference_image_urls:
        bits.append(
            "若提供设定稿参考图:只借鉴其中一张脸与常服颜色;"
            "禁止把设定稿多格/三视图/表情差分画进剧情图;"
            "禁止因参考图而多画同一个人"
        )
    return "。".join(bits)


def resolve_characters_for_shot(
    *,
    all_characters: list[Character],
    character_ids: list[int] | None,
    text: str,
    fallback_ids: list[int] | None = None,
) -> list[Character]:
    """分镜出场人物:显式 id → 正文点名 → 章节出场表。"""
    by_id = {c.id: c for c in all_characters}
    ordered: list[Character] = []
    seen: set[int] = set()

    def _add(cid: int) -> None:
        c = by_id.get(cid)
        if c and c.id not in seen:
            seen.add(c.id)
            ordered.append(c)

    for cid in character_ids or []:
        try:
            _add(int(cid))
        except (TypeError, ValueError):
            continue

    blob = text or ""
    # 长名优先,避免短名误伤
    for c in sorted(all_characters, key=lambda x: len(x.name or ""), reverse=True):
        name = (c.name or "").strip()
        if len(name) >= 2 and name in blob:
            _add(c.id)
        aliases = (c.aliases or "").replace("，", ",").replace("、", ",")
        for al in [a.strip() for a in aliases.split(",") if a.strip()]:
            if len(al) >= 2 and al in blob:
                _add(c.id)

    if not ordered:
        for cid in fallback_ids or []:
            try:
                _add(int(cid))
            except (TypeError, ValueError):
                continue

    return ordered[:6]


def collect_character_ref_urls(characters: list[Character], *, limit: int = 3) -> list[str]:
    """收集定妆/设定稿图路径,供生图接口做参考图。

    每个角色只取 1 张设定稿(通常为首张四分区图),避免多图诱发「同人双影」。
    单人镜头默认只传 1 张参考。
    """
    out: list[str] = []
    # 单人镜头更易被多参考图「克隆」,收紧到 1
    if len(characters) <= 1:
        limit = min(limit, 1)
    for c in characters:
        for u in c.reference_image_urls or []:
            u = (u or "").strip()
            if u and u not in out:
                out.append(u)
                break  # 每角色仅 1 张
        if len(out) >= limit:
            return out
    return out


def ensure_exact_frame_count(
    planned: list[dict[str, Any]],
    frame_count: int,
    *,
    title: str,
    outline: str | None,
    content: str,
    characters: list[Character],
    scenes: list[NovelScene],
) -> list[dict[str, Any]]:
    """保证规划结果张数与用户选择一致(截断或规则补齐)。"""
    n = max(1, min(12, frame_count))
    if len(planned) >= n:
        out = planned[:n]
    else:
        filler = plan_frames_from_text(
            title=title,
            outline=outline,
            content=content,
            frame_count=n,
            characters=characters,
            scenes=scenes,
        )
        out = list(planned)
        for item in filler[len(planned) :]:
            out.append(item)
            if len(out) >= n:
                break
    for i, item in enumerate(out):
        item["sort"] = i
    return out[:n]


def build_consistency_block(
    novel: Novel,
    characters: list[Character],
    scene: NovelScene | None,
) -> str:
    parts: list[str] = [style_lock_block(novel)]
    for c in characters:
        parts.append(character_lock_line(c))
    if scene:
        sc = f"场景「{scene.title}」"
        if scene.description:
            sc += f":{scene.description}"
        if scene.atmosphere:
            sc += f";氛围:{scene.atmosphere}"
        refs = scene.reference_image_urls or []
        if refs:
            sc += f";场景氛围参考设定(勿改变角色脸)"
        parts.append(sc)
    return "。".join(parts)


def build_shot_image_prompt(
    *,
    novel: Novel,
    caption: str,
    shot_prompt: str,
    characters: list[Character],
    scene: NovelScene | None,
    skill_hint: str | None = None,
) -> str:
    """分镜生图:剧情画面优先;定妆/设定稿只锁外貌,绝不画成设定稿。"""
    uniq: list[Character] = []
    seen: set[int] = set()
    for c in characters:
        cid = getattr(c, "id", None)
        if cid is not None and cid in seen:
            continue
        if cid is not None:
            seen.add(cid)
        uniq.append(c)
    characters = uniq

    locks = [character_lock_line(c) for c in characters]
    style = novel_style(novel) or _DEFAULT_STYLE
    names = [((c.name or "").strip() or "角色") for c in characters]
    cast_n = len(names)
    plot = strip_design_sheet_noise(shot_prompt)
    if cast_n == 0:
        cast_rule = "【人数】人物尽量少,每人只出现一次,禁止双胞胎分身。"
    elif cast_n == 1:
        cast_rule = (
            f"【人数硬锁】画面里有且仅有 1 个「{names[0]}」,只画一次;"
            "禁止左右各画一个、禁止三视图并排、禁止表情差分墙。"
        )
    else:
        cast_rule = (
            f"【人数硬锁】出场共 {cast_n} 人:{'、'.join(names)};"
            "每人只出现一次,禁止同人双影/设定稿分流。"
        )

    scene_bits: list[str] = []
    if scene:
        scene_bits.append(f"场景「{scene.title}」")
        if scene.description:
            scene_bits.append(scene.description[:200])
        if scene.atmosphere:
            scene_bits.append(f"氛围:{scene.atmosphere}")

    blocks = [
        "【最高优先级·剧情单幅插画】这是章节故事瞬间,不是角色设定稿。"
        "必须画成一个连贯场景里的动作/情绪/环境;禁止米白底设定表、禁止四宫格、"
        "禁止正面侧面背面并排、禁止多表情头像墙、禁止服装拆分平铺。",
        style_lock_block(novel),
        cast_rule,
        f"分镜标题:{(caption or '章节分镜').strip()}",
        f"【剧情画面·主要描绘】{plot[:1200] or (caption or '章节关键瞬间')}",
    ]
    if scene_bits:
        blocks.append("场景补充:" + "；".join(scene_bits))
    hint = (skill_hint or "").strip()
    if hint:
        blocks.append(
            "【技能指导】在服从剧情画面的前提下参考:\n" + hint[:1000]
        )
    if locks:
        blocks.append(
            "【外貌一致性·次要】下列仅用于脸与服装不换人,不要按设定稿构图去画:\n"
            + "\n".join(locks)
        )
    blocks.append(
        f"最终画面:单镜头剧情插画,清晰主体与戏剧光影,画风「{style}」;"
        "角色外形与短外貌锁/参考图一致即可;"
        "绝对不要输出设定稿拼版;不要大段文字水印。"
    )
    return "\n".join(blocks)[:4000]


def build_video_prompt_from_frames(
    novel: Novel,
    chapter: Chapter,
    frames: list[Any],
    override: str | None = None,
) -> str:
    if (override or "").strip():
        return override.strip()[:800]
    caps = []
    for f in frames[:8]:
        cap = getattr(f, "caption", None) or ""
        prompt = getattr(f, "shot_prompt", None) or ""
        caps.append(f"{cap}:{prompt[:80]}" if cap else prompt[:100])
    style = novel_style(novel) or _DEFAULT_STYLE
    body = " → ".join(c for c in caps if c)
    parts = [
        f"全书画风强制锁定:「{style}」,整段视频不得偏离该画风",
        f"根据小说《{novel.title}》章节「{chapter.title}」做短视频分镜动画",
        body,
        "镜头连贯,角色外貌一致",
        "禁止改成照片写实或无关三维渲染(除非画风原文要求)",
    ]
    return "。".join(parts)[:800]


def parse_llm_frames(text: str, frame_count: int) -> list[dict[str, Any]] | None:
    """尝试从模型 JSON 输出解析分镜。"""
    raw = (text or "").strip()
    if not raw:
        return None
    # 抠 JSON 数组
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    out: list[dict[str, Any]] = []
    for i, item in enumerate(data[:frame_count]):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "sort": i,
                "caption": str(item.get("caption") or item.get("title") or f"镜头 {i + 1}")[:255],
                "shot_prompt": str(item.get("shot_prompt") or item.get("prompt") or item.get("description") or "")[
                    :1600
                ],
                "character_ids": item.get("character_ids") or [],
                "scene_id": item.get("scene_id"),
            }
        )
    return out or None
