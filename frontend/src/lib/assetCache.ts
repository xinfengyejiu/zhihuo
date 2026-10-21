"use client";

import { getApiBase, getToken } from "@/lib/api";

/** 内存级封面/资源缓存:同一 /gen-assets/ 路径只鉴权拉取一次。 */
const blobByUrl = new Map<string, string>();
const inflight = new Map<string, Promise<string>>();

export function resolveAssetUrl(url: string): string {
  if (!url) return "";
  if (
    url.startsWith("http://") ||
    url.startsWith("https://") ||
    url.startsWith("data:") ||
    url.startsWith("blob:")
  ) {
    return url;
  }
  const base = getApiBase().replace(/\/api\/?$/, "");
  if (url.startsWith("/api/")) return `${base}${url}`;
  if (url.startsWith("/gen-assets/")) return `${base}/api${url}`;
  return url.startsWith("/") ? `${base}${url}` : url;
}

function needsAuthFetch(url: string, resolved: string): boolean {
  return url.includes("/gen-assets/") || resolved.includes("/gen-assets/");
}

/** 带鉴权拉取 gen-assets,命中内存缓存则秒回 blob URL。 */
export function loadAuthedAsset(url: string): Promise<string> {
  if (!url) return Promise.resolve("");
  const resolved = resolveAssetUrl(url);
  if (!needsAuthFetch(url, resolved)) {
    return Promise.resolve(resolved);
  }
  const cached = blobByUrl.get(resolved);
  if (cached) return Promise.resolve(cached);
  const pending = inflight.get(resolved);
  if (pending) return pending;

  const token = getToken();
  const job = fetch(resolved, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    // 配合后端 Cache-Control,走浏览器 HTTP 缓存
    cache: "force-cache",
  })
    .then(async (resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const obj = URL.createObjectURL(blob);
      blobByUrl.set(resolved, obj);
      return obj;
    })
    .catch(() => {
      // 回退直链(公共封面 / 已缓存响应)
      return resolved;
    })
    .finally(() => {
      inflight.delete(resolved);
    });

  inflight.set(resolved, job);
  return job;
}

/** 主动丢弃某资源缓存(换封面后调用)。 */
export function invalidateAssetCache(url: string | null | undefined) {
  if (!url) return;
  const resolved = resolveAssetUrl(url);
  const blob = blobByUrl.get(resolved);
  if (blob) {
    URL.revokeObjectURL(blob);
    blobByUrl.delete(resolved);
  }
  inflight.delete(resolved);
}
