from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def now():
    return datetime.now(timezone.utc)


# ---------- 章节 / 发布状态机 ----------
CHAPTER_STATUSES = [
    "draft",           # 草稿
    "pending_review",  # 待审核
    "approved",        # 审核通过(进入发布列表与阅读器)
    "scheduled",       # 已排期
    "published",       # 已发布
    "rejected",        # 审核驳回(等同草稿,需修改后重新提交)
]
REVIEWABLE_FROM = ("approved", "scheduled", "published")
# 供 .in_() 查询使用的列表形式
ReviewableFilter = list(REVIEWABLE_FROM)

# AI 用途路由
# 兼容旧短名;完整用途见 products.PRODUCTS + novel./media. 前缀
AI_PURPOSES = ["chat", "outline", "chapter", "polish", "analysis", "summary", "embedding", "visual"]
AI_PURPOSE_LABELS = {
    "chat": "自由对话",
    "outline": "大纲生成",
    "chapter": "章节正文",
    "polish": "润色",
    "analysis": "复盘分析",
    "summary": "前情摘要",
    "embedding": "向量记忆",
    "topic_expand": "选题扩写",
    "visual": "分镜/生图",
    "script": "漫剧剧本",
}

TAG_TYPES = ["genre", "style", "status", "custom"]


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(64), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    last_product_key = Column(String(32), nullable=True)  # novel / media / ...
    created_at = Column(DateTime, default=now)


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(32), nullable=False)
    type = Column(String(16), nullable=False, default="custom")  # genre/style/status/custom
    color = Column(String(16), nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tag_user_name"),)


class Novel(Base):
    __tablename__ = "novels"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(128), nullable=False)
    summary = Column(Text, nullable=True)
    cover = Column(String(500), nullable=True)
    visual_style_prompt = Column(Text, nullable=True)  # 全书画风/镜头语言,生图时注入
    status = Column(String(16), nullable=False, default="draft")  # draft/serializing/completed
    target_words = Column(Integer, nullable=True)
    current_words = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    tags = relationship("Tag", secondary="novel_tags", lazy="selectin")
    volumes = relationship("Volume", backref="novel", cascade="all, delete-orphan", lazy="selectin")
    platforms = relationship("NovelPlatform", backref="novel", cascade="all, delete-orphan", lazy="selectin")


class NovelTag(Base):
    __tablename__ = "novel_tags"

    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class WorldEntry(Base):
    """世界观条目:地理/势力/力量体系/物品/事件等。"""

    __tablename__ = "world_entries"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(32), nullable=False, default="other")
    title = Column(String(128), nullable=False)
    content = Column(Text, nullable=True)
    sort = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=now, onupdate=now)


WORLD_CATEGORIES = ["geography", "faction", "power", "item", "event", "other"]
WORLD_CATEGORY_LABELS = {
    "geography": "地理",
    "faction": "势力",
    "power": "力量体系",
    "item": "物品",
    "event": "事件",
    "other": "其他",
}


class Character(Base):
    """人物卡。"""

    __tablename__ = "characters"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    aliases = Column(String(255), nullable=True)
    role = Column(String(16), nullable=False, default="support")  # protagonist/support/villain/minor
    occupation = Column(String(128), nullable=True)
    organization = Column(String(128), nullable=True)
    appearance = Column(Text, nullable=True)
    visual_prompt = Column(Text, nullable=True)  # 固定外貌/服装/画风约束,保证跨章一致
    reference_image_urls = Column(JSON, nullable=False, default=list)  # /gen-assets/... 定妆图
    personality = Column(Text, nullable=True)
    goal = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=now, onupdate=now)

    __table_args__ = (UniqueConstraint("novel_id", "name", name="uq_character_novel_name"),)


class NovelScene(Base):
    """小说场景库:地点/氛围/定场图,分镜生图时复用。"""

    __tablename__ = "novel_scenes"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    atmosphere = Column(String(255), nullable=True)
    reference_image_urls = Column(JSON, nullable=False, default=list)
    sort = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=now, onupdate=now)


class ChapterStoryboard(Base):
    """章节分镜板:一章一组镜头 + 可选成片视频。"""

    __tablename__ = "chapter_storyboards"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    status = Column(String(16), nullable=False, default="draft")  # draft / planned / generating / ready / failed
    frame_count = Column(Integer, nullable=False, default=4)
    style_snapshot = Column(Text, nullable=True)
    video_url = Column(String(500), nullable=True)
    video_prompt = Column(Text, nullable=True)
    error = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=now, onupdate=now)
    created_at = Column(DateTime, default=now)


class ChapterStoryboardFrame(Base):
    __tablename__ = "chapter_storyboard_frames"

    id = Column(Integer, primary_key=True)
    storyboard_id = Column(
        Integer, ForeignKey("chapter_storyboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sort = Column(Integer, nullable=False, default=0)
    caption = Column(String(255), nullable=True)  # 分镜标题/节拍
    shot_prompt = Column(Text, nullable=False, default="")
    image_url = Column(String(500), nullable=True)
    character_ids = Column(JSON, nullable=False, default=list)
    scene_id = Column(Integer, ForeignKey("novel_scenes.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(16), nullable=False, default="pending")  # pending / ready / failed
    error = Column(String(500), nullable=True)


class CharacterRelationship(Base):
    __tablename__ = "character_relationships"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    from_character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    to_character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(32), nullable=False, default="other")  # family/mentor/rival/lover/ally/enemy/other
    intimacy = Column(Integer, nullable=False, default=0)  # -100 ~ 100
    note = Column(String(500), nullable=True)


RELATIONSHIP_TYPES = ["family", "mentor", "rival", "lover", "ally", "enemy", "other"]
RELATIONSHIP_LABELS = {
    "family": "亲人",
    "mentor": "师徒",
    "rival": "对手",
    "lover": "恋人",
    "ally": "盟友",
    "enemy": "敌对",
    "other": "其他",
}


class NovelRule(Base):
    """小说"宪法":全书写作规则,生成时强制注入。"""

    __tablename__ = "novel_rules"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)


class OutlineDoc(Base):
    """全书大纲文档:与卷/章节结构相互独立,作为创作蓝图在 AI 生成时注入。"""

    __tablename__ = "outline_docs"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(128), nullable=False, default="大纲")
    content = Column(Text, nullable=False, default="")
    sort = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=now, onupdate=now)


class Volume(Base):
    __tablename__ = "volumes"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(128), nullable=False)
    outline = Column(Text, nullable=True)
    sort = Column(Integer, nullable=False, default=0)


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    volume_id = Column(Integer, ForeignKey("volumes.id", ondelete="CASCADE"), nullable=True)
    title = Column(String(128), nullable=False)
    sort = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="draft", index=True)
    current_version_id = Column(Integer, ForeignKey("chapter_versions.id", ondelete="SET NULL"), nullable=True)
    word_count = Column(Integer, nullable=False, default=0)
    # 章纲
    outline = Column(Text, nullable=True)          # 本章目标/剧情要点
    characters_involved = Column(JSON, nullable=True)  # [character_id, ...]
    foreshadow_note = Column(Text, nullable=True)  # 本章需埋/需回收的伏笔
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    versions = relationship(
        "ChapterVersion",
        foreign_keys="ChapterVersion.chapter_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    novel = relationship("Novel", foreign_keys=[novel_id])


class ChapterVersion(Base):
    __tablename__ = "chapter_versions"

    id = Column(Integer, primary_key=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="manual")  # ai_draft / manual
    content = Column(Text, nullable=False, default="")
    word_count = Column(Integer, nullable=False, default=0)
    model_name = Column(String(128), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=now)


class ReviewRecord(Base):
    __tablename__ = "review_records"

    id = Column(Integer, primary_key=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("chapter_versions.id", ondelete="SET NULL"), nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(16), nullable=False)  # approved / rejected / submitted
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)


class Foreshadow(Base):
    """伏笔台账(M1 简版,章节复盘在 M3 完善)。"""

    __tablename__ = "foreshadows"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    plant_chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    expect_recall_chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(16), nullable=False, default="planted")  # planted/recalled/overdue


class AiProvider(Base):
    """用户自配的 OpenAI 兼容供应商。"""

    __tablename__ = "ai_providers"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key_encrypted = Column(String(1000), nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    remark = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=now)

    models = relationship("AiModel", backref="provider", cascade="all, delete-orphan", lazy="selectin")


class AiModel(Base):
    __tablename__ = "ai_models"

    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id = Column(String(128), nullable=False)
    display_name = Column(String(128), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    # text / image / video;空则按 model_id 启发式推断
    modality = Column(String(16), nullable=True)

    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_ai_model"),)


class AiModelRoute(Base):
    """用途路由:每个 AI 用途 -> 供应商+模型+参数(+备用)。"""

    __tablename__ = "ai_model_routes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    purpose = Column(String(64), nullable=False)  # novel.chapter / media.chat / embedding ...
    provider_id = Column(Integer, ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True)
    model_id = Column(String(128), nullable=True)
    temperature = Column(Float, nullable=False, default=0.8)
    max_tokens = Column(Integer, nullable=False, default=4096)
    fallback_provider_id = Column(Integer, ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True)
    fallback_model_id = Column(String(128), nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "purpose", name="uq_route_user_purpose"),)


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="SET NULL"), nullable=True)
    product_key = Column(String(32), nullable=True, index=True)  # novel / media
    workspace_id = Column(Integer, nullable=True)  # novel_id or media_account_id
    purpose = Column(String(64), nullable=False)
    provider_name = Column(String(64), nullable=True)
    model_name = Column(String(128), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now, index=True)


class AiCallError(Base):
    """AI 调用失败/降级日志,供个人中心用量看板排查。"""

    __tablename__ = "ai_call_errors"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="SET NULL"), nullable=True)
    product_key = Column(String(32), nullable=True, index=True)
    workspace_id = Column(Integer, nullable=True)
    purpose = Column(String(64), nullable=False, index=True)
    provider_name = Column(String(64), nullable=True)
    model_name = Column(String(128), nullable=True)
    status = Column(String(16), nullable=False, default="failed")  # failed / degraded
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=now, index=True)


class RollingSummary(Base):
    """前情摘要:每本书一份,审核通过后滚动更新。"""

    __tablename__ = "rolling_summaries"

    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), primary_key=True)
    content = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=now, onupdate=now)


class ReadingProgress(Base):
    __tablename__ = "reading_progress"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    updated_at = Column(DateTime, default=now, onupdate=now)

    __table_args__ = (UniqueConstraint("user_id", "novel_id", name="uq_progress_user_novel"),)


# ---------- 阅读器书城(独立读者账号,与创作台 User 分离) ----------
class ReaderUser(Base):
    __tablename__ = "reader_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(64), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=now)
    last_login_at = Column(DateTime, nullable=True)


class ReaderProgress(Base):
    __tablename__ = "reader_progress"

    id = Column(Integer, primary_key=True)
    reader_user_id = Column(Integer, ForeignKey("reader_users.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    position_pct = Column(Integer, nullable=False, default=0)  # 0-100
    updated_at = Column(DateTime, default=now, onupdate=now)

    __table_args__ = (UniqueConstraint("reader_user_id", "novel_id", name="uq_reader_progress_user_novel"),)


class ReaderHistory(Base):
    __tablename__ = "reader_history"

    id = Column(Integer, primary_key=True)
    reader_user_id = Column(Integer, ForeignKey("reader_users.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    last_chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    visited_at = Column(DateTime, default=now, onupdate=now)

    __table_args__ = (UniqueConstraint("reader_user_id", "novel_id", name="uq_reader_history_user_novel"),)


class ReaderFavorite(Base):
    __tablename__ = "reader_favorites"

    id = Column(Integer, primary_key=True)
    reader_user_id = Column(Integer, ForeignKey("reader_users.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=now)

    __table_args__ = (UniqueConstraint("reader_user_id", "novel_id", name="uq_reader_favorite_user_novel"),)


class ReaderLike(Base):
    __tablename__ = "reader_likes"

    id = Column(Integer, primary_key=True)
    reader_user_id = Column(Integer, ForeignKey("reader_users.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=now)

    __table_args__ = (UniqueConstraint("reader_user_id", "novel_id", name="uq_reader_like_user_novel"),)


class NovelReaderStats(Base):
    __tablename__ = "novel_reader_stats"

    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), primary_key=True)
    read_count = Column(Integer, nullable=False, default=0)
    like_count = Column(Integer, nullable=False, default=0)
    favorite_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=now, onupdate=now)


# ---------- 发布中心 ----------
PUBLISH_PLATFORMS = ["fanqie", "qidian", "qimao", "feilu", "zongheng", "jjwxc"]
PLATFORM_LABELS = {
    "fanqie": "番茄小说",
    "qidian": "起点中文网",
    "qimao": "七猫中文网",
    "feilu": "飞卢小说网",
    "zongheng": "纵横中文网",
    "jjwxc": "晋江文学城",
}
PLATFORM_ACCOUNT_STATUSES = ["not_configured", "logged_in", "expired"]
PUBLISH_TASK_STATUSES = ["queued", "scheduled", "need_manual", "success", "failed"]


class NovelPlatform(Base):
    """书 × 发布平台 标记,记录该平台已发到第几章。"""

    __tablename__ = "novel_platforms"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    published_chapter_no = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("novel_id", "platform", name="uq_novel_platform"),)


class PlatformAccount(Base):
    """平台账号登录态(M3 自动化;当前手动标记状态)。"""

    __tablename__ = "platform_accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    platform = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="not_configured")  # not_configured/logged_in/expired
    note = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=now, onupdate=now)

    __table_args__ = (UniqueConstraint("user_id", "platform", name="uq_account_user_platform"),)


class PublishTask(Base):
    """发布任务:章节 × 平台,半自动模式由人工标记结果。"""

    __tablename__ = "publish_tasks"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="queued", index=True)
    scheduled_at = Column(DateTime, nullable=True)
    platform_chapter_id = Column(String(128), nullable=True)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


# ---------- M2:知识库 / Skills ----------
SKILL_STAGES = ["outline", "chapter", "polish", "analysis", "review", "script"]
SKILL_STAGE_LABELS = {
    "outline": "大纲生成",
    "chapter": "章节正文",
    "polish": "润色",
    "analysis": "复盘分析",
    "review": "审核审查",
    "script": "漫剧分镜",
}


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(String(500), nullable=True)
    scope_tags = Column(JSON, nullable=False, default=list)  # ["novel","media","drama","shared"]
    created_at = Column(DateTime, default=now)


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id = Column(Integer, primary_key=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="ready")  # ready / failed
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=now)


class KbChunk(Base):
    """向量以 JSON 存储(可移植);Postgres 部署时可平滑迁移 pgvector。"""

    __tablename__ = "kb_chunks"

    id = Column(Integer, primary_key=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    idx = Column(Integer, nullable=False, default=0)
    content = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=True)


class NovelKbLink(Base):
    __tablename__ = "novel_kb_links"

    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), primary_key=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)


class Skill(Base):
    """写作技能包:可为纯提示词,或文件夹包(pack_dir 指向 data/skills 下目录)。"""

    __tablename__ = "skills"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    version = Column(String(16), nullable=False, default="1.0.0")
    stage = Column(String(32), nullable=False, default="chapter")
    system_prompt = Column(Text, nullable=False)
    params = Column(JSON, nullable=False, default=dict)
    builtin = Column(Boolean, nullable=False, default=False)
    scope_tags = Column(JSON, nullable=False, default=list)  # ["novel","media","shared"]
    # 相对 skills_root 的包路径,如 "3/12";有值时对话注入整包文件
    pack_dir = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class ResourceMount(Base):
    """通用资源挂载:Skills/KB → 任意产品工作区。"""

    __tablename__ = "resource_mounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_key = Column(String(32), nullable=False, index=True)
    workspace_type = Column(String(32), nullable=False)  # novel / media_account
    workspace_id = Column(Integer, nullable=False, index=True)
    resource_type = Column(String(16), nullable=False)  # skill / kb
    resource_id = Column(Integer, nullable=False)
    stage = Column(String(32), nullable=False, default="")  # skill 阶段;kb 用空串

    __table_args__ = (
        UniqueConstraint(
            "product_key",
            "workspace_type",
            "workspace_id",
            "resource_type",
            "resource_id",
            "stage",
            name="uq_resource_mount",
        ),
    )


class NovelSkillLink(Base):
    """书 × 阶段 挂载的技能。"""

    __tablename__ = "novel_skill_links"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(32), nullable=False)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("novel_id", "stage", name="uq_novel_skill_stage"),)


# ---------- M3:复盘 / M4:协作 ----------
class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    model_name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=now)


class NovelCollaborator(Base):
    __tablename__ = "novel_collaborators"

    id = Column(Integer, primary_key=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(16), nullable=False, default="editor")  # editor / reviewer

    __table_args__ = (UniqueConstraint("novel_id", "user_id", name="uq_collab_novel_user"),)


# ---------- 自由对话(首页聊天 / 一书一对话) ----------
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # 绑定小说时:同一用户同一部书只有一个对话 id;首页自由聊则为空
    # 唯一性由 schema_patch 的部分索引保证(允许多个 novel_id IS NULL 的自由聊)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=True, index=True)
    product_key = Column(String(32), nullable=False, default="novel", index=True)
    workspace_id = Column(Integer, nullable=True, index=True)  # media_account_id 等
    title = Column(String(64), nullable=False, default="新对话")
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="SET NULL"), nullable=True)
    # 超长历史自动压缩后的滚动记忆(替代被压缩掉的旧消息)
    compressed_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


# ---------- 自媒体产品 ----------
MEDIA_PLATFORMS = ["xhs", "wechat_mp", "douyin", "toutiao", "other"]
MEDIA_PLATFORM_LABELS = {
    "xhs": "小红书",
    "wechat_mp": "公众号",
    "douyin": "抖音",
    "toutiao": "头条",
    "other": "其他",
}
MEDIA_PUBLISH_STATUSES = ["queued", "publishing", "success", "failed", "need_manual"]


class MediaAccount(Base):
    __tablename__ = "media_accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    platform = Column(String(32), nullable=False, default="other")
    persona = Column(Text, nullable=True)  # 人设/口吻
    audience = Column(String(255), nullable=True)
    forbidden = Column(Text, nullable=True)
    # 一键发布凭证(加密存储)
    app_id = Column(String(128), nullable=True)
    app_secret_encrypted = Column(String(1000), nullable=True)
    access_token_encrypted = Column(String(2000), nullable=True)
    refresh_token_encrypted = Column(String(2000), nullable=True)
    # 自定义发布网关(OpenAPI/自建桥接),为空时走内置适配器
    publish_api_base = Column(String(500), nullable=True)
    token_note = Column(String(255), nullable=True)
    token_status = Column(String(16), nullable=False, default="not_configured")  # not_configured/ready/expired
    token_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class MediaPublishTask(Base):
    """自媒体成稿一键发布任务。"""

    __tablename__ = "media_publish_tasks"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("media_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("media_articles.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="queued", index=True)
    platform_post_id = Column(String(128), nullable=True)
    result_url = Column(String(500), nullable=True)
    error = Column(String(800), nullable=True)
    raw_response = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class TopicItem(Base):
    __tablename__ = "topic_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("media_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=True)
    source = Column(String(32), nullable=False, default="manual")  # manual/rss/import/ai
    source_url = Column(String(500), nullable=True)
    platform_hint = Column(String(32), nullable=True)
    score = Column(Float, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="active")  # active/used/ignored
    created_at = Column(DateTime, default=now)


class MediaArticle(Base):
    __tablename__ = "media_articles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("media_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("topic_items.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False, default="")
    tags = Column(JSON, nullable=False, default=list)
    status = Column(String(16), nullable=False, default="draft")  # draft/ready/archived
    word_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class TopicRssSource(Base):
    __tablename__ = "topic_rss_sources"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    url = Column(String(500), nullable=False)
    platform_hint = Column(String(32), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    last_fetched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)


class DramaProject(Base):
    """漫剧项目:小说转漫剧 / 上传剧本 → 圣经 + 分镜首帧。"""

    __tablename__ = "drama_projects"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(128), nullable=False, default="未命名漫剧")
    source_type = Column(String(16), nullable=False, default="paste")  # novel / upload / paste
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="SET NULL"), nullable=True, index=True)
    source_text = Column(Text, nullable=True)
    style_prompt = Column(Text, nullable=True)
    episode_count = Column(Integer, nullable=False, default=5)
    shots_per_episode = Column(Integer, nullable=False, default=3)
    bible_json = Column(JSON, nullable=False, default=dict)
    status = Column(String(16), nullable=False, default="draft")  # draft / scripted / generating / ready
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    shots = relationship(
        "DramaShot",
        backref="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DramaShot.sort",
    )


class DramaShot(Base):
    __tablename__ = "drama_shots"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("drama_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_index = Column(Integer, nullable=False, default=1)
    shot_index = Column(Integer, nullable=False, default=1)
    sort = Column(Integer, nullable=False, default=0)
    caption = Column(String(255), nullable=True)
    start_sec = Column(Integer, nullable=False, default=0)
    end_sec = Column(Integer, nullable=False, default=5)
    shot_size = Column(String(16), nullable=True)  # cu/ms/fs/ls...
    camera_move = Column(String(16), nullable=True)  # static/push/pan...
    angle = Column(String(16), nullable=True)  # eye/high/low/over
    speaker = Column(String(64), nullable=True)  # 说话人
    visual = Column(Text, nullable=True)
    line = Column(Text, nullable=True)
    image_prompt = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)  # 当前勾选的主图
    candidate_urls = Column(JSON, nullable=False, default=list)  # 最多 3 张候选首帧
    selected_index = Column(Integer, nullable=False, default=0)
    refine_note = Column(Text, nullable=True)  # 用户对勾选图的改图描述
    status = Column(String(16), nullable=False, default="pending")  # pending / ready / failed
    error = Column(String(500), nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user / assistant / system
    content = Column(Text, nullable=False)
    model_name = Column(String(128), nullable=True)
    # 可选:本轮对话关联的章节(跨章节仍属同一 session)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now)
