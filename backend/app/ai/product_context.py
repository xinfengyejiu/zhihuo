"""按产品工作区组装系统提示:人设 + Skills + KB 片段。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import KnowledgeBase, MediaAccount, ResourceMount, Skill
from .skill_pack import build_skill_context


def _mounted_skills(
    db: Session,
    user_id: int,
    product_key: str,
    workspace_type: str,
    workspace_id: int,
    stage: str | None = None,
) -> list[Skill]:
    q = db.query(ResourceMount).filter_by(
        user_id=user_id,
        product_key=product_key,
        workspace_type=workspace_type,
        workspace_id=workspace_id,
        resource_type="skill",
    )
    if stage:
        q = q.filter(ResourceMount.stage.in_([stage, ""]))
    mounts = q.all()
    skills: list[Skill] = []
    for m in mounts:
        sk = db.query(Skill).filter_by(id=m.resource_id, user_id=user_id).first()
        if sk:
            skills.append(sk)
    return skills


def _mounted_kb_snippets(
    db: Session,
    user_id: int,
    product_key: str,
    workspace_type: str,
    workspace_id: int,
    query: str,
    limit: int = 4,
) -> list[str]:
    mounts = (
        db.query(ResourceMount)
        .filter_by(
            user_id=user_id,
            product_key=product_key,
            workspace_type=workspace_type,
            workspace_id=workspace_id,
            resource_type="kb",
        )
        .all()
    )
    if not mounts:
        return []
    # 轻量关键词召回,避免强依赖 embedding 配置
    from ..models import KbChunk

    kb_ids = [m.resource_id for m in mounts]
    chunks = (
        db.query(KbChunk)
        .filter(KbChunk.kb_id.in_(kb_ids))
        .order_by(KbChunk.id.desc())
        .limit(80)
        .all()
    )
    q = (query or "").strip()
    scored: list[tuple[int, str]] = []
    for ch in chunks:
        text = ch.content or ""
        score = 0
        if q:
            for token in q.replace("，", " ").replace(",", " ").split():
                if token and token in text:
                    score += 2
        else:
            score = 1
        if score:
            scored.append((score, text[:800]))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:limit]]


def build_media_system_prompt(
    db: Session,
    user_id: int,
    account: MediaAccount | None,
    *,
    user_message: str = "",
    extra_skill_id: int | None = None,
    mode: str = "write",
) -> str:
    """mode=write 成稿创作; mode=brainstorm 选题策划(可不绑账号)。"""
    if mode == "brainstorm" or account is None:
        parts = [
            "你是自媒体选题策划助手。帮助用户发散、筛选、打磨选题。",
            "给出标题要具体、有信息差或情绪点;每个选题附一句角度说明。",
            "可用列表输出,方便用户一键存入选题库。不做站内爬取,只基于对话与公开常识。",
            "若用户要「成稿+配图」,直接输出完整图文草稿;需要插图时使用 Markdown 占位 "
            "`![具体画面说明](images/简短英文slug.png)`,系统会自动调用图片模型生成真实配图。",
        ]
    else:
        parts = [
            "你是自媒体写作助手,根据账号人设与平台风格协作创作短内容/长文。",
            "输出可直接发布的文案;需要标题时给出 1 个主标题与可选备选。",
            "写图文笔记时,在对应位置插入 Markdown 图片占位 "
            "`![具体画面说明(含风格/文字要点)](images/简短英文slug.png)`,"
            "不要编造 http 链接;系统会自动生成真实图片并替换占位。",
        ]

    if account is not None:
        parts.append(f"## 账号\n名称:{account.name}\n平台:{account.platform}")
        if account.persona:
            parts.append(f"## 人设与口吻\n{account.persona}")
        if account.audience:
            parts.append(f"## 受众\n{account.audience}")
        if account.forbidden:
            parts.append(f"## 禁忌\n{account.forbidden}")
        skills = _mounted_skills(db, user_id, "media", "media_account", account.id, stage="chat")
        snippets = _mounted_kb_snippets(
            db, user_id, "media", "media_account", account.id, user_message
        )
    else:
        skills = []
        snippets = []

    if extra_skill_id:
        sk = db.query(Skill).filter_by(id=extra_skill_id, user_id=user_id).first()
        if sk and sk not in skills:
            skills.append(sk)
    for sk in skills:
        parts.append(build_skill_context(sk))

    if snippets:
        parts.append("## 知识库参考\n" + "\n---\n".join(snippets))

    return "\n\n".join(parts)


def build_drama_system_prompt(
    *,
    title: str,
    source_type: str,
    story_excerpt: str,
    bible: dict | None = None,
    extra_skill: str = "",
    kb_snippets: list[str] | None = None,
) -> str:
    parts = [
        "你是「织火」漫剧创作顾问:协助把小说/剧本改成可拍摄的漫剧(分集、分镜、对白、首帧画面)。",
        "回答要具体可执行;讨论分镜时强调单幅剧情瞬间,不要建议画成角色设定稿或三视图。",
        "当用户要求输出分镜剧本/视频分镜/可应用剧本时:直接输出完整 JSON(不要 Markdown 围栏),"
        "每镜必须含 startSec/endSec、shotSize、cameraMove、angle、visual、speaker、line、imagePrompt;"
        "时间轴连续,便于用户一键「应用剧本」。",
        f"当前项目:《{title or '未命名漫剧'}》来源:{source_type or 'chat'}",
    ]
    excerpt = (story_excerpt or "").strip()
    if excerpt:
        parts.append("## 故事摘录\n" + excerpt[:2500])
    if bible:
        eps = bible.get("episodes") or []
        names = [a.get("name") for a in (bible.get("assets") or []) if a.get("name")]
        parts.append(
            f"## 已有圣经摘要\n剧名:{bible.get('title') or title}\n"
            f"集数:{len(eps)} 人物:{'、'.join(str(n) for n in names[:12]) or '未锁定'}"
        )
    if kb_snippets:
        parts.append("## 知识库参考\n" + "\n---\n".join(kb_snippets[:4]))
    if extra_skill:
        parts.append(extra_skill)
    return "\n\n".join(parts)
