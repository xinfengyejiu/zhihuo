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

export function CoverImg({
  url,
  title,
  className = "h-full w-full object-cover",
}: {
  url: string | null;
  title: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const src = url && !failed ? resolveUrl(url) : "";

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
      onError={() => setFailed(true)}
    />
  );
}
