"""漫剧创作:项目、导入小说/剧本、生成圣经、首帧、对话。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai.drama_pipeline import (
    DEFAULT_DRAMA_STYLE,
    STORY_CLIP,
    DRAMA_SCRIPT_SYSTEM,
    clip_story,
    first_frame_prompt,
    flatten_shots,
    parse_drama_bible,
    script_user_prompt,
)
from ..ai.gateway import resolve_any_purpose, stream_chat
from ..ai.media_gen import find_modality_model, generate_image, resolve_modality_route
from ..ai.novel_import import decode_txt_bytes
from ..ai.product_context import build_drama_system_prompt
from ..ai.skill_pack import build_skill_context
from ..database import SessionLocal, get_db
from ..models import (
    Chapter,
    Character,
    ChatMessage,
    ChatSession,
    DramaProject,
    DramaShot,
    Novel,
    Skill,
    User,
    now,
)
from ..routers.novels import get_owned_novel
from ..schemas import ORMModel
from ..security import get_current_user

router = APIRouter(prefix="/drama", tags=["drama"])


class DramaProjectIn(BaseModel):
    title: str | None = None
    source_type: str = "paste"
    novel_id: int | None = None
    source_text: str | None = None
    style_prompt: str | None = None
    episode_count: int = Field(default=5, ge=3, le=8)
    shots_per_episode: int = Field(default=3, ge=2, le=6)


class DramaProjectUpdateIn(BaseModel):
    title: str | None = None
    source_text: str | None = None
    style_prompt: str | None = None
    episode_count: int | None = Field(default=None, ge=3, le=8)
    shots_per_episode: int | None = Field(default=None, ge=2, le=6)
    novel_id: int | None = None
    source_type: str | None = None


class DramaShotOut(ORMModel):
    id: int
    episode_index: int
    shot_index: int
    sort: int
    caption: str | None
    start_sec: int = 0
    end_sec: int = 5
    shot_size: str | None = None
    camera_move: str | None = None
    angle: str | None = None
    speaker: str | None = None
    visual: str | None
    line: str | None
    image_prompt: str | None
    image_url: str | None
    candidate_urls: list[str] = []
    selected_index: int = 0
    refine_note: str | None = None
    status: str
    error: str | None


class DramaProjectOut(ORMModel):
    id: int
    title: str
    source_type: str
    novel_id: int | None
    source_text: str | None
    style_prompt: str | None
    episode_count: int
    shots_per_episode: int
    bible_json: dict
    status: str
    error: str | None
    shot_count: int = 0
    ready_count: int = 0
    updated_at: object | None = None
    shots: list[DramaShotOut] = []


class DramaChatIn(BaseModel):
    message: str
    skill_id: int | None = None


class DramaSessionCreateIn(BaseModel):
    title: str | None = None
    project_id: int | None = None


class DramaSessionRenameIn(BaseModel):
    title: str


class ApplyScriptIn(BaseModel):
    """把助手回复(或显式 content)解析为视频分镜并写入项目。"""
    project_id: int | None = None
    session_id: int | None = None
    content: str | None = None
    create_title: str | None = None
    episode_count: int = Field(default=5, ge=3, le=8)
    shots_per_episode: int = Field(default=3, ge=2, le=6)


class ApplySourceIn(BaseModel):
    """把聊天里的剧本正文存为项目来源(可自行粘贴)。"""
    project_id: int | None = None
    session_id: int | None = None
    content: str | None = None
    create_title: str | None = None


class ImportNovelIn(BaseModel):
    novel_id: int


class GenerateFramesIn(BaseModel):
    shot_ids: list[int] | None = None
    regenerate_all: bool = False
    candidates: int = Field(default=3, ge=1, le=4)
    refine_note: str | None = None
    # True:只重绘当前勾选那一张;False:整镜换一批候选
    replace_selected_only: bool = False


class SelectShotImageIn(BaseModel):
    selected_index: int = Field(default=0, ge=0, le=7)
    refine_note: str | None = None


GENERAL_DRAMA_WORKSPACE = 0


def _owned(db: Session, user: User, project_id: int) -> DramaProject:
    row = db.query(DramaProject).filter_by(id=project_id, owner_id=user.id).first()
    if row is None:
        raise HTTPException(404, "漫剧项目不存在")
    return row


def _shot_out(s: DramaShot) -> DramaShotOut:
    item = DramaShotOut.model_validate(s)
    urls = list(s.candidate_urls or [])
    if not urls and s.image_url:
        urls = [s.image_url]
    item.candidate_urls = urls
    item.selected_index = int(s.selected_index or 0)
    if item.selected_index >= len(urls) and urls:
        item.selected_index = 0
    if urls and not item.image_url:
        item.image_url = urls[item.selected_index] if urls else None
    return item


def _sync_shot_image(sh: DramaShot) -> None:
    urls = [u for u in (sh.candidate_urls or []) if u]
    sh.candidate_urls = urls
    if not urls:
        sh.image_url = None
        sh.selected_index = 0
        return
    idx = int(sh.selected_index or 0)
    if idx < 0 or idx >= len(urls):
        idx = 0
    sh.selected_index = idx
    sh.image_url = urls[idx]


def _project_out(p: DramaProject, *, with_shots: bool = False) -> DramaProjectOut:
    shots = list(p.shots or [])
    item = DramaProjectOut.model_validate(p)
    item.shot_count = len(shots)
    item.ready_count = sum(1 for s in shots if s.status == "ready" and s.image_url)
    item.shots = [_shot_out(s) for s in shots] if with_shots else []
    return item


def _chapter_text(chapter: Chapter) -> str:
    for v in chapter.versions:
        if v.id == chapter.current_version_id:
            return v.content or ""
    return (chapter.outline or "") if hasattr(chapter, "outline") else ""


def _assemble_novel_text(db: Session, novel: Novel, limit: int = STORY_CLIP) -> str:
    parts = [f"《{novel.title}》"]
    if novel.summary:
        parts.append(f"简介:{novel.summary}")
    chars = db.query(Character).filter_by(novel_id=novel.id).limit(12).all()
    if chars:
        bits = []
        for c in chars:
            look = (c.visual_prompt or c.appearance or c.personality or "")[:80]
            bits.append(f"{c.name}{('('+look+')') if look else ''}")
        parts.append("人物:" + "、".join(bits))
    chapters = (
        db.query(Chapter).filter_by(novel_id=novel.id).order_by(Chapter.sort, Chapter.id).limit(40).all()
    )
    for ch in chapters:
        body = _chapter_text(ch) or (ch.outline or "")
        if not body.strip():
            continue
        parts.append(f"## {ch.title}\n{body.strip()}")
        if sum(len(x) for x in parts) >= limit:
            break
    text, _ = clip_story("\n\n".join(parts), limit)
    return text


def _cast_hint(db: Session, project: DramaProject) -> str:
    bible = project.bible_json or {}
    assets = bible.get("assets") or []
    lines = [f"- {a.get('name')}: {a.get('prompt')}" for a in assets if a.get("name")]
    if lines:
        return "\n".join(lines[:16])
    if project.novel_id:
        chars = db.query(Character).filter_by(novel_id=project.novel_id).limit(8).all()
        return "\n".join(
            f"- {c.name}: {(c.visual_prompt or c.appearance or c.personality or '')[:160]}"
            for c in chars
        )
    return ""


def _replace_shots(db: Session, project: DramaProject, bible: dict[str, Any]) -> None:
    db.query(DramaShot).filter_by(project_id=project.id).delete()
    for item in flatten_shots(bible):
        db.add(
            DramaShot(
                project_id=project.id,
                episode_index=item["episode_index"],
                shot_index=item["shot_index"],
                sort=item["sort"],
                caption=item["caption"],
                start_sec=int(item.get("start_sec") or 0),
                end_sec=int(item.get("end_sec") or 5),
                shot_size=item.get("shot_size"),
                camera_move=item.get("camera_move"),
                angle=item.get("angle"),
                speaker=item.get("speaker"),
                visual=item["visual"],
                line=item["line"],
                image_prompt=item["image_prompt"],
                status="pending",
            )
        )


def _sse(events):
    async def gen():
        async for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/projects", response_model=list[DramaProjectOut])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(DramaProject).filter_by(owner_id=user.id).order_by(DramaProject.updated_at.desc()).all()
    )
    return [_project_out(p) for p in rows]


@router.post("/projects", response_model=DramaProjectOut)
def create_project(
    body: DramaProjectIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source_type = body.source_type if body.source_type in ("novel", "upload", "paste") else "paste"
    style = (body.style_prompt or "").strip() or None
    text = (body.source_text or "").strip()
    novel_id = None
    title = (body.title or "").strip()[:128]
    if body.novel_id:
        novel = get_owned_novel(body.novel_id, user, db)
        novel_id = novel.id
        source_type = "novel"
        title = title or novel.title
        text = _assemble_novel_text(db, novel)
        style = style or (novel.visual_style_prompt or None)
    text, _ = clip_story(text)
    if not title:
        title = (text[:18] + "…") if len(text) > 18 else (text or "未命名漫剧")
    row = DramaProject(
        owner_id=user.id,
        title=title[:128],
        source_type=source_type,
        novel_id=novel_id,
        source_text=text or None,
        style_prompt=style,
        episode_count=body.episode_count,
        shots_per_episode=body.shots_per_episode,
        bible_json={},
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _project_out(row, with_shots=True)


@router.get("/projects/{project_id}", response_model=DramaProjectOut)
def get_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _project_out(_owned(db, user, project_id), with_shots=True)


@router.put("/projects/{project_id}", response_model=DramaProjectOut)
def update_project(
    project_id: int,
    body: DramaProjectUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, user, project_id)
    if body.title is not None:
        t = body.title.strip()
        if t:
            row.title = t[:128]
    if body.source_text is not None:
        text, _ = clip_story(body.source_text)
        row.source_text = text or None
    if body.style_prompt is not None:
        row.style_prompt = body.style_prompt.strip() or None
    if body.episode_count is not None:
        row.episode_count = body.episode_count
    if body.shots_per_episode is not None:
        row.shots_per_episode = body.shots_per_episode
    if body.source_type in ("novel", "upload", "paste"):
        row.source_type = body.source_type
    if body.novel_id is not None:
        if body.novel_id:
            novel = get_owned_novel(body.novel_id, user, db)
            row.novel_id = novel.id
            row.source_type = "novel"
            row.source_text = _assemble_novel_text(db, novel)
            if not row.style_prompt:
                row.style_prompt = novel.visual_style_prompt
            if row.title in ("", "未命名漫剧"):
                row.title = novel.title
        else:
            row.novel_id = None
    row.updated_at = now()
    db.commit()
    db.refresh(row)
    return _project_out(row, with_shots=True)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned(db, user, project_id)
    db.query(ChatSession).filter_by(user_id=user.id, product_key="drama", workspace_id=row.id).delete()
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/import-novel", response_model=DramaProjectOut)
def import_novel(
    project_id: int,
    body: ImportNovelIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, user, project_id)
    novel = get_owned_novel(body.novel_id, user, db)
    row.novel_id = novel.id
    row.source_type = "novel"
    row.source_text = _assemble_novel_text(db, novel)
    if not row.style_prompt:
        row.style_prompt = novel.visual_style_prompt
    if not row.title or row.title == "未命名漫剧":
        row.title = novel.title
    row.updated_at = now()
    db.commit()
    db.refresh(row)
    return _project_out(row, with_shots=True)


@router.post("/projects/{project_id}/upload", response_model=DramaProjectOut)
async def upload_script(
    project_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, user, project_id)
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "文件超过 8MB")
    text = decode_txt_bytes(raw)
    text, clipped = clip_story(text)
    if len(text) < 40:
        raise HTTPException(400, "剧本太短,请上传更完整的 txt/md")
    row.source_text = text
    row.source_type = "upload"
    name = (file.filename or "").rsplit(".", 1)[0][:40]
    if name and (not row.title or row.title == "未命名漫剧"):
        row.title = name
    row.error = "已截取前约1.2万字" if clipped else None
    row.updated_at = now()
    db.commit()
    db.refresh(row)
    return _project_out(row, with_shots=True)


class ScriptGenerateIn(BaseModel):
    skill_id: int | None = None


@router.post("/projects/{project_id}/script/generate")
async def generate_script(
    project_id: int,
    body: ScriptGenerateIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, user, project_id)
    story = (row.source_text or "").strip()
    if len(story) < 40:
        raise HTTPException(400, "请先选择小说或上传/粘贴剧本")
    pid, uid = row.id, user.id
    skill_id = (body.skill_id if body else None) or None

    async def events():
        db2 = SessionLocal()
        try:
            proj = db2.get(DramaProject, pid)
            if proj is None:
                yield {"type": "error", "detail": "项目不存在"}
                return
            proj.status = "generating"
            proj.error = None
            db2.commit()
            skill_extra = ""
            skill_name = ""
            if skill_id:
                sk = db2.query(Skill).filter_by(id=skill_id, user_id=uid).first()
                if sk is None:
                    yield {"type": "error", "detail": "所选技能不存在"}
                    return
                skill_extra = build_skill_context(sk)
                skill_name = sk.name
            status_tip = f"正在生成 {proj.episode_count} 集 × {proj.shots_per_episode} 镜…"
            if skill_name:
                status_tip = f"技能「{skill_name}」· {status_tip}"
            yield {"type": "status", "detail": status_tip}
            user_row = db2.get(User, uid)
            if user_row is None:
                yield {"type": "error", "detail": "用户不存在"}
                return
            sess = _drama_session(db2, user_row, pid, title=proj.title or "漫剧对话")
            chat_user = (
                f"【生成剧本】请按项目设定输出视频分镜 JSON。"
                f"{proj.episode_count} 集 × {proj.shots_per_episode} 镜"
                + (f"；技能:{skill_name}" if skill_name else "")
            )
            db2.add(ChatMessage(session_id=sess.id, role="user", content=chat_user))
            if skill_id is not None:
                sess.skill_id = skill_id
            sess.updated_at = now()
            db2.commit()
            yield {"type": "chat_user", "content": chat_user}
            purpose = resolve_any_purpose(
                db2,
                uid,
                ["drama.script", "drama.chat", "novel.outline", "outline", "novel.chat", "chat"],
            )
            user_msg = script_user_prompt(
                proj.source_text or "",
                title=proj.title,
                episode_count=proj.episode_count,
                shots_per=proj.shots_per_episode,
                style=proj.style_prompt or "",
                cast_hint=_cast_hint(db2, proj),
            )
            system = DRAMA_SCRIPT_SYSTEM
            if skill_extra.strip():
                system = f"{DRAMA_SCRIPT_SYSTEM}\n\n# 分镜技能约束\n{skill_extra.strip()}"
            parts: list[str] = []
            async for ev in stream_chat(
                db2,
                uid,
                None,
                purpose,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                product_key="drama",
                workspace_id=pid,
            ):
                if ev.get("type") == "delta":
                    piece = str(ev.get("content") or "")
                    if piece:
                        parts.append(piece)
                        yield {"type": "delta", "content": piece}
                elif ev.get("type") == "reasoning":
                    piece = str(ev.get("content") or "")
                    if piece:
                        yield {"type": "reasoning", "content": piece}
                elif ev.get("type") == "error":
                    yield {"type": "error", "detail": ev.get("detail") or "生成失败"}
                    return
            full = "".join(parts).strip()
            db2.add(ChatMessage(session_id=sess.id, role="assistant", content=full or "（未生成内容）"))
            sess.updated_at = now()
            bible = parse_drama_bible(
                full,
                proj.source_text or "",
                episode_count=proj.episode_count,
                shots_per=proj.shots_per_episode,
                title=proj.title,
            )
            proj.bible_json = bible
            if bible.get("title") and proj.title in ("", "未命名漫剧"):
                proj.title = str(bible["title"])[:128]
            _replace_shots(db2, proj, bible)
            proj.status = "scripted"
            proj.updated_at = now()
            db2.commit()
            db2.refresh(proj)
            yield {"type": "done", "project": _project_out(proj, with_shots=True).model_dump(mode="json")}
        except Exception as e:  # noqa: BLE001
            p2 = db2.get(DramaProject, pid)
            if p2:
                p2.status = "draft"
                p2.error = str(getattr(e, "detail", None) or e)[:400]
                db2.commit()
            yield {"type": "error", "detail": str(getattr(e, "detail", None) or e)[:400]}
        finally:
            db2.close()

    return _sse(events())


def _shot_dict(sh: DramaShot) -> dict[str, Any]:
    return {
        "caption": sh.caption,
        "visual": sh.visual,
        "line": sh.line,
        "image_prompt": sh.image_prompt,
        "start_sec": sh.start_sec,
        "end_sec": sh.end_sec,
        "shot_size": sh.shot_size,
        "camera_move": sh.camera_move,
        "angle": sh.angle,
        "speaker": sh.speaker,
        "refine_note": sh.refine_note,
    }


def _merge_cast_assets(db: Session, project: DramaProject, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用小说人物卡补强 bible.assets 的造型描述,便于跨镜锁脸。"""
    by_name: dict[str, dict[str, Any]] = {}
    for a in assets:
        name = str(a.get("name") or "").strip()
        if not name:
            continue
        kind = str(a.get("kind") or "character")
        by_name[f"{kind}:{name}"] = {
            "id": a.get("id"),
            "kind": kind,
            "name": name,
            "prompt": str(a.get("prompt") or "").strip(),
        }
    if project.novel_id:
        chars = db.query(Character).filter_by(novel_id=project.novel_id).limit(20).all()
        for c in chars:
            key = f"character:{c.name}"
            look = (c.visual_prompt or c.appearance or c.personality or "").strip()
            refs = [u for u in (c.reference_image_urls or []) if u]
            row = by_name.get(key) or {"id": f"N{c.id}", "kind": "character", "name": c.name, "prompt": ""}
            if look and (not row.get("prompt") or len(str(row.get("prompt") or "")) < 20):
                row["prompt"] = look[:220]
            if refs:
                row["reference_urls"] = refs[:2]
            by_name[key] = row
    # 无小说时也保留 bible assets
    out = list(by_name.values())
    # 非角色资产顺带保留
    for a in assets:
        if (a.get("kind") or "character") == "character":
            continue
        name = str(a.get("name") or "").strip()
        if name:
            out.append(
                {
                    "id": a.get("id"),
                    "kind": a.get("kind") or "prop",
                    "name": name,
                    "prompt": str(a.get("prompt") or "").strip()[:220],
                }
            )
    return out


def _refs_for_shot(shot: dict[str, Any], assets: list[dict[str, Any]]) -> list[str]:
    from ..ai.drama_pipeline import _shot_cast_names

    names = set(_shot_cast_names(shot, assets))
    refs: list[str] = []
    for a in assets:
        if (a.get("kind") or "character") != "character":
            continue
        if str(a.get("name") or "").strip() not in names:
            continue
        for u in a.get("reference_urls") or []:
            if u and u not in refs:
                refs.append(u)
            if len(refs) >= 2:
                return refs
    return refs


async def _gen_one_frame_url(
    db2: Session,
    uid: int,
    *,
    found: tuple | None,
    prompt: str,
    caption: str,
    reference_images: list[str] | None = None,
) -> tuple[str | None, str]:
    if found:
        target = resolve_modality_route(
            db2, uid, "drama.visual", "image", provider_id=found[0], model_id=found[1]
        )
        urls = await generate_image(
            target, prompt, uid, n=1, reference_images=reference_images or None
        )
        url = urls[0] if urls else None
        return url, f"{target.provider.name}/{target.model_id}"
    from ..ai.local_image import render_local_card

    return render_local_card(uid, caption or "首帧", context=prompt[:200]), "local/pillow"


@router.post("/projects/{project_id}/frames/generate")
async def generate_frames(
    project_id: int,
    body: GenerateFramesIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, user, project_id)
    shots = db.query(DramaShot).filter_by(project_id=row.id).order_by(DramaShot.sort).all()
    if not shots:
        raise HTTPException(400, "请先生成剧本分镜")
    if body.shot_ids:
        want = set(body.shot_ids)
        targets = [s for s in shots if s.id in want]
    elif body.regenerate_all:
        targets = list(shots)
    else:
        targets = [s for s in shots if s.status != "ready" or not (s.image_url or s.candidate_urls)]
    if not targets:
        targets = list(shots)
    pid, uid = row.id, user.id
    target_ids = [s.id for s in targets]
    bible = row.bible_json or {}
    assets = _merge_cast_assets(db, row, list(bible.get("assets") or []))
    style = (row.style_prompt or "").strip()
    pal = (bible.get("palette") or {}).get("note") or ""
    if pal and pal not in (style or ""):
        style = f"{style} {pal}".strip() if style else pal
    if not style:
        style = DEFAULT_DRAMA_STYLE
    want_n = int(body.candidates or 3)
    refine_note = (body.refine_note or "").strip() or None
    replace_one = bool(body.replace_selected_only)

    async def events():
        import asyncio

        db2 = SessionLocal()
        try:
            proj = db2.get(DramaProject, pid)
            if proj is None:
                yield {"type": "error", "detail": "项目不存在"}
                return
            # 空画风时落库锁定,后续镜与重绘共用同一风格声明
            if not (proj.style_prompt or "").strip():
                proj.style_prompt = style[:500]
            proj.status = "generating"
            db2.commit()
            mode = "按描述重绘勾选图" if replace_one else f"每镜 {want_n} 张候选"
            yield {
                "type": "status",
                "detail": f"开始生成首帧({mode}) · {len(target_ids)} 镜 · 已锁定画风与角色定妆…",
            }
            found = find_modality_model(db2, uid, "image")
            ok = 0
            for i, sid in enumerate(target_ids):
                if i:
                    await asyncio.sleep(0.6)
                sh = db2.get(DramaShot, sid)
                if sh is None:
                    continue
                if refine_note is not None:
                    sh.refine_note = refine_note
                yield {
                    "type": "frame_start",
                    "shot_id": sh.id,
                    "index": i + 1,
                    "total": len(target_ids),
                    "caption": sh.caption,
                }
                ep_title = ""
                for ep in (proj.bible_json or {}).get("episodes") or []:
                    if int(ep.get("index") or 0) == sh.episode_index:
                        ep_title = str(ep.get("title") or "")
                        break
                shot_payload = _shot_dict(sh)
                refs = _refs_for_shot(shot_payload, assets)
                try:
                    if replace_one:
                        # 只重绘当前勾选位
                        cands = list(sh.candidate_urls or [])
                        if not cands and sh.image_url:
                            cands = [sh.image_url]
                        sel = int(sh.selected_index or 0)
                        if sel < 0 or (cands and sel >= len(cands)):
                            sel = 0
                        prompt = first_frame_prompt(
                            style=style,
                            shot=shot_payload,
                            assets=assets,
                            episode_title=ep_title,
                            refine_note=sh.refine_note or "",
                            variant_index=sel,
                        )
                        url, source = await _gen_one_frame_url(
                            db2,
                            uid,
                            found=found,
                            prompt=prompt,
                            caption=sh.caption or "首帧",
                            reference_images=refs,
                        )
                        if not url:
                            raise RuntimeError("未返回图片")
                        while len(cands) <= sel:
                            cands.append(url)
                        cands[sel] = url
                        sh.candidate_urls = cands
                        sh.selected_index = sel
                        _sync_shot_image(sh)
                        sh.status = "ready"
                        sh.error = None
                        ok += 1
                        db2.commit()
                        yield {
                            "type": "frame_done",
                            "shot_id": sh.id,
                            "image_url": sh.image_url,
                            "candidate_urls": sh.candidate_urls,
                            "selected_index": sh.selected_index,
                            "source": source,
                            "index": i + 1,
                            "total": len(target_ids),
                        }
                    else:
                        collected: list[str] = []
                        source = "local/pillow"
                        for vi in range(want_n):
                            if vi:
                                await asyncio.sleep(0.45)
                            prompt = first_frame_prompt(
                                style=style,
                                shot=shot_payload,
                                assets=assets,
                                episode_title=ep_title,
                                refine_note=sh.refine_note or "",
                                variant_index=vi,
                            )
                            url, source = await _gen_one_frame_url(
                                db2,
                                uid,
                                found=found,
                                prompt=prompt,
                                caption=f"{sh.caption or '首帧'}-{vi + 1}",
                                reference_images=refs,
                            )
                            if url:
                                collected.append(url)
                            yield {
                                "type": "frame_progress",
                                "shot_id": sh.id,
                                "variant": vi + 1,
                                "need": want_n,
                                "image_url": url,
                            }
                        if not collected:
                            raise RuntimeError("未返回图片")
                        sh.candidate_urls = collected
                        sh.selected_index = 0
                        _sync_shot_image(sh)
                        sh.status = "ready"
                        sh.error = None
                        ok += 1
                        db2.commit()
                        yield {
                            "type": "frame_done",
                            "shot_id": sh.id,
                            "image_url": sh.image_url,
                            "candidate_urls": sh.candidate_urls,
                            "selected_index": sh.selected_index,
                            "source": source,
                            "index": i + 1,
                            "total": len(target_ids),
                        }
                except Exception as e:  # noqa: BLE001
                    sh.status = "failed"
                    sh.error = str(getattr(e, "detail", None) or e)[:400]
                    db2.commit()
                    yield {"type": "frame_error", "shot_id": sh.id, "detail": sh.error}
            proj2 = db2.get(DramaProject, pid)
            if proj2:
                ready = db2.query(DramaShot).filter_by(project_id=pid, status="ready").count()
                proj2.status = "ready" if ready else "scripted"
                proj2.error = None
                db2.commit()
            yield {"type": "done", "ok": ok, "total": len(target_ids)}
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "detail": str(getattr(e, "detail", None) or e)[:400]}
        finally:
            db2.close()

    return _sse(events())


@router.put("/projects/{project_id}/shots/{shot_id}/select", response_model=DramaShotOut)
def select_shot_image(
    project_id: int,
    shot_id: int,
    body: SelectShotImageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """勾选某张候选为首帧,并可保存改图描述。"""
    _owned(db, user, project_id)
    sh = db.query(DramaShot).filter_by(id=shot_id, project_id=project_id).first()
    if sh is None:
        raise HTTPException(404, "分镜不存在")
    urls = list(sh.candidate_urls or [])
    if not urls and sh.image_url:
        urls = [sh.image_url]
        sh.candidate_urls = urls
    if not urls:
        raise HTTPException(400, "该镜还没有候选图")
    if body.selected_index >= len(urls):
        raise HTTPException(400, f"只能勾选 0–{len(urls) - 1}")
    sh.selected_index = body.selected_index
    if body.refine_note is not None:
        sh.refine_note = (body.refine_note or "").strip() or None
    _sync_shot_image(sh)
    db.commit()
    db.refresh(sh)
    return _shot_out(sh)


def _drama_session(db: Session, user: User, workspace_id: int, title: str | None = None) -> ChatSession:
    sess = (
        db.query(ChatSession)
        .filter_by(user_id=user.id, product_key="drama", workspace_id=workspace_id)
        .order_by(ChatSession.id.desc())
        .first()
    )
    if sess:
        return sess
    sess = ChatSession(
        user_id=user.id,
        product_key="drama",
        workspace_id=workspace_id,
        title=(title or ("漫剧对话" if workspace_id else "新对话"))[:64],
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def _get_drama_chat_session(db: Session, user: User, session_id: int) -> ChatSession:
    sess = (
        db.query(ChatSession)
        .filter_by(id=session_id, user_id=user.id, product_key="drama")
        .first()
    )
    if sess is None:
        raise HTTPException(404, "对话不存在")
    return sess


def _session_item(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "updated_at": s.updated_at,
        "skill_id": s.skill_id,
        "project_id": s.workspace_id if s.workspace_id and s.workspace_id != GENERAL_DRAMA_WORKSPACE else None,
        "workspace_id": s.workspace_id,
    }


def _chat_messages(sess: ChatSession, db: Session) -> list[dict]:
    msgs = (
        db.query(ChatMessage).filter_by(session_id=sess.id).order_by(ChatMessage.id.asc()).limit(200).all()
    )
    return [
        {"id": m.id, "role": m.role, "content": m.content, "model_name": m.model_name}
        for m in msgs
        if m.role in ("user", "assistant")
    ]


def _last_assistant(db: Session, session_id: int) -> ChatMessage | None:
    return (
        db.query(ChatMessage)
        .filter_by(session_id=session_id, role="assistant")
        .order_by(ChatMessage.id.desc())
        .first()
    )


def _last_user(db: Session, session_id: int) -> ChatMessage | None:
    return (
        db.query(ChatMessage)
        .filter_by(session_id=session_id, role="user")
        .order_by(ChatMessage.id.desc())
        .first()
    )


def _ensure_project_for_apply(
    db: Session,
    user: User,
    *,
    project_id: int | None,
    create_title: str | None,
    episode_count: int = 5,
    shots_per: int = 3,
) -> DramaProject:
    if project_id:
        return _owned(db, user, project_id)
    title = (create_title or "未命名漫剧").strip()[:128] or "未命名漫剧"
    row = DramaProject(
        owner_id=user.id,
        title=title,
        source_type="chat",
        source_text="",
        episode_count=episode_count,
        shots_per_episode=shots_per,
        bible_json={},
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _apply_bible_to_project(
    db: Session,
    project: DramaProject,
    raw: str,
    *,
    episode_count: int | None = None,
    shots_per: int | None = None,
) -> DramaProject:
    n = episode_count or project.episode_count or 5
    k = shots_per or project.shots_per_episode or 3
    bible = parse_drama_bible(
        raw,
        project.source_text or raw,
        episode_count=n,
        shots_per=k,
        title=project.title,
    )
    eps = bible.get("episodes") or []
    if not eps or not any((ep.get("shots") or []) for ep in eps):
        raise HTTPException(400, "未能解析出分镜剧本,请让助手输出含 episodes/shots 的 JSON")
    project.bible_json = bible
    if bible.get("title") and project.title in ("", "未命名漫剧"):
        project.title = str(bible["title"])[:128]
    project.episode_count = len(eps) or n
    if eps and eps[0].get("shots"):
        project.shots_per_episode = len(eps[0]["shots"])
    _replace_shots(db, project, bible)
    project.status = "scripted"
    project.error = None
    project.updated_at = now()
    db.commit()
    db.refresh(project)
    return project


async def _stream_drama_chat(
    *,
    user: User,
    sess: ChatSession,
    text: str,
    skill_id: int | None,
    project: DramaProject | None,
):
    sid, uid = sess.id, user.id
    pid = project.id if project else (sess.workspace_id or GENERAL_DRAMA_WORKSPACE)

    async def events():
        db2 = SessionLocal()
        try:
            sess_row = db2.get(ChatSession, sid)
            if sess_row is None:
                yield {"type": "error", "detail": "对话不存在"}
                return
            proj_row = db2.get(DramaProject, pid) if pid and pid != GENERAL_DRAMA_WORKSPACE else None
            db2.add(ChatMessage(session_id=sid, role="user", content=text))
            if skill_id is not None:
                sess_row.skill_id = skill_id
            if sess_row.title in ("", "漫剧对话", "新对话"):
                sess_row.title = text[:20]
            sess_row.updated_at = now()
            db2.commit()
            extra = ""
            if skill_id:
                sk = db2.query(Skill).filter_by(id=skill_id, user_id=uid).first()
                if sk:
                    extra = build_skill_context(sk)
            system = build_drama_system_prompt(
                title=(proj_row.title if proj_row else sess_row.title) or "未命名漫剧",
                source_type=(proj_row.source_type if proj_row else "chat"),
                story_excerpt=(proj_row.source_text if proj_row else "") or "",
                bible=(proj_row.bible_json if proj_row else None),
                extra_skill=extra,
            )
            purpose = resolve_any_purpose(
                db2, uid, ["drama.chat", "drama.script", "novel.chat", "chat", "outline"]
            )
            history = (
                db2.query(ChatMessage)
                .filter_by(session_id=sid)
                .order_by(ChatMessage.id.desc())
                .limit(24)
                .all()
            )
            history = list(reversed(history))
            messages = [{"role": "system", "content": system}]
            for m in history:
                if m.role in ("user", "assistant"):
                    messages.append({"role": m.role, "content": m.content[:3500]})
            acc: list[str] = []
            async for ev in stream_chat(
                db2, uid, None, purpose, messages, product_key="drama", workspace_id=pid or None
            ):
                if ev.get("type") == "delta":
                    acc.append(str(ev.get("content") or ""))
                    yield ev
                elif ev.get("type") in ("model", "usage"):
                    yield ev
            full = "".join(acc).strip()
            db2.add(ChatMessage(session_id=sid, role="assistant", content=full or "（无回复）"))
            db2.commit()
            yield {"type": "done"}
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "detail": str(getattr(e, "detail", None) or e)[:400]}
        finally:
            db2.close()

    return _sse(events())


@router.get("/sessions")
def list_drama_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(ChatSession)
        .filter_by(user_id=user.id, product_key="drama")
        .order_by(ChatSession.updated_at.desc())
        .limit(100)
        .all()
    )
    return [_session_item(s) for s in rows]


@router.post("/sessions")
def create_drama_session(
    body: DramaSessionCreateIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    workspace_id = GENERAL_DRAMA_WORKSPACE
    title = (body.title if body and body.title else "新对话")[:64]
    if body and body.project_id:
        proj = _owned(db, user, body.project_id)
        workspace_id = proj.id
        if not (body and body.title):
            title = f"{proj.title}对话"[:64]
    sess = ChatSession(
        user_id=user.id,
        product_key="drama",
        workspace_id=workspace_id,
        title=title,
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return _session_item(sess)


@router.put("/sessions/{session_id}")
def rename_drama_session(
    session_id: int,
    body: DramaSessionRenameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sess = _get_drama_chat_session(db, user, session_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    sess.title = title[:64]
    db.commit()
    return {"ok": True, "title": sess.title}


@router.delete("/sessions/{session_id}")
def delete_drama_session(
    session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    sess = _get_drama_chat_session(db, user, session_id)
    db.delete(sess)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def list_drama_session_messages(
    session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    sess = _get_drama_chat_session(db, user, session_id)
    return _chat_messages(sess, db)


@router.post("/sessions/{session_id}/chat")
async def drama_session_chat(
    session_id: int,
    body: DramaChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(400, "请输入内容")
    sess = _get_drama_chat_session(db, user, session_id)
    project = None
    if sess.workspace_id and sess.workspace_id != GENERAL_DRAMA_WORKSPACE:
        project = _owned(db, user, sess.workspace_id)
    return await _stream_drama_chat(
        user=user, sess=sess, text=text, skill_id=body.skill_id, project=project
    )


@router.post("/chat/apply-source", response_model=DramaProjectOut)
def apply_source_from_chat(
    body: ApplySourceIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把用户粘贴/最后一条用户消息存为项目剧本正文。"""
    content = (body.content or "").strip()
    if not content and body.session_id:
        sess = _get_drama_chat_session(db, user, body.session_id)
        last_u = _last_user(db, sess.id)
        last_a = _last_assistant(db, sess.id)
        # 优先用户消息(自己输入的剧本);否则取助手长文
        pick = None
        if last_u and len((last_u.content or "").strip()) >= 40:
            pick = last_u.content
        elif last_a and len((last_a.content or "").strip()) >= 40:
            pick = last_a.content
        content = (pick or "").strip()
    if len(content) < 40:
        raise HTTPException(400, "剧本太短,请粘贴更完整正文或先在聊天里发送剧本")
    text, clipped = clip_story(content)
    project = _ensure_project_for_apply(
        db, user, project_id=body.project_id, create_title=body.create_title or text[:20]
    )
    project.source_text = text
    project.source_type = "paste" if body.project_id else "chat"
    project.error = "已截取前约1.2万字" if clipped else None
    project.updated_at = now()
    if body.session_id:
        sess = _get_drama_chat_session(db, user, body.session_id)
        sess.workspace_id = project.id
        sess.updated_at = now()
    db.commit()
    db.refresh(project)
    return _project_out(project, with_shots=True)


@router.post("/chat/apply-script", response_model=DramaProjectOut)
def apply_script_from_chat(
    body: ApplyScriptIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把助手输出的视频分镜 JSON 应用到项目(可新建项目)。"""
    content = (body.content or "").strip()
    if not content and body.session_id:
        sess = _get_drama_chat_session(db, user, body.session_id)
        last = _last_assistant(db, sess.id)
        if not last or not (last.content or "").strip():
            raise HTTPException(400, "没有可应用的助手回复,请先聊天或传入 content")
        content = last.content.strip()
    if not content:
        raise HTTPException(400, "没有可应用的剧本内容")
    project = _ensure_project_for_apply(
        db,
        user,
        project_id=body.project_id,
        create_title=body.create_title,
        episode_count=body.episode_count,
        shots_per=body.shots_per_episode,
    )
    if not (project.source_text or "").strip():
        # 若尚无来源,用原文摘一段存档
        project.source_text, _ = clip_story(content)
        project.source_type = project.source_type or "chat"
    project = _apply_bible_to_project(
        db,
        project,
        content,
        episode_count=body.episode_count,
        shots_per=body.shots_per_episode,
    )
    if body.session_id:
        sess = _get_drama_chat_session(db, user, body.session_id)
        sess.workspace_id = project.id
        sess.updated_at = now()
        db.commit()
        db.refresh(project)
    return _project_out(project, with_shots=True)


@router.get("/projects/{project_id}/chat")
def get_chat(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = _owned(db, user, project_id)
    sess = _drama_session(db, user, proj.id)
    return {
        "session_id": sess.id,
        "messages": _chat_messages(sess, db),
    }


@router.post("/projects/{project_id}/chat")
async def post_chat(
    project_id: int,
    body: DramaChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    proj = _owned(db, user, project_id)
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(400, "请输入内容")
    sess = _drama_session(db, user, proj.id)
    return await _stream_drama_chat(
        user=user, sess=sess, text=text, skill_id=body.skill_id, project=proj
    )


@router.post("/projects/{project_id}/chat/apply-script", response_model=DramaProjectOut)
def apply_script_from_project_chat(
    project_id: int,
    body: ApplyScriptIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    proj = _owned(db, user, project_id)
    sess = _drama_session(db, user, proj.id)
    content = (body.content if body else None) or ""
    content = content.strip()
    if not content:
        last = _last_assistant(db, sess.id)
        if not last or not (last.content or "").strip():
            raise HTTPException(400, "没有可应用的助手回复")
        content = last.content.strip()
    return _project_out(
        _apply_bible_to_project(
            db,
            proj,
            content,
            episode_count=(body.episode_count if body else None),
            shots_per=(body.shots_per_episode if body else None),
        ),
        with_shots=True,
    )
