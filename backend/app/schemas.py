from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    email: Optional[str] = None
    nickname: Optional[str] = None


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


# ---------- Profile ----------
class UserOut(ORMModel):
    id: int
    username: str
    email: Optional[str] = None
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None


class ProfileUpdateIn(BaseModel):
    nickname: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


# ---------- AI Providers ----------
class ProviderIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    enabled: bool = True
    remark: Optional[str] = None


class ProviderUpdateIn(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    remark: Optional[str] = None


class ProviderOut(ORMModel):
    id: int
    name: str
    base_url: str
    api_key_masked: str
    enabled: bool
    remark: Optional[str] = None


class AiModelOut(ORMModel):
    id: int
    model_id: str
    display_name: Optional[str] = None
    enabled: bool
    modality: Optional[str] = None  # text / image / video


class ModelToggleIn(BaseModel):
    model_id: str
    display_name: Optional[str] = None
    enabled: bool = True
    modality: Optional[str] = None  # text / image / video / null=自动


class RouteIn(BaseModel):
    provider_id: Optional[int] = None
    model_id: Optional[str] = None
    temperature: float = 0.8
    max_tokens: int = 4096
    fallback_provider_id: Optional[int] = None
    fallback_model_id: Optional[str] = None


class RouteOut(RouteIn):
    purpose: str


# ---------- Tags ----------
class TagIn(BaseModel):
    name: str = Field(max_length=32)
    type: str = "custom"
    color: Optional[str] = None


class TagUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=32)
    type: Optional[str] = None
    color: Optional[str] = None


class TagOut(ORMModel):
    id: int
    name: str
    type: str
    color: Optional[str] = None


# ---------- Novels ----------
class NovelIn(BaseModel):
    title: str = Field(max_length=128)
    summary: Optional[str] = None
    cover: Optional[str] = None
    status: str = "draft"
    target_words: Optional[int] = None
    tag_ids: list[int] = []
    platforms: list[str] = []


class NovelUpdateIn(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    cover: Optional[str] = None
    visual_style_prompt: Optional[str] = None
    status: Optional[str] = None
    target_words: Optional[int] = None
    tag_ids: Optional[list[int]] = None
    platforms: Optional[list[str]] = None


class CoverGenerateIn(BaseModel):
    """按书本关键词/简介生成封面。keywords 留空则用标签+世界观条目。"""
    keywords: Optional[str] = None
    extra: Optional[str] = None


class NovelPlatformOut(ORMModel):
    platform: str
    enabled: bool
    published_chapter_no: int


class NovelOut(ORMModel):
    id: int
    title: str
    summary: Optional[str]
    cover: Optional[str]
    visual_style_prompt: Optional[str] = None
    status: str
    target_words: Optional[int]
    current_words: int
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut] = []
    platforms: list[NovelPlatformOut] = []
    my_role: str = "owner"


# ---------- World / Characters / Rules ----------
class WorldEntryIn(BaseModel):
    category: str = "other"
    title: str
    content: Optional[str] = None
    sort: int = 0


class WorldEntryOut(ORMModel):
    id: int
    category: str
    title: str
    content: Optional[str]
    sort: int


class CharacterIn(BaseModel):
    name: str
    aliases: Optional[str] = None
    role: str = "support"
    occupation: Optional[str] = None
    organization: Optional[str] = None
    appearance: Optional[str] = None
    visual_prompt: Optional[str] = None
    reference_image_urls: list[str] = Field(default_factory=list)
    personality: Optional[str] = None
    goal: Optional[str] = None
    notes: Optional[str] = None


class CharacterOut(ORMModel):
    id: int
    name: str
    aliases: Optional[str]
    role: str
    occupation: Optional[str]
    organization: Optional[str]
    appearance: Optional[str]
    visual_prompt: Optional[str] = None
    reference_image_urls: list = []
    personality: Optional[str]
    goal: Optional[str]
    notes: Optional[str]

    @field_validator("reference_image_urls", mode="before")
    @classmethod
    def _coerce_refs(cls, v):
        return v or []


class NovelSceneIn(BaseModel):
    title: str
    description: Optional[str] = None
    atmosphere: Optional[str] = None
    reference_image_urls: list[str] = Field(default_factory=list)
    sort: int = 0


class NovelSceneOut(ORMModel):
    id: int
    title: str
    description: Optional[str]
    atmosphere: Optional[str]
    reference_image_urls: list = []
    sort: int

    @field_validator("reference_image_urls", mode="before")
    @classmethod
    def _coerce_scene_refs(cls, v):
        return v or []


class StoryboardFrameOut(ORMModel):
    id: int
    sort: int
    caption: Optional[str]
    shot_prompt: str
    image_url: Optional[str]
    character_ids: list = []
    scene_id: Optional[int]
    status: str
    error: Optional[str]


class StoryboardOut(ORMModel):
    id: int
    chapter_id: int
    status: str
    frame_count: int
    style_snapshot: Optional[str]
    video_url: Optional[str]
    video_prompt: Optional[str]
    error: Optional[str]
    frames: list[StoryboardFrameOut] = []
    updated_at: Optional[datetime] = None


class StoryboardPlanIn(BaseModel):
    frame_count: int = Field(default=4, ge=1, le=12)


class StoryboardGenerateIn(BaseModel):
    frame_ids: Optional[list[int]] = None  # 空=全部未完成/全部重跑指定
    regenerate_all: bool = False
    skill_id: Optional[int] = None  # 可选:注入技能包指导本批/单镜生图


class StoryboardVideoIn(BaseModel):
    prompt: Optional[str] = None  # 空则用分镜摘要拼


class RelationshipIn(BaseModel):
    from_character_id: int
    to_character_id: int
    type: str = "other"
    intimacy: int = 0
    note: Optional[str] = None


class RelationshipOut(ORMModel):
    id: int
    from_character_id: int
    to_character_id: int
    type: str
    intimacy: int
    note: Optional[str]


class RuleIn(BaseModel):
    content: str
    priority: int = 0
    enabled: bool = True


class RuleOut(ORMModel):
    id: int
    content: str
    priority: int
    enabled: bool


# ---------- Volumes & Chapters ----------
class VolumeIn(BaseModel):
    title: str
    outline: Optional[str] = None
    sort: int = 0


class VolumeOut(ORMModel):
    id: int
    title: str
    outline: Optional[str]
    sort: int


class ChapterIn(BaseModel):
    title: str
    volume_id: Optional[int] = None
    outline: Optional[str] = None
    characters_involved: Optional[list[int]] = None
    foreshadow_note: Optional[str] = None


class ChapterUpdateIn(BaseModel):
    title: Optional[str] = None
    volume_id: Optional[int] = None
    sort: Optional[int] = None
    outline: Optional[str] = None
    characters_involved: Optional[list[int]] = None
    foreshadow_note: Optional[str] = None


class VersionOut(ORMModel):
    id: int
    kind: str
    word_count: int
    model_name: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    created_at: datetime


class ChapterOut(ORMModel):
    id: int
    novel_id: int
    volume_id: Optional[int]
    title: str
    sort: int
    status: str
    word_count: int
    outline: Optional[str]
    characters_involved: Optional[list]
    foreshadow_note: Optional[str]
    current_version_id: Optional[int]
    updated_at: datetime
    versions: list[VersionOut] = []
    # 被驳回时带上最近一次驳回意见,供作者「按驳回改」
    last_reject_comment: Optional[str] = None


class ChapterContentIn(BaseModel):
    content: str


class AdoptIn(BaseModel):
    version_id: int


class ReviewActionIn(BaseModel):
    comment: Optional[str] = None


# ---------- Reader ----------
class ReaderCatalogItem(ORMModel):
    id: int
    title: str
    status: str
    word_count: int
    sort: int
    volume_id: Optional[int] = None


class ReaderProgressIn(BaseModel):
    chapter_id: int


# ---------- Generate ----------
class GenerateIn(BaseModel):
    mode: str = "chapter"  # chapter / continue / polish / rewrite
    instruction: Optional[str] = None


# ---------- Usage ----------
class UsagePoint(BaseModel):
    date: str
    purpose: str
    model_name: Optional[str] = None
    prompt_tokens: int
    completion_tokens: int


# ---------- Publish ----------
class AccountStatusIn(BaseModel):
    status: str
    note: Optional[str] = None


class PlatformAccountOut(ORMModel):
    platform: str
    status: str
    note: Optional[str]
    updated_at: datetime


class PublishTaskOut(ORMModel):
    id: int
    novel_id: int
    chapter_id: int
    chapter_title: str = ""
    platform: str
    status: str
    scheduled_at: Optional[datetime] = None
    platform_chapter_id: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime


class PublishChapterTask(ORMModel):
    platform: str
    status: str


class PublishChapterOut(ORMModel):
    id: int
    title: str
    status: str
    word_count: int
    sort: int
    tasks: list[PublishChapterTask] = []


class PublishCreateIn(BaseModel):
    chapter_ids: list[int]
    platforms: list[str]
    scheduled_at: Optional[datetime] = None


class MarkSuccessIn(BaseModel):
    platform_chapter_id: Optional[str] = None


# ---------- Knowledge Base ----------
class KbCreateIn(BaseModel):
    name: str = Field(max_length=128)
    description: Optional[str] = None
    scope_tags: list[str] = Field(default_factory=lambda: ["shared"])


class KbOut(ORMModel):
    id: int
    name: str
    description: Optional[str]
    scope_tags: list = []
    document_count: int = 0
    chunk_count: int = 0


class KbDocumentOut(ORMModel):
    id: int
    filename: str
    size: int
    chunk_count: int
    status: str
    error: Optional[str]
    created_at: datetime


class KbSearchIn(BaseModel):
    query: str
    top_k: int = 6


class KbSearchHit(BaseModel):
    content: str
    document_id: int
    kb_id: int
    score: float


class KbMountIn(BaseModel):
    kb_ids: list[int]


# ---------- Skills ----------
class SkillIn(BaseModel):
    name: str = Field(max_length=64)
    version: str = "1.0.0"
    stage: str = "chapter"
    system_prompt: str
    params: dict = {}
    scope_tags: list[str] = Field(default_factory=lambda: ["shared"])


class SkillOut(ORMModel):
    id: int
    name: str
    version: str
    stage: str
    system_prompt: str
    params: dict
    builtin: bool
    scope_tags: list = []
    pack_dir: Optional[str] = None
    file_count: int = 0
    files: list[dict] = []


class SkillImportIn(BaseModel):
    name: Optional[str] = None
    stage: str = "chapter"
    markdown: str
    scope_tags: list[str] = Field(default_factory=lambda: ["shared"])


class SkillGithubImportIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    stage: str = "chapter"
    name: Optional[str] = None  # 仅单文件时覆盖名称
    scope_tags: list[str] = Field(default_factory=lambda: ["shared"])


class SkillMountIn(BaseModel):
    stage: str
    skill_id: Optional[int] = None


# ---------- Insights(伏笔 / 复盘) ----------
class ForeshadowIn(BaseModel):
    description: str
    plant_chapter_id: Optional[int] = None
    expect_recall_chapter_id: Optional[int] = None
    status: str = "planted"


class ForeshadowOut(ORMModel):
    id: int
    description: str
    plant_chapter_id: Optional[int]
    expect_recall_chapter_id: Optional[int]
    status: str


class ReportOut(ORMModel):
    id: int
    chapter_id: int
    content: str
    model_name: Optional[str]
    created_at: datetime


class SelfCheckIn(BaseModel):
    """可选:直接传正文(保存前预检);不传则用库里当前正文。"""
    content: Optional[str] = None


class SelfCheckIssueOut(BaseModel):
    kind: str
    title: str
    detail: str
    advice: str
    excerpt: Optional[str] = None
    count: Optional[int] = None
    hits: list[str] = []


class SelfCheckOut(BaseModel):
    score: int
    word_count: int
    para_count: int
    avg_para_chars: int
    issue_count: int
    issues: list[SelfCheckIssueOut]
    summary: str


class SelfCheckFixIssueIn(BaseModel):
    """前端勾选并可能改写过建议的自检项。"""
    kind: str
    title: str
    detail: str = ""
    advice: str = ""
    excerpt: Optional[str] = None
    hits: list[str] = []


class SelfCheckFixIn(BaseModel):
    """按自检建议定点修改正文。"""
    content: Optional[str] = None
    issues: list[SelfCheckFixIssueIn]


# ---------- Collaboration ----------
class CollabAddIn(BaseModel):
    username: str
    role: str = "editor"


class CollabUpdateIn(BaseModel):
    role: str


class CollabOut(ORMModel):
    id: int
    user_id: int
    username: str
    nickname: Optional[str] = None
    role: str


# ---------- Reader store (独立读者端) ----------
class ReaderRegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    nickname: Optional[str] = None


class ReaderLoginIn(BaseModel):
    username: str
    password: str


class ReaderUserOut(ORMModel):
    id: int
    username: str
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None


class ReaderTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: ReaderUserOut


class ReaderProgressIn(BaseModel):
    chapter_id: int
    position_pct: Optional[int] = 0


class ReaderLocalProgressItem(BaseModel):
    novel_id: int
    chapter_id: int
    position_pct: int = 0


class ReaderSyncLocalIn(BaseModel):
    progress: list[ReaderLocalProgressItem] = []
    favorite_novel_ids: list[int] = []
    like_novel_ids: list[int] = []


class ReaderNovelCardOut(BaseModel):
    id: int
    title: str
    summary: Optional[str] = None
    cover: Optional[str] = None
    tags: list[str] = []
    word_count: int = 0
    chapter_count: int = 0
    read_count: int = 0
    like_count: int = 0
    favorite_count: int = 0
    updated_at: Optional[datetime] = None
    progress_chapter_id: Optional[int] = None
    progress_chapter_title: Optional[str] = None
    favorited: bool = False
    liked: bool = False


class ReaderNovelDetailOut(ReaderNovelCardOut):
    pass


class ReaderBookshelfItemOut(ReaderNovelCardOut):
    visited_at: Optional[datetime] = None
    progress_updated_at: Optional[datetime] = None


TokenOut.model_rebuild()
