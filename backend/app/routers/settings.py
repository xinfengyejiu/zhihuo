"""设定库:世界观条目 / 人物卡 / 关系 / 规则页,全部书级隔离;支持 AI 解析 markdown 批量导入。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..ai.extract import extract_json_array
from ..ai.gateway import resolve_route
from ..database import get_db
from ..models import (
    RELATIONSHIP_TYPES,
    WORLD_CATEGORIES,
    Character,
    CharacterRelationship,
    Foreshadow,
    NovelRule,
    User,
    WorldEntry,
)
from ..routers.novels import get_owned_novel
from ..schemas import (
    CharacterIn,
    CharacterOut,
    RelationshipIn,
    RelationshipOut,
    RuleIn,
    RuleOut,
    WorldEntryIn,
    WorldEntryOut,
)
from ..security import get_current_user

router = APIRouter(prefix="/novels/{novel_id}", tags=["settings"])


def _check_category(cat: str):
    if cat not in WORLD_CATEGORIES:
        raise HTTPException(400, f"未知分类:{cat}")


# ---------- 世界观 ----------
@router.get("/world", response_model=list[WorldEntryOut])
def list_world(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(WorldEntry).filter_by(novel_id=novel_id).order_by(WorldEntry.sort, WorldEntry.id).all()


@router.post("/world", response_model=WorldEntryOut)
def create_world(
    novel_id: int,
    body: WorldEntryIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    _check_category(body.category)
    entry = WorldEntry(novel_id=novel_id, **body.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.put("/world/{entry_id}", response_model=WorldEntryOut)
def update_world(
    novel_id: int,
    entry_id: int,
    body: WorldEntryIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    _check_category(body.category)
    entry = db.query(WorldEntry).filter_by(id=entry_id, novel_id=novel_id).first()
    if entry is None:
        raise HTTPException(404, "条目不存在")
    for k, v in body.model_dump().items():
        setattr(entry, k, v)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/world/{entry_id}")
def delete_world(
    novel_id: int, entry_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    entry = db.query(WorldEntry).filter_by(id=entry_id, novel_id=novel_id).first()
    if entry is None:
        raise HTTPException(404, "条目不存在")
    db.delete(entry)
    db.commit()
    return {"ok": True}


# ---------- 人物 ----------
@router.get("/characters", response_model=list[CharacterOut])
def list_characters(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(Character).filter_by(novel_id=novel_id).order_by(Character.id).all()


@router.post("/characters", response_model=CharacterOut)
def create_character(
    novel_id: int,
    body: CharacterIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    if db.query(Character).filter_by(novel_id=novel_id, name=body.name).first():
        raise HTTPException(400, "同名人物已存在")
    data = body.model_dump()
    data["reference_image_urls"] = data.get("reference_image_urls") or []
    row = Character(novel_id=novel_id, **data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/characters/{character_id}", response_model=CharacterOut)
def update_character(
    novel_id: int,
    character_id: int,
    body: CharacterIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    row = db.query(Character).filter_by(id=character_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "人物不存在")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/characters/{character_id}")
def delete_character(
    novel_id: int, character_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    row = db.query(Character).filter_by(id=character_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "人物不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------- 关系 ----------
@router.get("/relationships", response_model=list[RelationshipOut])
def list_relationships(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(CharacterRelationship).filter_by(novel_id=novel_id).order_by(CharacterRelationship.id).all()


@router.post("/relationships", response_model=RelationshipOut)
def create_relationship(
    novel_id: int,
    body: RelationshipIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    if body.type not in RELATIONSHIP_TYPES:
        raise HTTPException(400, f"未知关系类型:{body.type}")
    if body.from_character_id == body.to_character_id:
        raise HTTPException(400, "不能和自己建立关系")
    for cid in (body.from_character_id, body.to_character_id):
        if not db.query(Character).filter_by(id=cid, novel_id=novel_id).first():
            raise HTTPException(400, f"人物 {cid} 不存在于本书")
    row = CharacterRelationship(novel_id=novel_id, **body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/relationships/{relationship_id}")
def delete_relationship(
    novel_id: int,
    relationship_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_novel(novel_id, user, db)
    row = db.query(CharacterRelationship).filter_by(id=relationship_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "关系不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------- 规则页 ----------
@router.get("/rules", response_model=list[RuleOut])
def list_rules(novel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_novel(novel_id, user, db)
    return db.query(NovelRule).filter_by(novel_id=novel_id).order_by(NovelRule.priority.desc(), NovelRule.id).all()


@router.post("/rules", response_model=RuleOut)
def create_rule(
    novel_id: int, body: RuleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    row = NovelRule(novel_id=novel_id, **body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    novel_id: int, rule_id: int, body: RuleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    row = db.query(NovelRule).filter_by(id=rule_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "规则不存在")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/rules/{rule_id}")
def delete_rule(
    novel_id: int, rule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    row = db.query(NovelRule).filter_by(id=rule_id, novel_id=novel_id).first()
    if row is None:
        raise HTTPException(404, "规则不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------- AI 批量导入(markdown/文本 → 结构化回填) ----------
_CHARACTER_PROMPT = (
    "你是小说设定库结构化助手。从用户提供的 markdown/文本中提取【所有人物】,输出严格的 JSON 数组,"
    "不要输出任何解释或其他内容。数组元素格式:\n"
    '{"name":"姓名","aliases":"别名,多个用顿号分隔,没有则空字符串","role":"protagonist|support|villain|minor",'
    '"occupation":"职业","organization":"所属组织","appearance":"外貌","personality":"性格","goal":"目标动机","notes":"其他备注"}\n'
    "规则:字段值尽量取自原文,原文没有的填空字符串;role 按语义选最接近的一个"
    "(protagonist=主角, support=配角, villain=反派, minor=龙套);不要遗漏任何人。"
)

_RELATIONSHIP_PROMPT = (
    "你是小说设定库结构化助手。从用户提供的文本中提取【人物关系】,输出严格的 JSON 数组,"
    "不要输出任何解释或其他内容。数组元素格式:\n"
    '{"from":"人物A姓名","to":"人物B姓名","type":"family|mentor|rival|lover|ally|enemy|other","intimacy":-100到100的整数,"note":"关系备注"}\n'
    "规则:from/to 必须使用原文中的姓名;type 选最接近的"
    "(family=亲人, mentor=师徒, rival=对手, lover=恋人, ally=盟友/同门, enemy=敌对, other=其他);"
    "intimacy 按亲疏判断(挚友/恋人 60-90, 同事/普通 20-40, 陌生 0, 有怨 -40, 死敌 -80);note 简短说明。"
)

_FORESHADOW_PROMPT = (
    "你是小说设定库结构化助手。从用户提供的文本中提取【伏笔清单】,输出严格的 JSON 数组,"
    "不要输出任何解释或其他内容。数组元素格式:\n"
    '{"description":"伏笔内容,一句话","status":"planted|recalled","note":"补充说明,可为空字符串"}\n'
    "规则:每条伏笔一个元素;原文明确说已回收/已揭晓的用 recalled,否则 planted;note 可为空。"
)

_WORLD_PROMPT = (
    "你是小说设定库结构化助手。从用户提供的文本中提取【世界观设定条目】,输出严格的 JSON 数组,"
    "不要输出任何解释或其他内容。数组元素格式:\n"
    '{"category":"geography|faction|power|item|event|other","title":"条目标题","content":"条目详细内容"}\n'
    "规则:category 按语义分类(geography=地理, faction=势力/组织, power=力量体系/功法, item=物品/法宝, event=大事件, other=其他);"
    "content 保留原文细节,可整理为多行文本;一个条目一个主题,不要把所有内容塞进一条。"
)


def _get_markdown(body: dict | None) -> str:
    markdown = ((body or {}).get("markdown") or "").strip()
    if len(markdown) < 10:
        raise HTTPException(400, "请粘贴至少一段有效的设定文本(10 字以上)")
    return markdown


@router.post("/import-characters")
def import_characters(
    novel_id: int, body: dict | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    markdown = _get_markdown(body)
    items = extract_json_array(db, user.id, _CHARACTER_PROMPT, markdown, novel_id)
    created, skipped = 0, []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if db.query(Character).filter_by(novel_id=novel_id, name=name).first():
            skipped.append(name)
            continue
        role = item.get("role") if item.get("role") in ("protagonist", "support", "villain", "minor") else "support"
        db.add(
            Character(
                novel_id=novel_id,
                name=name[:64],
                aliases=(str(item.get("aliases") or "").strip() or None),
                role=role,
                occupation=(str(item.get("occupation") or "").strip() or None),
                organization=(str(item.get("organization") or "").strip() or None),
                appearance=(str(item.get("appearance") or "").strip() or None),
                personality=(str(item.get("personality") or "").strip() or None),
                goal=(str(item.get("goal") or "").strip() or None),
                notes=(str(item.get("notes") or "").strip() or None),
            )
        )
        created += 1
    db.commit()
    message = f"已新增 {created} 个人物"
    if skipped:
        message += f",跳过重名:{('、'.join(skipped))[:100]}"
    return {"created": created, "skipped": skipped, "message": message}


@router.post("/import-relationships")
def import_relationships(
    novel_id: int, body: dict | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    markdown = _get_markdown(body)
    items = extract_json_array(db, user.id, _RELATIONSHIP_PROMPT, markdown, novel_id)
    name_map = {c.name: c for c in db.query(Character).filter_by(novel_id=novel_id).all()}

    def ensure(name: str) -> Character:
        name = name.strip()[:64]
        if name in name_map:
            return name_map[name]
        c = Character(novel_id=novel_id, name=name, role="support")
        db.add(c)
        db.flush()
        name_map[name] = c
        return c

    created, skipped = 0, []
    for item in items:
        fr, to = str(item.get("from") or "").strip(), str(item.get("to") or "").strip()
        if not fr or not to or fr == to:
            continue
        rel_type = item.get("type") if item.get("type") in RELATIONSHIP_TYPES else "other"
        try:
            intimacy = max(-100, min(100, int(item.get("intimacy", 0))))
        except (TypeError, ValueError):
            intimacy = 0
        a, b = ensure(fr), ensure(to)
        dup = (
            db.query(CharacterRelationship)
            .filter_by(novel_id=novel_id, from_character_id=a.id, to_character_id=b.id, type=rel_type)
            .first()
        )
        if dup:
            skipped.append(f"{fr}↔{to}")
            continue
        db.add(
            CharacterRelationship(
                novel_id=novel_id,
                from_character_id=a.id,
                to_character_id=b.id,
                type=rel_type,
                intimacy=intimacy,
                note=(str(item.get("note") or "").strip() or None),
            )
        )
        created += 1
    db.commit()
    # 统计自动新建的人物卡
    final_names = {c.name for c in db.query(Character).filter_by(novel_id=novel_id).all()}
    initial_names = set(name_map.keys())
    message = f"已新增 {created} 条关系"
    new_chars = [n for n in final_names - initial_names if n]
    if new_chars:
        message += f",自动新建人物卡:{('、'.join(new_chars))[:80]}"
    if skipped:
        message += f",跳过重复:{('、'.join(skipped))[:80]}"
    return {"created": created, "skipped": skipped, "message": message}


@router.post("/import-foreshadows")
def import_foreshadows(
    novel_id: int, body: dict | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    markdown = _get_markdown(body)
    items = extract_json_array(db, user.id, _FORESHADOW_PROMPT, markdown, novel_id)
    created = 0
    for item in items:
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        status = item.get("status") if item.get("status") in ("planted", "recalled", "overdue") else "planted"
        note = str(item.get("note") or "").strip()
        # 备注并入描述尾部,保持台账单字段可读
        full_description = f"{description}({note})" if note else description
        db.add(
            Foreshadow(
                novel_id=novel_id,
                description=full_description[:500],
                status=status,
            )
        )
        created += 1
    db.commit()
    return {"created": created, "message": f"已新增 {created} 条伏笔"}


@router.post("/import-world")
def import_world(
    novel_id: int, body: dict | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    get_owned_novel(novel_id, user, db)
    markdown = _get_markdown(body)
    items = extract_json_array(db, user.id, _WORLD_PROMPT, markdown, novel_id)
    created, skipped = 0, []
    for item in items:
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title:
            continue
        if db.query(WorldEntry).filter_by(novel_id=novel_id, title=title).first():
            skipped.append(title)
            continue
        category = item.get("category") if item.get("category") in WORLD_CATEGORIES else "other"
        db.add(WorldEntry(novel_id=novel_id, category=category, title=title[:128], content=content or None))
        created += 1
    db.commit()
    message = f"已新增 {created} 个条目"
    if skipped:
        message += f",跳过同名:{('、'.join(skipped))[:100]}"
    return {"created": created, "skipped": skipped, "message": message}
