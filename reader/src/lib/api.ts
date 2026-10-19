"use client";

/** 未配置 NEXT_PUBLIC_API_BASE 时,跟随当前页面主机名连同机 8011 端口。 */
export function getApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) return process.env.NEXT_PUBLIC_API_BASE;
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8011/api`;
  }
  return "http://localhost:8011/api";
}

/** 创作台(工作台)地址,默认同机 3000。 */
export function getStudioBase(): string {
  if (process.env.NEXT_PUBLIC_STUDIO_BASE) return process.env.NEXT_PUBLIC_STUDIO_BASE.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:3000`;
  }
  return "http://localhost:3000";
}

const TOKEN_KEY = "xiaohuo_reader_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}, opts?: { auth?: boolean }): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(`${getApiBase()}${path}`, { ...options, headers });
  if (resp.status === 401 && opts?.auth) {
    setToken(null);
  }
  if (!resp.ok) {
    let detail = `请求失败(HTTP ${resp.status})`;
    try {
      const data = await resp.json();
      if (data.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  publicGet: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/** 游客本地进度/收藏 */
const LOCAL_PROGRESS_KEY = "xiaohuo_reader_local_progress";
const LOCAL_FAV_KEY = "xiaohuo_reader_local_fav";
const LOCAL_LIKE_KEY = "xiaohuo_reader_local_like";

export type LocalProgress = { novel_id: number; chapter_id: number; position_pct: number };

export function getLocalProgress(): LocalProgress[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(LOCAL_PROGRESS_KEY) || "[]");
  } catch {
    return [];
  }
}

export function upsertLocalProgress(item: LocalProgress) {
  const list = getLocalProgress().filter((x) => x.novel_id !== item.novel_id);
  list.unshift(item);
  localStorage.setItem(LOCAL_PROGRESS_KEY, JSON.stringify(list.slice(0, 50)));
}

export function getLocalFavIds(): number[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(LOCAL_FAV_KEY) || "[]");
  } catch {
    return [];
  }
}

export function toggleLocalFav(novelId: number): boolean {
  const set = new Set(getLocalFavIds());
  if (set.has(novelId)) set.delete(novelId);
  else set.add(novelId);
  localStorage.setItem(LOCAL_FAV_KEY, JSON.stringify(Array.from(set)));
  return set.has(novelId);
}

export function getLocalLikeIds(): number[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(LOCAL_LIKE_KEY) || "[]");
  } catch {
    return [];
  }
}

export function toggleLocalLike(novelId: number): boolean {
  const set = new Set(getLocalLikeIds());
  if (set.has(novelId)) set.delete(novelId);
  else set.add(novelId);
  localStorage.setItem(LOCAL_LIKE_KEY, JSON.stringify(Array.from(set)));
  return set.has(novelId);
}

export async function syncLocalToServer() {
  const progress = getLocalProgress();
  const favorite_novel_ids = getLocalFavIds();
  const like_novel_ids = getLocalLikeIds();
  if (!progress.length && !favorite_novel_ids.length && !like_novel_ids.length) return;
  await api.post("/reader/me/sync-local", { progress, favorite_novel_ids, like_novel_ids });
  localStorage.removeItem(LOCAL_PROGRESS_KEY);
  localStorage.removeItem(LOCAL_FAV_KEY);
  localStorage.removeItem(LOCAL_LIKE_KEY);
}
