"""漫剧视频分镜剧本:小说/剧本 → 分集时间轴镜头(秒数/景别/运镜/对白)。"""

from __future__ import annotations

import json
import re
from typing import Any


STORY_CLIP = 12000
SHOT_DURATION = 5  # 默认每镜时长(秒)

SHOT_SIZE_LABELS = {
    "ecu": "大特写",
    "cu": "特写",
    "ms": "中景",
    "fs": "全景",
    "ls": "远景",
    "els": "大远景",
}
CAMERA_MOVE_LABELS = {
    "static": "固定",
    "push": "推进",
    "pull": "拉远",
    "pan": "横摇",
    "tilt": "俯仰",
    "follow": "跟随",
    "orbit": "环绕",
}
ANGLE_LABELS = {
    "eye": "平视",
    "high": "俯拍",
    "low": "仰拍",
    "over": "过肩",
}

DRAMA_SCRIPT_SYSTEM = (
    "你是短视频/漫剧分镜导演兼编剧。"
    "输出「可直接拍视频」的分镜剧本:每镜必须写清起止秒数、景别、运镜、机位、画面动作、说话人与对白。"
    "只输出 JSON,不要解释,不要 Markdown 围栏。"
    "每个镜头是时间轴上的一段视频,不是角色设定稿或三视图。"
)


def clip_story(text: str, limit: int = STORY_CLIP) -> tuple[str, bool]:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw, False
    return raw[:limit], True


def script_user_prompt(
    story: str,
    *,
    title: str,
    episode_count: int,
    shots_per: int,
    style: str,
    cast_hint: str,
) -> str:
    n, k = episode_count, shots_per
    dur = SHOT_DURATION
    ep_len = k * dur
    style_line = f"画风约束:{style}" if style else "画风按故事气质自拟并写入 palette.note"
    return f"""把下面的故事做成「视频分镜剧本」《{title or '未命名'}》。
必须正好 {n} 集;每集正好 {k} 个镜头;每集总时长约 {ep_len} 秒。
每镜默认 {dur} 秒,时间轴连续:第1镜 0-{dur}s,第2镜 {dur}-{dur * 2}s,以此类推,不要重叠、不要留空档。
{style_line}
人物外形参考(整片强制锁脸锁服,assets 里每个角色 prompt 写清五官/发型/服装主色/标志物,后续首帧依赖此定妆):
{cast_hint or '从故事推断并固定;每个角色写一句不可改的外形'}

这是拍摄用分镜表,不是小说摘要。每一镜必须包含:
- startSec/endSec:整数秒
- shotSize: cu|ms|fs|ls|ecu|els (特写/中景/全景/远景…)
- cameraMove: static|push|pull|pan|tilt|follow|orbit
- angle: eye|high|low|over
- visual:画面里谁在做什么、环境与动作(写细,可拍)
- speaker:说话人姓名;无人说话写「旁白」或空串
- line:对白或旁白原文;无声可写空串或动作音效提示
- imagePrompt:该镜首帧文生图提示(单幅剧情,不要风格词、不要三视图)

输出 JSON:
{{"title":"剧名","palette":{{"note":"色调一句话","colors":[{{"name":"色名","hex":"#RRGGBB","role":"主/辅/点缀"}}]}},"assets":[{{"id":"C01","kind":"character|scene|prop","name":"名","prompt":"造型一句话"}}],"episodes":[{{"index":1,"title":"集标题","synopsis":"本集一事","durationSec":{ep_len},"narrator":"本集旁白总述(可空)","lastFrame":"本集最后一格静止画面","shots":[{{"startSec":0,"endSec":{dur},"shotSize":"ms","cameraMove":"static","angle":"eye","visual":"画面动作","speaker":"角色名或旁白","line":"对白","imagePrompt":"首帧画面描述"}}]}}]}}

严禁:角色设定稿、四宫格、省略秒数/景别/对白字段、同一人画两次。

故事:
{story.strip()}"""


def parse_drama_bible(
    text: str,
    fallback_story: str,
    *,
    episode_count: int = 5,
    shots_per: int = 3,
    title: str = "",
) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    blob = fenced.group(1) if fenced else raw
    start, end = blob.find("{"), blob.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(blob[start : end + 1])
            if isinstance(data, dict):
                return normalize_bible(data, fallback_story, episode_count, shots_per, title)
        except json.JSONDecodeError:
            pass
    return fallback_bible(fallback_story, episode_count, shots_per, title)


def _str(v: Any, cap: int = 800) -> str:
    return str(v or "").strip()[:cap]


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _norm_shot_size(raw: Any, visual: str) -> str:
    s = _str(raw, 24).lower()
    for key, label in SHOT_SIZE_LABELS.items():
        if s == key or label in s or key in s:
            return key
    if re.search(r"大特写|瞳孔|嘴唇", visual):
        return "ecu"
    if re.search(r"特写|面部|脸部|手部", visual):
        return "cu"
    if re.search(r"大远景|天际|全城", visual):
        return "els"
    if re.search(r"远景|空镜", visual):
        return "ls"
    if re.search(r"全景|全身", visual):
        return "fs"
    return "ms"


def _norm_camera_move(raw: Any, visual: str) -> str:
    s = _str(raw, 24).lower()
    for key, label in CAMERA_MOVE_LABELS.items():
        if s == key or label in s or key in s:
            return key
    if re.search(r"推进|推近|走近", visual):
        return "push"
    if re.search(r"拉远|拉开", visual):
        return "pull"
    if re.search(r"横摇|摇过", visual):
        return "pan"
    if re.search(r"跟随|追上|追着", visual):
        return "follow"
    if re.search(r"环绕", visual):
        return "orbit"
    return "static"


def _norm_angle(raw: Any, visual: str) -> str:
    s = _str(raw, 24).lower()
    for key, label in ANGLE_LABELS.items():
        if s == key or label in s or key in s:
            return key
    if re.search(r"俯拍|俯视", visual):
        return "high"
    if re.search(r"仰拍|仰视", visual):
        return "low"
    if re.search(r"过肩", visual):
        return "over"
    return "eye"


def normalize_bible(
    r: dict[str, Any],
    fallback_story: str,
    episode_count: int,
    shots_per: int,
    title: str,
) -> dict[str, Any]:
    pal = r.get("palette") if isinstance(r.get("palette"), dict) else {}
    colors = pal.get("colors") if isinstance(pal.get("colors"), list) else []
    assets_in = r.get("assets") if isinstance(r.get("assets"), list) else []
    assets = []
    for i, a in enumerate(assets_in[:24]):
        if not isinstance(a, dict):
            continue
        name = _str(a.get("name"), 40)
        if not name:
            continue
        assets.append(
            {
                "id": _str(a.get("id"), 16) or f"A{i + 1:02d}",
                "kind": _str(a.get("kind"), 16) or "character",
                "name": name,
                "prompt": _str(a.get("prompt"), 400),
            }
        )
    eps_in = r.get("episodes") if isinstance(r.get("episodes"), list) else []
    episodes: list[dict[str, Any]] = []
    for i in range(episode_count):
        src = eps_in[i] if i < len(eps_in) and isinstance(eps_in[i], dict) else {}
        shots_src = src.get("shots") if isinstance(src.get("shots"), list) else []
        shots = []
        cursor = 0
        for j in range(shots_per):
            sh = shots_src[j] if j < len(shots_src) and isinstance(shots_src[j], dict) else {}
            visual = _str(sh.get("visual") or sh.get("imagePrompt"), 500) or f"第{i + 1}集第{j + 1}镜剧情瞬间"
            start = _int(sh.get("startSec"), cursor)
            end = _int(sh.get("endSec"), start + SHOT_DURATION)
            if end <= start:
                end = start + SHOT_DURATION
            # 强制时间轴连续
            if j == 0:
                start = 0
            else:
                start = shots[j - 1]["endSec"]
            if end <= start:
                end = start + SHOT_DURATION
            cursor = end
            speaker = _str(sh.get("speaker") or sh.get("role"), 40)
            line = _str(sh.get("line") or sh.get("dialogue"), 280)
            shots.append(
                {
                    "startSec": start,
                    "endSec": end,
                    "visual": visual,
                    "speaker": speaker,
                    "line": line,
                    "imagePrompt": _str(sh.get("imagePrompt") or visual, 600),
                    "shotSize": _norm_shot_size(sh.get("shotSize"), visual),
                    "cameraMove": _norm_camera_move(sh.get("cameraMove"), visual),
                    "angle": _norm_angle(sh.get("angle"), visual),
                }
            )
        episodes.append(
            {
                "index": i + 1,
                "title": _str(src.get("title"), 40) or f"第{i + 1}集",
                "synopsis": _str(src.get("synopsis"), 300),
                "durationSec": shots[-1]["endSec"] if shots else shots_per * SHOT_DURATION,
                "narrator": _str(src.get("narrator"), 300),
                "lastFrame": _str(src.get("lastFrame"), 300),
                "shots": shots,
            }
        )
    return {
        "title": _str(r.get("title"), 80) or title or "未命名漫剧",
        "palette": {
            "note": _str(pal.get("note"), 120),
            "colors": [
                {
                    "name": _str(c.get("name"), 20),
                    "hex": _str(c.get("hex"), 8) or "#888888",
                    "role": _str(c.get("role"), 12),
                }
                for c in colors[:8]
                if isinstance(c, dict)
            ],
        },
        "assets": assets,
        "episodes": episodes,
    }


def fallback_bible(story: str, episode_count: int, shots_per: int, title: str) -> dict[str, Any]:
    raw = (story or "").strip() or "一段待拍的故事。"
    paras = [p.strip() for p in re.split(r"\n\s*\n+|(?<=[。！？])", raw) if p.strip()]
    if not paras:
        paras = [raw]
    n = max(1, episode_count) * max(1, shots_per)
    if len(paras) < n:
        paras = (paras * ((n // len(paras)) + 1))[:n]
    else:
        step = len(paras) / n
        paras = [paras[min(len(paras) - 1, int(i * step))] for i in range(n)]
    episodes = []
    idx = 0
    for i in range(episode_count):
        shots = []
        for j in range(shots_per):
            beat = paras[idx][:400]
            idx += 1
            start, end = j * SHOT_DURATION, (j + 1) * SHOT_DURATION
            shots.append(
                {
                    "startSec": start,
                    "endSec": end,
                    "visual": beat,
                    "speaker": "旁白",
                    "line": beat[:40],
                    "imagePrompt": f"单幅剧情插画:{beat}",
                    "shotSize": _norm_shot_size(None, beat),
                    "cameraMove": _norm_camera_move(None, beat),
                    "angle": _norm_angle(None, beat),
                }
            )
        episodes.append(
            {
                "index": i + 1,
                "title": f"第{i + 1}集",
                "synopsis": shots[0]["visual"][:80],
                "durationSec": shots[-1]["endSec"],
                "narrator": "",
                "lastFrame": shots[-1]["visual"][:120],
                "shots": shots,
            }
        )
    return {
        "title": title or "未命名漫剧",
        "palette": {"note": "按故事气质", "colors": []},
        "assets": [],
        "episodes": episodes,
    }


def flatten_shots(bible: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sort = 0
    for ep in bible.get("episodes") or []:
        ei = int(ep.get("index") or 1)
        etitle = _str(ep.get("title"), 40)
        for j, sh in enumerate(ep.get("shots") or []):
            sort += 1
            visual = _str(sh.get("visual"), 500)
            start = _int(sh.get("startSec"), j * SHOT_DURATION)
            end = _int(sh.get("endSec"), start + SHOT_DURATION)
            out.append(
                {
                    "episode_index": ei,
                    "shot_index": j + 1,
                    "sort": sort - 1,
                    "caption": f"E{ei} {start}-{end}s · {etitle}",
                    "start_sec": start,
                    "end_sec": end,
                    "shot_size": _norm_shot_size(sh.get("shotSize"), visual),
                    "camera_move": _norm_camera_move(sh.get("cameraMove"), visual),
                    "angle": _norm_angle(sh.get("angle"), visual),
                    "speaker": _str(sh.get("speaker"), 40),
                    "visual": visual,
                    "line": _str(sh.get("line"), 280),
                    "image_prompt": _str(sh.get("imagePrompt") or visual, 600),
                }
            )
    return out


def shot_language_cn(shot: dict[str, Any]) -> str:
    size = SHOT_SIZE_LABELS.get(_str(shot.get("shotSize") or shot.get("shot_size"), 8), "中景")
    move = CAMERA_MOVE_LABELS.get(_str(shot.get("cameraMove") or shot.get("camera_move"), 12), "固定")
    ang = ANGLE_LABELS.get(_str(shot.get("angle"), 8), "平视")
    return f"{size} · {move} · {ang}"


VARIANT_HINTS = (
    "变体A:构图略偏环境交代,主体略靠中后景——仍保持同一画风与同一角色外形",
    "变体B:主体更近、戏剧光更强——仍保持同一画风与同一角色外形",
    "变体C:机位或姿态微调——仍保持同一画风与同一角色外形",
)

DEFAULT_DRAMA_STYLE = (
    "中国二维动画风格,赛璐璐上色,清晰墨线轮廓,统一角色比例与五官设计,"
    "禁止Q版/chibi、禁止写实摄影、禁止3D渲染、禁止欧美卡通"
)


def _shot_cast_names(shot: dict[str, Any], assets: list[dict[str, Any]]) -> list[str]:
    """只取本镜可能出场的角色名,避免把全员塞进画面。"""
    blob = " ".join(
        [
            str(shot.get("visual") or ""),
            str(shot.get("image_prompt") or ""),
            str(shot.get("caption") or ""),
            str(shot.get("speaker") or ""),
            str(shot.get("line") or ""),
        ]
    )
    names: list[str] = []
    for a in assets:
        if (a.get("kind") or "character") != "character":
            continue
        name = str(a.get("name") or "").strip()
        if not name:
            continue
        if name in blob or (shot.get("speaker") or "").strip() == name:
            names.append(name)
    if not names:
        # 画面未点名时取主角资产前 2 个,仍限制人数
        for a in assets:
            if (a.get("kind") or "character") != "character":
                continue
            name = str(a.get("name") or "").strip()
            if name:
                names.append(name)
            if len(names) >= 2:
                break
    # 保序去重
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:4]


def first_frame_prompt(
    *,
    style: str,
    shot: dict[str, Any],
    assets: list[dict[str, Any]],
    episode_title: str,
    refine_note: str = "",
    variant_index: int = 0,
) -> str:
    style = (style or "").strip() or DEFAULT_DRAMA_STYLE
    cast_names = _shot_cast_names(shot, assets)
    look_map = {
        str(a.get("name") or "").strip(): str(a.get("prompt") or "").strip()[:220]
        for a in assets
        if (a.get("kind") or "character") == "character" and a.get("name")
    }
    looks = [f"{n}:{look_map[n]}" for n in cast_names if look_map.get(n)]
    visual = (shot.get("visual") or shot.get("image_prompt") or "").strip()
    line = (shot.get("line") or "").strip()
    speaker = (shot.get("speaker") or "").strip()
    note = (refine_note or shot.get("refine_note") or "").strip()
    lang = shot_language_cn(shot)
    t0 = shot.get("start_sec", shot.get("startSec", 0))
    t1 = shot.get("end_sec", shot.get("endSec", SHOT_DURATION))
    vi = max(0, min(int(variant_index or 0), len(VARIANT_HINTS) - 1))
    cast_line = "、".join(cast_names) if cast_names else "按剧情最少必要角色"
    return "\n".join(
        [
            "【最高优先级·视频分镜首帧】这是漫剧时间轴上的一镜首帧,不是角色设定稿。"
            "必须画成可接视频的剧情瞬间;禁止四宫格、三视图、表情墙、角色设定拼贴。",
            f"【全书画风·强制锁定】「{style}」。"
            "同一项目所有分镜、所有候选图必须同一画风;"
            "禁止在候选之间切换为Q版/chibi/3D/写实/油画等其它风格。",
            "【角色一致性·强制锁定】同一角色跨分镜必须同脸、同发型、同年纪感、同服装主色与标志配饰;"
            "禁止换脸、禁止随机路人顶替具名角色。",
            f"【本镜出场】仅画:{cast_line}。每人只出现一次;禁止塞入未点名的多余角色/神仙/路人团。",
            ("【定妆外貌·最高优先于姿势】按下列描述锁脸与服装,姿势服从剧情:\n" + "\n".join(looks))
            if looks
            else "【定妆外貌】从剧情推断后永久固定,后续镜不得改脸改服。",
            f"分镜:{episode_title} / {t0}-{t1}秒 / {lang} / {shot.get('caption') or ''}",
            f"【镜头】{lang}",
            f"【剧情画面】{visual}",
            f"【对白】{speaker}:{line}" if line else "",
            f"【用户改图要求·优先遵守】{note}" if note else "",
            f"【候选差异·仅构图/光影/姿态,不改画风与人设】{VARIANT_HINTS[vi]}",
            "构图:单镜头剧情插画,清晰主体,戏剧光影;不要大段文字水印。",
        ]
    ).strip()[:3600]
