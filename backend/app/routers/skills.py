"""Skills 技能工坊:支持纯提示词与文件夹技能包(整目录落盘注入)。"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..ai.github_skills import fetch_skill_packs, peek_frontmatter, strip_frontmatter
from ..ai.presets import SKILL_PRESETS
from ..ai.skill_pack import (
    build_skill_context,
    delete_pack,
    extract_entry_prompt,
    group_zip_or_folder_into_packs,
    list_pack_files,
    relative_pack_dir,
    write_pack_files,
)
from ..database import get_db
from ..models import SKILL_STAGES, NovelSkillLink, ResourceMount, Skill, User
from ..routers.novels import get_owned_novel
from ..schemas import SkillGithubImportIn, SkillImportIn, SkillIn, SkillMountIn, SkillOut
from ..security import get_current_user

MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_FOLDER_BYTES = 20 * 1024 * 1024

router = APIRouter(prefix="/skills", tags=["skills"])
mount_router = APIRouter(prefix="/novels/{novel_id}/skills", tags=["skills"])


def _check_stage(stage: str):
    if stage not in SKILL_STAGES:
        raise HTTPException(400, f"未知技能阶段:{stage}")


def _skill_out(skill: Skill, *, with_files: bool = False) -> SkillOut:
    files = list_pack_files(skill) if skill.pack_dir else []
    data = SkillOut(
        id=skill.id,
        name=skill.name,
        version=skill.version,
        stage=skill.stage,
        system_prompt=skill.system_prompt,
        params=skill.params or {},
        builtin=skill.builtin,
        scope_tags=skill.scope_tags or [],
        pack_dir=skill.pack_dir,
        file_count=len(files),
        files=files if with_files else [],
    )
    return data


def _sync_novel_skill_mount(db: Session, user_id: int, novel_id: int, stage: str, skill_id: int | None):
    q = db.query(ResourceMount).filter_by(
        user_id=user_id,
        product_key="novel",
        workspace_type="novel",
        workspace_id=novel_id,
        resource_type="skill",
        stage=stage,
    )
    if skill_id is None:
        q.delete(synchronize_session=False)
        return
    row = q.first()
    if row:
        row.resource_id = skill_id
    else:
        db.add(
            ResourceMount(
                user_id=user_id,
                product_key="novel",
                workspace_type="novel",
                workspace_id=novel_id,
                resource_type="skill",
                resource_id=skill_id,
                stage=stage,
            )
        )


def _parse_skill_markdown(markdown: str, name_override: str | None = None) -> tuple[str, str, str]:
    meta = peek_frontmatter(markdown)
    text = strip_frontmatter(markdown)
    name = (name_override or meta.get("name") or "").strip() or "导入的技能"
    version = (meta.get("version") or "1.0.0").strip()[:32] or "1.0.0"
    return name[:64], version, text


def _create_pack_skill(
    db: Session,
    user: User,
    *,
    pack_name: str,
    files: list[tuple[str, bytes]],
    default_stage: str,
    name_override: str | None,
    scope_tags: list[str],
    source: str,
    source_extra: dict | None = None,
) -> Skill | None:
    """创建一条技能并写入文件夹包;已存在同名同阶段则跳过返回 None。"""
    entry = extract_entry_prompt(files)
    meta = peek_frontmatter(entry)
    stage = default_stage
    fm_stage = (meta.get("stage") or "").strip()
    if fm_stage in SKILL_STAGES:
        stage = fm_stage
    name, version, prompt_body = _parse_skill_markdown(entry, name_override or pack_name or meta.get("name"))
    if not prompt_body.strip():
        prompt_body = entry[:8000] or f"技能包:{name}"
    existing = db.query(Skill).filter_by(user_id=user.id, name=name, stage=stage).first()
    if existing:
        return None
    params = {"source": source, "file_count": len(files)}
    if source_extra:
        params.update(source_extra)
    skill = Skill(
        user_id=user.id,
        name=name,
        version=version,
        stage=stage,
        system_prompt=prompt_body,
        params=params,
        builtin=False,
        scope_tags=scope_tags or ["shared"],
    )
    db.add(skill)
    db.flush()
    written = write_pack_files(user.id, skill.id, files)
    skill.pack_dir = relative_pack_dir(user.id, skill.id)
    skill.params = {**(skill.params or {}), "file_count": len(written), "files": written[:40]}
    return skill


@router.get("", response_model=list[SkillOut])
def list_skills(
    scope: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(Skill).filter_by(user_id=user.id).order_by(Skill.builtin.desc(), Skill.id).all()
    if scope:
        rows = [s for s in rows if scope in (s.scope_tags or []) or "shared" in (s.scope_tags or [])]
    return [_skill_out(s) for s in rows]


@router.get("/export")
def export_skills(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Skill).filter_by(user_id=user.id).order_by(Skill.id).all()
    return {
        "version": 1,
        "items": [
            {
                "name": s.name,
                "version": s.version,
                "stage": s.stage,
                "system_prompt": s.system_prompt,
                "params": s.params or {},
                "scope_tags": s.scope_tags or [],
                "builtin": s.builtin,
                "pack_dir": s.pack_dir,
                "files": list_pack_files(s),
            }
            for s in rows
        ],
    }


@router.post("/seed-presets")
def seed_presets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    created = 0
    existing = {(s.name, s.stage) for s in db.query(Skill).filter_by(user_id=user.id).all()}
    for preset in SKILL_PRESETS:
        if (preset["name"], preset["stage"]) in existing:
            continue
        db.add(
            Skill(
                user_id=user.id,
                name=preset["name"],
                version=preset["version"],
                stage=preset["stage"],
                system_prompt=preset["system_prompt"],
                params={},
                builtin=True,
                scope_tags=preset.get("scope_tags") or ["novel", "shared"],
            )
        )
        created += 1
    db.commit()
    return {"created": created}


@router.post("", response_model=SkillOut)
def create_skill(body: SkillIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _check_stage(body.stage)
    skill = Skill(user_id=user.id, **body.model_dump(), builtin=False)
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return _skill_out(skill)


@router.post("/import-markdown", response_model=SkillOut)
def import_markdown(body: SkillImportIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _check_stage(body.stage)
    name, version, text = _parse_skill_markdown(body.markdown, body.name)
    if not text:
        raise HTTPException(400, "markdown 内容为空")
    # 单文件也落成迷你包,便于统一注入
    skill = Skill(
        user_id=user.id,
        name=name,
        version=version,
        stage=body.stage,
        system_prompt=text,
        params={"source": "markdown"},
        builtin=False,
        scope_tags=body.scope_tags or ["shared"],
    )
    db.add(skill)
    db.flush()
    write_pack_files(user.id, skill.id, [("SKILL.md", body.markdown.encode("utf-8"))])
    skill.pack_dir = relative_pack_dir(user.id, skill.id)
    db.commit()
    db.refresh(skill)
    return _skill_out(skill)


@router.post("/import-github")
def import_github(body: SkillGithubImportIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """从 GitHub 安装完整技能文件夹。"""
    _check_stage(body.stage)
    packs = fetch_skill_packs(body.url)
    created = []
    skipped = []
    for pack in packs:
        override = body.name if len(packs) == 1 else None
        skill = _create_pack_skill(
            db,
            user,
            pack_name=pack.name,
            files=pack.files,
            default_stage=body.stage,
            name_override=override,
            scope_tags=body.scope_tags or ["shared"],
            source="github",
            source_extra={"url": body.url},
        )
        if skill is None:
            skipped.append(pack.name)
        else:
            created.append({"id": skill.id, "name": skill.name, "stage": skill.stage, "files": len(pack.files)})
    db.commit()
    if not created and skipped:
        raise HTTPException(400, f"未新建技能(可能已存在):{', '.join(skipped[:8])}")
    if not created:
        raise HTTPException(400, "未安装任何技能")
    return {"ok": True, "created": len(created), "skipped": skipped, "items": created}


def _parse_scope_tags_form(raw: str | None) -> list[str]:
    if not raw:
        return ["shared"]
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            tags = [str(x) for x in data if x]
            return tags or ["shared"]
    except json.JSONDecodeError:
        pass
    return ["shared"]


@router.post("/import-zip")
async def import_zip(
    file: UploadFile = File(...),
    stage: str = Form("chapter"),
    name: str | None = Form(None),
    scope_tags: str = Form('["shared"]'),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传 zip:按文件夹整包安装(目录内全部文件落盘)。"""
    _check_stage(stage)
    tags = _parse_scope_tags_form(scope_tags)
    filename = (file.filename or "").lower()
    if not filename.endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 文件")
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_ZIP_BYTES:
        raise HTTPException(400, "zip 过大(上限 20MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise HTTPException(400, "无效的 zip 文件") from e

    flat: list[tuple[str, bytes]] = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name_path = info.filename.replace("\\", "/")
            if name_path.startswith("__MACOSX/") or "/__MACOSX/" in name_path:
                continue
            flat.append((name_path, zf.read(info)))

    packs = group_zip_or_folder_into_packs(flat)
    if not packs:
        raise HTTPException(400, "zip 内没有可用文件")
    created = []
    skipped = []
    for pack_name, files in packs[:20]:
        override = name if len(packs) == 1 else None
        skill = _create_pack_skill(
            db,
            user,
            pack_name=pack_name,
            files=files,
            default_stage=stage,
            name_override=override,
            scope_tags=tags,
            source="zip",
            source_extra={"filename": file.filename or ""},
        )
        if skill is None:
            skipped.append(pack_name)
        else:
            created.append({"id": skill.id, "name": skill.name, "files": len(files)})
    db.commit()
    if not created and skipped:
        raise HTTPException(400, f"未新建技能(可能已存在):{', '.join(skipped[:8])}")
    if not created:
        raise HTTPException(400, "未安装任何技能")
    return {"ok": True, "created": len(created), "skipped": skipped, "items": created}


@router.post("/import-folder")
async def import_folder(
    files: list[UploadFile] = File(...),
    paths: str = Form("[]"),
    stage: str = Form("chapter"),
    name: str | None = Form(None),
    scope_tags: str = Form('["shared"]'),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """浏览器选择文件夹上传:paths 为 JSON 相对路径数组,与 files 一一对应。"""
    _check_stage(stage)
    tags = _parse_scope_tags_form(scope_tags)
    try:
        rel_paths = json.loads(paths)
    except json.JSONDecodeError as e:
        raise HTTPException(400, "paths 必须是 JSON 数组") from e
    if not isinstance(rel_paths, list) or len(rel_paths) != len(files):
        raise HTTPException(400, "files 与 paths 数量不一致")
    flat: list[tuple[str, bytes]] = []
    total = 0
    for upload, rel in zip(files, rel_paths):
        raw = await upload.read()
        total += len(raw)
        if total > MAX_FOLDER_BYTES:
            raise HTTPException(400, "文件夹过大(上限 20MB)")
        flat.append((str(rel), raw))
    packs = group_zip_or_folder_into_packs(flat)
    if not packs:
        raise HTTPException(400, "文件夹内没有可用文件")
    # 选择文件夹通常是一个技能包
    if len(packs) > 1 and name:
        # 强制合并为一个包
        merged: list[tuple[str, bytes]] = []
        for _, fs in packs:
            merged.extend(fs)
        packs = [(name, merged)]
    created = []
    skipped = []
    for pack_name, pack_files in packs[:10]:
        override = name if len(packs) == 1 else None
        skill = _create_pack_skill(
            db,
            user,
            pack_name=pack_name,
            files=pack_files,
            default_stage=stage,
            name_override=override,
            scope_tags=tags,
            source="folder",
        )
        if skill is None:
            skipped.append(pack_name)
        else:
            created.append({"id": skill.id, "name": skill.name, "files": len(pack_files)})
    db.commit()
    if not created and skipped:
        raise HTTPException(400, f"未新建技能(可能已存在):{', '.join(skipped[:8])}")
    if not created:
        raise HTTPException(400, "未安装任何技能")
    return {"ok": True, "created": len(created), "skipped": skipped, "items": created}


@router.put("/{skill_id}", response_model=SkillOut)
def update_skill(
    skill_id: int, body: SkillIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _check_stage(body.stage)
    skill = db.query(Skill).filter_by(id=skill_id, user_id=user.id).first()
    if skill is None:
        raise HTTPException(404, "技能不存在")
    for k, v in body.model_dump().items():
        setattr(skill, k, v)
    db.commit()
    db.refresh(skill)
    return _skill_out(skill)


@router.delete("/{skill_id}")
def delete_skill(skill_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    skill = db.query(Skill).filter_by(id=skill_id, user_id=user.id).first()
    if skill is None:
        raise HTTPException(404, "技能不存在")
    delete_pack(skill)
    db.delete(skill)
    db.commit()
    return {"ok": True}


@router.get("/{skill_id}", response_model=SkillOut)
def get_skill(skill_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    skill = db.query(Skill).filter_by(id=skill_id, user_id=user.id).first()
    if skill is None:
        raise HTTPException(404, "技能不存在")
    return _skill_out(skill, with_files=True)


@router.get("/{skill_id}/context")
def preview_skill_context(skill_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """预览对话注入时的完整技能上下文。"""
    skill = db.query(Skill).filter_by(id=skill_id, user_id=user.id).first()
    if skill is None:
        raise HTTPException(404, "技能不存在")
    text = build_skill_context(skill)
    return {"id": skill.id, "name": skill.name, "pack_dir": skill.pack_dir, "context": text, "length": len(text)}


def get_mounted_skill(db: Session, novel_id: int, stage: str) -> Skill | None:
    link = db.query(NovelSkillLink).filter_by(novel_id=novel_id, stage=stage).first()
    if link is None:
        return None
    return db.get(Skill, link.skill_id)


@mount_router.get("")
def get_novel_skills(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    links = db.query(NovelSkillLink).filter_by(novel_id=novel_id).all()
    return {l.stage: l.skill_id for l in links}


@mount_router.put("")
def mount_skill(
    novel_id: int, body: SkillMountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    _check_stage(body.stage)
    link = db.query(NovelSkillLink).filter_by(novel_id=novel_id, stage=body.stage).first()
    if body.skill_id is None:
        if link:
            db.delete(link)
        _sync_novel_skill_mount(db, user.id, novel_id, body.stage, None)
    else:
        skill = db.query(Skill).filter(Skill.id == body.skill_id, Skill.user_id == user.id).first()
        if skill is None:
            raise HTTPException(400, "技能不存在")
        if link is None:
            link = NovelSkillLink(novel_id=novel_id, stage=body.stage)
            db.add(link)
        link.skill_id = body.skill_id
        _sync_novel_skill_mount(db, user.id, novel_id, body.stage, body.skill_id)
    db.commit()
    return {"ok": True}
