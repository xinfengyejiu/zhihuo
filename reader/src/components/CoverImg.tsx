"use client";

import { useState } from "react";
import { getApiBase } from "@/lib/api";

function resolveUrl(url: string): string {
  if (!url) return "";
  if (url.startsWith("http") || url.startsWith("data:") || url.startsWith("blob:")) return url;
  const base = getApiBase().replace(/\/api\/?$/, "");
  if (url.startsWith("/gen-assets/")) return `${base}/api${url}`;
  if (url.startsWith("/")) return `${base}${url}`;
  return url;
}

/** 浏览器会按 Cache-Control 缓存;再加一层内存标记,避免同页反复 onError 抖动。 */
const failedUrls = new Set<string>();

export function CoverImg({
  url,
  title,
  className = "h-full w-full object-cover",
}: {
  url: string | null;
  title: string;
  className?: string;
}) {
  const resolved = url ? resolveUrl(url) : "";
  const [failed, setFailed] = useState(() => (resolved ? failedUrls.has(resolved) : false));
  const src = resolved && !failed ? resolved : "";

  if (!src) {
    return (
      <div className={`flex items-center justify-center bg-gradient-to-br from-amber-100 to-orange-200 text-2xl ${className}`}>
        📖
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={title}
      className={className}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => {
        failedUrls.add(src);
        setFailed(true);
      }}
    />
  );
}
