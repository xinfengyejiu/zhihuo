"""小说视觉:场景库 + 章节分镜生图/生视频(复用 media_gen)。"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..ai.gateway import resolve_any_purpose, stream_chat
from ..ai.media_gen import (
    find_modality_model,
    generate_image,
    generate_video,
    resolve_modality_route,
    save_bytes,
)
from ..ai.novel_visual import (
    build_character_portrait_prompt,
    build_shot_image_prompt,
    build_stable_visual_prompt,
    build_video_prompt_from_frames,
    collect_character_ref_urls,
    enrich_shot_with_look,
    ensure_exact_frame_count,
    format_characters_for_plan,
    parse_llm_frames,
    plan_frames_from_text,
    resolve_characters_for_shot,
)
from ..ai.skill_pack import build_skill_context
from ..database import SessionLocal, get_db
from ..models import (
    Chapter,
    ChapterStoryboard,
    ChapterStoryboardFrame,
    Character,
    Novel,
    NovelScene,
    Skill,
    User,
    now,
)
from ..routers.novels import get_owned_novel
from ..schemas import (
    CharacterOut,
    NovelSceneIn,
    NovelSceneOut,
    StoryboardGenerateIn,
    StoryboardOut,
    StoryboardPlanIn,
    StoryboardFrameOut,
    StoryboardVideoIn,
)
from ..security import get_current_user

router = APIRouter(prefix="/novels/{novel_id}", tags=["visual"])


def _chapter(db: Session, novel_id: int, chapter_id: int) -> Chapter:
    ch = db.query(Chapter).filter_by(id=chapter_id, novel_id=novel_id).first()
    if ch is None:
        raise HTTPException(404, "章节不存在")
    return ch


def _chapter_text(chapter: Chapter) -> str:
    for v in chapter.versions:
        if v.id == chapter.current_version_id:
            return v.content or ""
    return ""


def _frame_out(f: ChapterStoryboardFrame) -> StoryboardFrameOut:
    return StoryboardFrameOut(
        id=f.id,
        sort=f.sort,
        caption=f.caption,
        shot_prompt=f.shot_prompt or "",
        image_url=f.image_url,
        character_ids=f.character_ids or [],
        scene_id=f.scene_id,
        status=f.status,
        error=f.error,
    )


def _board_out(board: ChapterStoryboard, frames: list[ChapterStoryboardFrame]) -> StoryboardOut:
    return StoryboardOut(
        id=board.id,
        chapter_id=board.chapter_id,
        status=board.status,
        frame_count=board.frame_count,
        style_snapshot=board.style_snapshot,
        video_url=board.video_url,
        video_prompt=board.video_prompt,
        error=board.error,
        frames=[_frame_out(f) for f in sorted(frames, key=lambda x: x.sort)],
        updated_at=board.updated_at,
    )


def _get_or_create_board(db: Session, novel_id: int, chapter_id: int) -> ChapterStoryboard:
    board = db.query(ChapterStoryboard).filter_by(chapter_id=chapter_id).first()
    if board:
        return board
    board = ChapterStoryboard(novel_id=novel_id, chapter_id=chapter_id, status="draft", frame_count=4)
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


# ---------- 场景库 ----------
@router.get("/scenes", response_model=list[NovelSceneOut])
def list_scenes(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(NovelScene).filter_by(novel_id=novel_id).order_by(NovelScene.sort, NovelScene.id).all()


@router.post("/scenes", response_model=NovelSceneOut)
def create_scene(
    novel_id: int,
    body: NovelSceneIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    row = NovelScene(
        novel_id=novel_id,
        title=body.title.strip(),
        description=body.description,
        atmosphere=body.atmosphere,
        reference_image_urls=body.reference_image_urls or [],
        sort=body.sort,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/scenes/{scene_id}", response_model=NovelSceneOut)
def update_scene(
    novel_id: int,
    scene_id: int,
    body: NovelSceneIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    row = db.query(NovelScene).filter_by(id=scene_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "场景不存在")
    row.title = body.title.strip()
    row.description = body.description
    row.atmosphere = body.atmosphere
    row.reference_image_urls = body.reference_image_urls or []
    row.sort = body.sort
    row.updated_at = now()
    db.commit()
    db.refresh(row)
    return row


@router.delete("/scenes/{scene_id}")
def delete_scene(
    novel_id: int, scene_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    row = db.query(NovelScene).filter_by(id=scene_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "场景不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/characters/{character_id}/generate-portrait", response_model=CharacterOut)
async def generate_character_portrait(
    novel_id: int,
    character_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """根据人物卡生成四分区角色设定稿,写入 reference_image_urls(插到最前)。后续章节分镜沿用此图。"""
    novel = get_owned_novel(novel_id, user, db)
    row = db.query(Character).filter_by(id=character_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "人物不存在")
    desc = " ".join(
        x
        for x in (
            row.visual_prompt,
            row.appearance,
            row.personality,
            row.notes,
            row.occupation,
        )
        if (x or "").strip()
    ).strip()
    if len(desc) < 4 and not (novel.visual_style_prompt or "").strip():
        raise HTTPException(
            400,
            "人物描述太少。请先在「人物」里填写外貌/性格/备注,或在「人物场景视觉」填写全书画风后再生成。",
        )

    prompt = build_character_portrait_prompt(novel, row)
    found = find_modality_model(db, user.id, "image")
    if found:
        target = resolve_modality_route(
            db, user.id, "novel.visual", "image", provider_id=found[0], model_id=found[1]
        )
        urls = await generate_image(target, prompt, user.id, size="1792x1024")
        url = urls[0] if urls else None
    else:
        from ..ai.local_image import render_local_card

        url = render_local_card(user.id, f"{row.name}定妆", context=prompt[:220])

    if not url:
        raise HTTPException(502, "定妆图生成失败,未返回地址")

    refs = list(row.reference_image_urls or [])
    refs = [url] + [u for u in refs if u != url]
    row.reference_image_urls = refs[:8]
    # 每次定妆都刷新详尽定妆词,保证后续分镜锁定同一套外形
    stable = build_stable_visual_prompt(row)
    prev = (row.visual_prompt or "").strip()
    if len(prev) >= 200:
        # 保留原有细项,并补上永久定妆约束头
        row.visual_prompt = (prev if "永久定妆" in prev else f"{stable}\n原文定妆补充:{prev}")[:1500]
    else:
        row.visual_prompt = stable
    row.updated_at = now()
    db.commit()
    db.refresh(row)
    return CharacterOut.model_validate(row)


@router.post("/visual-upload")
async def upload_visual(
    novel_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """定妆/场景参考图上传 → /gen-assets/..."""
    get_owned_novel(novel_id, user, db)
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "图片超过 8MB")
    name = (file.filename or "ref.png").lower()
    ext = "png"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        ext = "jpg"
    elif name.endswith(".webp"):
        ext = "webp"
    elif name.endswith(".gif"):
        ext = "gif"
    url = save_bytes(user.id, data, ext)
    return {"url": url}


# ---------- 分镜 ----------
@router.get("/chapters/{chapter_id}/storyboard", response_model=StoryboardOut)
def get_storyboard(
    novel_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    _chapter(db, novel_id, chapter_id)
    board = _get_or_create_board(db, novel_id, chapter_id)
    frames = db.query(ChapterStoryboardFrame).filter_by(storyboard_id=board.id).all()
    return _board_out(board, frames)


@router.post("/chapters/{chapter_id}/storyboard/plan", response_model=StoryboardOut)
async def plan_storyboard(
    novel_id: int,
    chapter_id: int,
    body: StoryboardPlanIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按章纲/正文规划 N 个分镜(优先 LLM,失败则规则切分)。"""
    novel = get_owned_novel(novel_id, user, db)
    chapter = _chapter(db, novel_id, chapter_id)
    content = _chapter_text(chapter)
    involved_ids = chapter.characters_involved or []
    q = db.query(Character).filter_by(novel_id=novel_id)
    if involved_ids:
        chars = q.filter(Character.id.in_(involved_ids)).all() or q.limit(6).all()
    else:
        chars = q.limit(6).all()
    scenes = db.query(NovelScene).filter_by(novel_id=novel_id).order_by(NovelScene.sort).limit(20).all()

    planned: list[dict] | None = None
    # 尝试 LLM;未配置模型时静默回退规则切分
    try:
        purpose = resolve_any_purpose(
            db, user.id, ["novel.visual", "visual", "novel.outline", "outline", "novel.chat", "chat"]
        )
        scene_brief = "、".join(f"{s.title}(id={s.id})" for s in scenes) or "无"
        style = (novel.visual_style_prompt or "").strip() or "中国动画风格"
        user_msg = (
            f"为小说《{novel.title}》章节「{chapter.title}」规划恰好 {body.frame_count} 个分镜镜头"
            f"(必须输出 {body.frame_count} 项,不能多也不能少)。\n"
            f"全书画风:{style}\n"
            f"人物外形仅供一致参考(不要在 shot_prompt 里写设定稿/三视图/定妆清单,生图系统会另锁外貌):\n"
            f"{format_characters_for_plan(chars)}\n"
            f"场景库:{scene_brief}\n"
            f"章纲:\n{(chapter.outline or '')[:1500]}\n\n"
            f"正文摘录:\n{content[:3500]}\n\n"
            "只输出 JSON 数组,每项含 caption, shot_prompt, character_ids(数字数组), scene_id(可选数字)。\n"
            "shot_prompt 必须是「单幅剧情插画」说明书,建议 180~400 字,强烈围绕剧情,包含:\n"
            "1) 本镜出场N人:人名(每人只出现一次);\n"
            "2) 【剧情瞬间】谁在做什么、表情、与道具/环境的互动(最重要,写细);\n"
            "3) 【镜头】景别、机位、构图(单镜头,不要多格);\n"
            "4) 【环境与道具】空间与关键物;\n"
            "5) 【光影情绪】氛围。\n"
            "严禁:角色设定稿、三视图、四宫格、表情差分墙、服装拆分、把同一人画成多个。"
            "caption 用短句标题。不要输出 JSON 以外的文字。"
        )
        parts: list[str] = []
        async for ev in stream_chat(
            db,
            user.id,
            novel_id,
            purpose,
            [{"role": "user", "content": user_msg}],
        ):
            if ev.get("type") == "delta":
                parts.append(str(ev.get("content") or ""))
        planned = parse_llm_frames("".join(parts), body.frame_count)
    except Exception:
        planned = None

    if not planned:
        planned = plan_frames_from_text(
            title=chapter.title,
            outline=chapter.outline,
            content=content,
            frame_count=body.frame_count,
            characters=chars,
            scenes=scenes,
        )
    planned = ensure_exact_frame_count(
        planned,
        body.frame_count,
        title=chapter.title,
        outline=chapter.outline,
        content=content,
        characters=chars,
        scenes=scenes,
    )

    board = _get_or_create_board(db, novel_id, chapter_id)
    db.query(ChapterStoryboardFrame).filter_by(storyboard_id=board.id).delete()
    frames: list[ChapterStoryboardFrame] = []
    fallback_ids = list(chapter.characters_involved or []) or [c.id for c in chars[:4]]
    for item in planned:
        cids = item.get("character_ids") or []
        if isinstance(cids, list):
            cids = [int(x) for x in cids if str(x).isdigit() or isinstance(x, int)]
        else:
            cids = []
        sid = item.get("scene_id")
        try:
            sid = int(sid) if sid is not None else None
        except (TypeError, ValueError):
            sid = None
        if sid and not any(s.id == sid for s in scenes):
            sid = scenes[0].id if scenes else None
        caption = (item.get("caption") or "")[:255] or None
        shot = item.get("shot_prompt") or ""
        resolved = resolve_characters_for_shot(
            all_characters=chars,
            character_ids=cids,
            text=f"{caption or ''}\n{shot}",
            # 点名失败时最多兜底 1 人,避免一镜塞满诱发「同脸多人」
            fallback_ids=fallback_ids[:1],
        )
        cids = [c.id for c in resolved] or cids[:2] or fallback_ids[:1]
        by_cid = {c.id: c for c in chars}
        resolved_chars = [by_cid[i] for i in cids if i in by_cid] or list(resolved)
        scene_obj = next((s for s in scenes if s.id == sid), None) if sid else None
        shot = enrich_shot_with_look(
            shot,
            resolved_chars,
            scene=scene_obj,
            caption=caption or "",
        )
        fr = ChapterStoryboardFrame(
            storyboard_id=board.id,
            sort=int(item.get("sort") or 0),
            caption=caption,
            shot_prompt=shot,
            character_ids=cids,
            scene_id=sid,
            status="pending",
        )
        db.add(fr)
        frames.append(fr)
    board.frame_count = body.frame_count
    board.status = "planned"
    board.style_snapshot = (novel.visual_style_prompt or "")[:2000] or None
    board.error = None
    board.updated_at = now()
    db.commit()
    for f in frames:
        db.refresh(f)
    db.refresh(board)
    return _board_out(board, frames)


def _sse(events: AsyncIterator[dict]) -> StreamingResponse:
    async def gen():
        async for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/chapters/{chapter_id}/storyboard/generate")
async def generate_storyboard_images(
    novel_id: int,
    chapter_id: int,
    body: StoryboardGenerateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE 逐镜生图。"""
    novel = get_owned_novel(novel_id, user, db)
    chapter = _chapter(db, novel_id, chapter_id)
    board = db.query(ChapterStoryboard).filter_by(chapter_id=chapter_id).first()
    if board is None:
        raise HTTPException(400, "请先规划分镜")
    frames = (
        db.query(ChapterStoryboardFrame)
        .filter_by(storyboard_id=board.id)
        .order_by(ChapterStoryboardFrame.sort)
        .all()
    )
    if not frames:
        raise HTTPException(400, "分镜为空,请先规划")
    if body.frame_ids:
        want = set(body.frame_ids)
        targets = [f for f in frames if f.id in want]
    elif body.regenerate_all:
        targets = list(frames)
    else:
        targets = [f for f in frames if f.status != "ready" or not f.image_url]
    if not targets:
        targets = list(frames)

    skill_hint = ""
    skill_label = ""
    if body.skill_id:
        skill = db.query(Skill).filter_by(id=body.skill_id, user_id=user.id).first()
        if skill is None:
            raise HTTPException(404, "技能不存在或无权使用")
        skill_hint = build_skill_context(skill, max_total=1200).strip()
        skill_label = skill.name

    char_list = db.query(Character).filter_by(novel_id=novel_id).all()
    chars_all = {c.id: c for c in char_list}
    scenes_all = {s.id: s for s in db.query(NovelScene).filter_by(novel_id=novel_id).all()}
    fallback_ids = list(chapter.characters_involved or [])
    board_id = board.id
    user_id = user.id
    missing_sheet = [c.name for c in char_list if not (c.reference_image_urls or [])]
    # 序列化进闭包;生图兜底最多 1 人,减少无点名时多人同框克隆
    chapter_fallback = list(fallback_ids[:1])
    target_ids = [f.id for f in targets]

    async def events():
        db2 = SessionLocal()
        try:
            board2 = db2.get(ChapterStoryboard, board_id)
            if board2 is None:
                yield {"type": "error", "detail": "分镜板不存在"}
                return
            # 生图前刷新全书画风快照,确保与设定库「保存画风」一致
            novel2 = db2.get(Novel, novel_id) or novel
            style_now = (getattr(novel2, "visual_style_prompt", None) or "").strip()
            board2.style_snapshot = style_now[:2000] or None
            board2.status = "generating"
            board2.error = None
            db2.commit()
            yield {"type": "status", "detail": f"开始生成 {len(target_ids)} 张分镜…"}
            if skill_label:
                yield {
                    "type": "status",
                    "detail": f"已选用技能「{skill_label}」指导本批生图构图/镜头语言",
                }
            if style_now:
                yield {
                    "type": "status",
                    "detail": f"全书画风已锁定:「{style_now[:120]}」— 本批分镜严格按此画风生成",
                }
            else:
                yield {
                    "type": "status",
                    "detail": "提示:设定库尚未填写全书画风,将使用默认中国动画风格。建议先保存画风再重绘。",
                }
            if missing_sheet:
                yield {
                    "type": "status",
                    "detail": (
                        "提示:以下角色还没有设定稿,容易换脸 — "
                        + "、".join(missing_sheet[:8])
                        + "。请先到设定库「人物场景视觉」生成设定稿。"
                    ),
                }
            import asyncio

            found = find_modality_model(db2, user_id, "image")
            ok = 0
            skip_refs = False  # 批次内一旦参考图导致断连,后续镜只用文案
            for i, fid in enumerate(target_ids):
                if i > 0:
                    await asyncio.sleep(0.8)  # 降速,减轻供应商连发掐断
                fr = db2.get(ChapterStoryboardFrame, fid)
                if fr is None:
                    continue
                yield {
                    "type": "frame_start",
                    "frame_id": fr.id,
                    "sort": fr.sort,
                    "caption": fr.caption,
                    "index": i + 1,
                    "total": len(target_ids),
                }
                text_blob = f"{fr.caption or ''}\n{fr.shot_prompt or ''}"
                chs = resolve_characters_for_shot(
                    all_characters=list(chars_all.values()),
                    character_ids=list(fr.character_ids or []),
                    text=text_blob,
                    fallback_ids=chapter_fallback,
                )
                # 回写解析到的人物,方便下次重绘
                if chs and (not fr.character_ids or set(fr.character_ids) != {c.id for c in chs}):
                    fr.character_ids = [c.id for c in chs]
                    db2.commit()
                scene = scenes_all.get(fr.scene_id) if fr.scene_id else None
                prompt = build_shot_image_prompt(
                    novel=novel2,
                    caption=fr.caption or "",
                    shot_prompt=fr.shot_prompt or "",
                    characters=chs,
                    scene=scene,
                    skill_hint=skill_hint or None,
                )
                ref_urls = [] if skip_refs else collect_character_ref_urls(chs, limit=1)
                try:
                    if found:
                        target = resolve_modality_route(
                            db2, user_id, "novel.visual", "image", provider_id=found[0], model_id=found[1]
                        )
                        try:
                            urls = await generate_image(
                                target,
                                prompt,
                                user_id,
                                reference_images=ref_urls or None,
                            )
                        except HTTPException as e:
                            detail = str(e.detail if hasattr(e, "detail") else e)
                            # 网络类失败:去掉参考图再试一次
                            if ref_urls and ("网络错误" in detail or "ConnectError" in detail or "Timeout" in detail):
                                skip_refs = True
                                yield {
                                    "type": "status",
                                    "detail": "上游连接不稳,后续镜改为纯文生图(仍锁外貌文案),正在重试本镜…",
                                }
                                await asyncio.sleep(1.0)
                                urls = await generate_image(target, prompt, user_id, reference_images=None)
                            else:
                                raise
                        url = urls[0] if urls else None
                        source = f"{target.provider.name}/{target.model_id}"
                        if ref_urls and not skip_refs:
                            source += "+定妆参考"
                        if skill_label:
                            source += f"+技能:{skill_label}"
                    else:
                        from ..ai.local_image import render_local_card

                        url = render_local_card(user_id, fr.caption or "分镜", context=prompt[:200])
                        source = "local/pillow"
                    if not url:
                        raise RuntimeError("未返回图片")
                    fr.image_url = url
                    fr.status = "ready"
                    fr.error = None
                    ok += 1
                    db2.commit()
                    yield {
                        "type": "frame_done",
                        "frame_id": fr.id,
                        "image_url": url,
                        "source": source,
                        "index": i + 1,
                        "total": len(target_ids),
                        "characters": [c.name for c in chs],
                    }
                except Exception as e:  # noqa: BLE001
                    fr.status = "failed"
                    fr.error = str(getattr(e, "detail", None) or e)[:400]
                    db2.commit()
                    yield {"type": "frame_error", "frame_id": fr.id, "detail": fr.error}

            board2 = db2.get(ChapterStoryboard, board_id)
            if board2:
                ready = (
                    db2.query(ChapterStoryboardFrame)
                    .filter_by(storyboard_id=board_id, status="ready")
                    .count()
                )
                board2.status = "ready" if ready else "failed"
                board2.updated_at = now()
                db2.commit()
            yield {"type": "done", "ok": ok, "total": len(targets)}
        finally:
            db2.close()

    return _sse(events())


@router.post("/chapters/{chapter_id}/storyboard/video")
async def generate_storyboard_video(
    novel_id: int,
    chapter_id: int,
    body: StoryboardVideoIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE 根据分镜生成短视频。"""
    novel = get_owned_novel(novel_id, user, db)
    chapter = _chapter(db, novel_id, chapter_id)
    board = db.query(ChapterStoryboard).filter_by(chapter_id=chapter_id).first()
    if board is None:
        raise HTTPException(400, "请先规划分镜")
    frames = (
        db.query(ChapterStoryboardFrame)
        .filter_by(storyboard_id=board.id)
        .order_by(ChapterStoryboardFrame.sort)
        .all()
    )
    prompt = build_video_prompt_from_frames(novel, chapter, frames, body.prompt)
    board.video_prompt = prompt
    board.updated_at = now()
    db.commit()
    board_id = board.id
    user_id = user.id

    async def events():
        db2 = SessionLocal()
        try:
            found = find_modality_model(db2, user_id, "video")
            if not found:
                yield {
                    "type": "error",
                    "detail": "未配置视频模型。请到个人中心为「小说分镜/生图」或媒体用途添加视频模型。",
                }
                return
            target = resolve_modality_route(
                db2, user_id, "novel.visual", "video", provider_id=found[0], model_id=found[1]
            )
            yield {"type": "status", "detail": f"视频生成中({target.provider.name}/{target.model_id})…"}
            video_url = None
            async for ev in generate_video(target, prompt, user_id):
                et = ev.get("type")
                if et in ("status", "media_status"):
                    yield {"type": "status", "detail": ev.get("detail")}
                elif et == "video":
                    video_url = ev.get("url")
                    yield {"type": "video", "url": video_url}
                elif et == "error":
                    yield {"type": "error", "detail": ev.get("detail")}
                    return
            board2 = db2.get(ChapterStoryboard, board_id)
            if board2 and video_url:
                board2.video_url = video_url
                board2.updated_at = now()
                db2.commit()
            yield {"type": "done", "url": video_url}
        finally:
            db2.close()

    return _sse(events())


@router.put("/chapters/{chapter_id}/storyboard/frames/{frame_id}", response_model=StoryboardFrameOut)
def update_frame(
    novel_id: int,
    chapter_id: int,
    frame_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    _chapter(db, novel_id, chapter_id)
    board = db.query(ChapterStoryboard).filter_by(chapter_id=chapter_id).first()
    if board is None:
        raise HTTPException(404, "分镜不存在")
    fr = db.query(ChapterStoryboardFrame).filter_by(id=frame_id, storyboard_id=board.id).first()
    if fr is None:
        raise HTTPException(404, "镜头不存在")
    if "caption" in body:
        fr.caption = (body.get("caption") or "")[:255] or None
    if "shot_prompt" in body and body["shot_prompt"] is not None:
        fr.shot_prompt = str(body["shot_prompt"])[:2000]
    if "character_ids" in body and isinstance(body["character_ids"], list):
        fr.character_ids = [int(x) for x in body["character_ids"]]
    if "scene_id" in body:
        fr.scene_id = body["scene_id"]
    db.commit()
    db.refresh(fr)
    return _frame_out(fr)
