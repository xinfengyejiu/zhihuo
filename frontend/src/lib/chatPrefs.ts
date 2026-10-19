/** 对话页技能/模型等选择持久化,切换路由不丢失。 */

export type MediaChatPrefs = {
  accountId: number | null;
  skillId: number | null;
  providerId: number | null;
  modelId: string | null;
};

export type NovelChatPrefs = {
  skillId: number | null;
};

const MEDIA_KEY = "xiaohuo_media_chat_prefs";
const MEDIA_ACCOUNT_KEY = "xiaohuo_media_account_chat_prefs";
const NOVEL_KEY = "xiaohuo_novel_chat_prefs";

function readJson<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore quota */
  }
}

export function loadMediaChatPrefs(): MediaChatPrefs {
  const p = readJson<Partial<MediaChatPrefs>>(MEDIA_KEY);
  return {
    accountId: typeof p?.accountId === "number" ? p.accountId : null,
    skillId: typeof p?.skillId === "number" ? p.skillId : null,
    providerId: typeof p?.providerId === "number" ? p.providerId : null,
    modelId: typeof p?.modelId === "string" ? p.modelId : null,
  };
}

export function saveMediaChatPrefs(patch: Partial<MediaChatPrefs>) {
  writeJson(MEDIA_KEY, { ...loadMediaChatPrefs(), ...patch });
}

export function loadMediaAccountChatPrefs(accountId: number): Omit<MediaChatPrefs, "accountId"> {
  const all = readJson<Record<string, Partial<MediaChatPrefs>>>(MEDIA_ACCOUNT_KEY) || {};
  const p = all[String(accountId)] || {};
  return {
    skillId: typeof p.skillId === "number" ? p.skillId : null,
    providerId: typeof p.providerId === "number" ? p.providerId : null,
    modelId: typeof p.modelId === "string" ? p.modelId : null,
  };
}

export function saveMediaAccountChatPrefs(accountId: number, patch: Partial<Omit<MediaChatPrefs, "accountId">>) {
  const all = readJson<Record<string, Partial<MediaChatPrefs>>>(MEDIA_ACCOUNT_KEY) || {};
  all[String(accountId)] = { ...loadMediaAccountChatPrefs(accountId), ...patch };
  writeJson(MEDIA_ACCOUNT_KEY, all);
}

export function loadNovelChatPrefs(): NovelChatPrefs {
  const p = readJson<Partial<NovelChatPrefs>>(NOVEL_KEY);
  return { skillId: typeof p?.skillId === "number" ? p.skillId : null };
}

export function saveNovelChatPrefs(patch: Partial<NovelChatPrefs>) {
  writeJson(NOVEL_KEY, { ...loadNovelChatPrefs(), ...patch });
}
