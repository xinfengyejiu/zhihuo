"use client";

/** 未配置 NEXT_PUBLIC_API_BASE 时,跟随当前页面主机名连同机 8011 端口(支持局域网 IP 访问)。 */
export function getApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) return process.env.NEXT_PUBLIC_API_BASE;
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8011/api`;
  }
  return "http://localhost:8011/api";
}

/** 独立阅读器服务,默认同机 3001。 */
export function getReaderBase(): string {
  if (process.env.NEXT_PUBLIC_READER_BASE) return process.env.NEXT_PUBLIC_READER_BASE.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:3001`;
  }
  return "http://localhost:3001";
}

/** 打开某书/某章的阅读器 URL(独立服务)。 */
export function readerNovelUrl(novelId: string | number, chapterId?: string | number): string {
  const base = getReaderBase();
  return chapterId != null && chapterId !== "" ? `${base}/${novelId}/${chapterId}` : `${base}/${novelId}`;
}

/** @deprecated 请优先用 getApiBase(),以便局域网 IP 访问时自动对齐主机名 */
export const API_BASE = typeof window !== "undefined"
  ? getApiBase()
  : process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8011/api";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("xiaohuo_token");
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem("xiaohuo_token", token);
  else localStorage.removeItem("xiaohuo_token");
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}, opts?: { optionalAuth?: boolean }): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(`${getApiBase()}${path}`, { ...options, headers });
  if (resp.status === 401 && typeof window !== "undefined") {
    if (!opts?.optionalAuth) {
      setToken(null);
      window.location.href = "/login";
      throw new ApiError(401, "未登录");
    }
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
  /** 公开接口:带 token 更好(可记进度),没有也不跳登录 */
  publicGet: <T>(path: string) => request<T>(path, {}, { optionalAuth: true }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** multipart 上传(不要手动设 Content-Type,交给浏览器带 boundary) */
  upload: async <T>(path: string, form: FormData): Promise<T> => {
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const resp = await fetch(`${getApiBase()}${path}`, { method: "POST", headers, body: form });
    if (resp.status === 401 && typeof window !== "undefined") {
      setToken(null);
      window.location.href = "/login";
      throw new ApiError(401, "未登录");
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
  },
};

/** POST + SSE 流式读取,逐事件回调。返回最终拼出的文本。 */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (event: Record<string, unknown>) => void
): Promise<void> {
  const token = getToken();
  const resp = await fetch(`${getApiBase()}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    let detail = `请求失败(HTTP ${resp.status})`;
    try {
      const data = await resp.json();
      if (data.detail) detail = data.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushLines = (chunk: string) => {
    buffer += chunk;
    const lines = buffer.split("\n\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      try {
        onEvent(JSON.parse(trimmed.slice(5).trim()));
      } catch {
        /* ignore bad json */
      }
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (value) flushLines(decoder.decode(value, { stream: !done }));
    if (done) {
      flushLines(decoder.decode());
      if (buffer.trim().startsWith("data:")) {
        try {
          onEvent(JSON.parse(buffer.trim().slice(5).trim()));
        } catch {
          /* ignore */
        }
        buffer = "";
      }
      break;
    }
  }
}
