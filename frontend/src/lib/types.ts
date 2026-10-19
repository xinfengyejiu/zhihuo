export interface Tag {
  id: number;
  name: string;
  type: string;
  color: string | null;
}

export interface NovelPlatformInfo {
  platform: string;
  enabled: boolean;
  published_chapter_no: number;
}

export const PLATFORMS: { key: string; label: string }[] = [
  { key: "fanqie", label: "番茄小说" },
  { key: "qidian", label: "起点中文网" },
  { key: "qimao", label: "七猫中文网" },
  { key: "feilu", label: "飞卢小说网" },
  { key: "zongheng", label: "纵横中文网" },
  { key: "jjwxc", label: "晋江文学城" },
];

export const PLATFORM_LABEL: Record<string, string> = Object.fromEntries(
  PLATFORMS.map((p) => [p.key, p.label])
);

export interface Novel {
  id: number;
  title: string;
  summary: string | null;
  cover: string | null;
  visual_style_prompt?: string | null;
  status: string;
  target_words: number | null;
  current_words: number;
  created_at: string;
  updated_at: string;
  tags: Tag[];
  platforms: NovelPlatformInfo[];
  my_role?: string;
}

export interface Version {
  id: number;
  kind: string;
  word_count: number;
  model_name: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  created_at: string;
}

export interface Chapter {
  id: number;
  novel_id: number;
  volume_id: number | null;
  title: string;
  sort: number;
  status: string;
  word_count: number;
  outline: string | null;
  characters_involved: number[] | null;
  foreshadow_note: string | null;
  current_version_id: number | null;
  updated_at: string;
  versions: Version[];
  /** 最近一次驳回意见(未再通过审核时) */
  last_reject_comment?: string | null;
}

export interface Volume {
  id: number;
  title: string;
  outline: string | null;
  sort: number;
}

export interface Character {
  id: number;
  name: string;
  aliases: string | null;
  role: string;
  occupation: string | null;
  organization: string | null;
  appearance: string | null;
  visual_prompt?: string | null;
  reference_image_urls?: string[];
  personality: string | null;
  goal: string | null;
  notes: string | null;
}

export interface NovelScene {
  id: number;
  title: string;
  description: string | null;
  atmosphere: string | null;
  reference_image_urls: string[];
  sort: number;
}

export interface StoryboardFrame {
  id: number;
  sort: number;
  caption: string | null;
  shot_prompt: string;
  image_url: string | null;
  character_ids: number[];
  scene_id: number | null;
  status: string;
  error: string | null;
}

export interface Storyboard {
  id: number;
  chapter_id: number;
  status: string;
  frame_count: number;
  style_snapshot: string | null;
  video_url: string | null;
  video_prompt: string | null;
  error: string | null;
  frames: StoryboardFrame[];
  updated_at?: string | null;
}

export interface Relationship {
  id: number;
  from_character_id: number;
  to_character_id: number;
  type: string;
  intimacy: number;
  note: string | null;
}

export interface WorldEntry {
  id: number;
  category: string;
  title: string;
  content: string | null;
  sort: number;
}

export interface Rule {
  id: number;
  content: string;
  priority: number;
  enabled: boolean;
}

export interface Provider {
  id: number;
  name: string;
  base_url: string;
  api_key_masked: string;
  enabled: boolean;
  remark: string | null;
}

export interface RouteConfig {
  purpose: string;
  provider_id: number | null;
  model_id: string | null;
  temperature: number;
  max_tokens: number;
  fallback_provider_id: number | null;
  fallback_model_id: string | null;
}

/* ---------- 发布中心 ---------- */
export interface PlatformAccountInfo {
  platform: string;
  status: string;
  note: string | null;
  updated_at: string;
}

export interface PublishTaskInfo {
  id: number;
  novel_id: number;
  chapter_id: number;
  chapter_title: string;
  platform: string;
  status: string;
  scheduled_at: string | null;
  platform_chapter_id: string | null;
  error: string | null;
  created_at: string;
}

export interface PublishChapterInfo {
  id: number;
  title: string;
  status: string;
  word_count: number;
  sort: number;
  tasks: { platform: string; status: string }[];
}

/* ---------- 知识库 / 技能 / 复盘 / 协作 ---------- */
export interface KbInfo {
  id: number;
  name: string;
  description: string | null;
  scope_tags?: string[];
  document_count: number;
  chunk_count: number;
}

export interface KbDocInfo {
  id: number;
  filename: string;
  size: number;
  chunk_count: number;
  status: string;
  error: string | null;
  created_at: string;
}

export interface SkillInfo {
  id: number;
  name: string;
  version: string;
  stage: string;
  system_prompt: string;
  params: Record<string, unknown>;
  builtin: boolean;
  scope_tags?: string[];
  pack_dir?: string | null;
  file_count?: number;
  files?: { path: string; size: number; text: boolean }[];
}

export interface ForeshadowInfo {
  id: number;
  description: string;
  plant_chapter_id: number | null;
  expect_recall_chapter_id: number | null;
  status: string;
}

export interface ReportInfo {
  id: number;
  chapter_id: number;
  content: string;
  model_name: string | null;
  created_at: string;
}

export interface CollabInfo {
  id: number;
  user_id: number;
  username: string;
  nickname: string | null;
  role: string;
}

export interface DramaShot {
  id: number;
  episode_index: number;
  shot_index: number;
  sort: number;
  caption: string | null;
  start_sec: number;
  end_sec: number;
  shot_size: string | null;
  camera_move: string | null;
  angle: string | null;
  speaker: string | null;
  visual: string | null;
  line: string | null;
  image_prompt: string | null;
  image_url: string | null;
  candidate_urls: string[];
  selected_index: number;
  refine_note: string | null;
  status: string;
  error: string | null;
}

export interface DramaBibleAsset {
  id?: string;
  kind?: string;
  name?: string;
  prompt?: string;
}

export interface DramaBibleEpisode {
  index: number;
  title: string;
  synopsis?: string;
  durationSec?: number;
  narrator?: string;
  lastFrame?: string;
  shots?: {
    startSec?: number;
    endSec?: number;
    visual?: string;
    speaker?: string;
    line?: string;
    imagePrompt?: string;
    shotSize?: string;
    cameraMove?: string;
    angle?: string;
  }[];
}

export interface DramaBible {
  title?: string;
  palette?: { note?: string; colors?: { name?: string; hex?: string; role?: string }[] };
  assets?: DramaBibleAsset[];
  episodes?: DramaBibleEpisode[];
}

export interface DramaProject {
  id: number;
  title: string;
  source_type: string;
  novel_id: number | null;
  source_text: string | null;
  style_prompt: string | null;
  episode_count: number;
  shots_per_episode: number;
  bible_json: DramaBible;
  status: string;
  error: string | null;
  shot_count: number;
  ready_count: number;
  updated_at?: string | null;
  shots: DramaShot[];
}
