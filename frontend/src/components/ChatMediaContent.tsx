"use client";

import { useEffect, useMemo, useState } from "react";
import { getApiBase, getToken } from "@/lib/api";
import { loadAuthedAsset } from "@/lib/assetCache";

type Segment =
  | { kind: "text"; text: string }
  | { kind: "image"; url: string; alt: string }
  | { kind: "video"; url: string; label: string };

const MD_IMAGE = /!\[([^\]]*)\]\(([^)]+)\)/g;
const MD_LINK = /🎥\s*\[([^\]]*)\]\(([^)]+)\)|\[([^\]]*视频[^\]]*)\]\(([^)]+)\)/g;
const BARE_MEDIA =
  /(https?:\/\/[^\s<>"']+\.(?:png|jpe?g|webp|gif|mp4|webm|mov)(?:\?[^\s<>"']*)?|\/gen-assets\/\d+\/[A-Za-z0-9._-]+)/gi;
const HTML_IMG = /<img[^>]+src=["']([^"']+)["'][^>]*>/gi;

function isVideoUrl(url: string, hint = ""): boolean {
  const u = url.toLowerCase();
  const h = hint.toLowerCase();
  if (h.includes("视频") || hint.includes("🎥")) return true;
  return /\.(mp4|webm|mov)(\?|$)/i.test(u);
}

export function isLoadableMediaUrl(url: string): boolean {
  const raw = (url || "").trim();
  if (!raw) return false;
  if (raw.startsWith("data:") || raw.startsWith("blob:")) return true;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return true;
  if (raw.includes("/gen-assets/")) return true;
  return false;
}

export function listImagePlaceholders(content: string): { alt: string; url: string }[] {
  const out: { alt: string; url: string }[] = [];
  for (const m of Array.from((content || "").matchAll(MD_IMAGE))) {
    const alt = m[1] || "配图";
    const url = (m[2] || "").trim();
    if (url && !isLoadableMediaUrl(url)) out.push({ alt, url });
  }
  return out;
}

function toApiUrl(url: string): string {
  const raw = url.trim();
  if (!raw) return raw;
  if (raw.startsWith("data:") || raw.startsWith("blob:")) return raw;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  const path = raw.startsWith("/") ? raw : `/${raw}`;
  const apiPath = path.startsWith("/api/") ? path.slice(4) : path;
  const base = getApiBase().replace(/\/$/, "");
  if (apiPath.startsWith("/gen-assets/")) {
    const token = getToken();
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";
    return `${base}${apiPath}${qs}`;
  }
  return raw;
}

function parseContent(content: string): Segment[] {
  if (!content) return [];
  const marks: { start: number; end: number; seg: Segment }[] = [];

  for (const m of Array.from(content.matchAll(MD_IMAGE))) {
    const url = m[2];
    const alt = m[1] || "图片";
    marks.push({
      start: m.index ?? 0,
      end: (m.index ?? 0) + m[0].length,
      seg: isVideoUrl(url, alt) ? { kind: "video", url, label: alt } : { kind: "image", url, alt },
    });
  }
  for (const m of Array.from(content.matchAll(HTML_IMG))) {
    const url = m[1];
    if (!url) continue;
    const start = m.index ?? 0;
    const end = start + m[0].length;
    if (marks.some((x) => !(end <= x.start || start >= x.end))) continue;
    marks.push({
      start,
      end,
      seg: isVideoUrl(url) ? { kind: "video", url, label: "视频" } : { kind: "image", url, alt: "图片" },
    });
  }
  for (const m of Array.from(content.matchAll(MD_LINK))) {
    const label = m[1] || m[3] || "视频";
    const url = m[2] || m[4];
    if (!url) continue;
    const start = m.index ?? 0;
    const end = start + m[0].length;
    if (marks.some((x) => !(end <= x.start || start >= x.end))) continue;
    marks.push({ start, end, seg: { kind: "video", url, label } });
  }
  for (const m of Array.from(content.matchAll(BARE_MEDIA))) {
    const url = m[1] || m[0];
    const start = m.index ?? 0;
    const end = start + m[0].length;
    if (marks.some((x) => !(end <= x.start || start >= x.end))) continue;
    marks.push({
      start,
      end,
      seg: isVideoUrl(url) ? { kind: "video", url, label: "视频" } : { kind: "image", url, alt: "图片" },
    });
  }

  marks.sort((a, b) => a.start - b.start);
  const merged: typeof marks = [];
  for (const m of marks) {
    const last = merged[merged.length - 1];
    if (last && m.start < last.end) continue;
    merged.push(m);
  }

  const segs: Segment[] = [];
  let cursor = 0;
  for (const m of merged) {
    if (m.start > cursor) segs.push({ kind: "text", text: content.slice(cursor, m.start) });
    segs.push(m.seg);
    cursor = m.end;
  }
  if (cursor < content.length) segs.push({ kind: "text", text: content.slice(cursor) });
  return segs.length ? segs : [{ kind: "text", text: content }];
}

/** 带鉴权拉取 gen-assets,转成 blob URL;同路径命中内存缓存秒回。 */
function useAuthedSrc(url: string, enabled: boolean) {
  const [src, setSrc] = useState<string>("");
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!enabled || !url) {
      setSrc("");
      setError(false);
      return;
    }
    let cancelled = false;
    void loadAuthedAsset(url)
      .then((u) => {
        if (cancelled) return;
        setSrc(u || toApiUrl(url));
        setError(false);
      })
      .catch(() => {
        if (!cancelled) {
          setSrc(toApiUrl(url));
          setError(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [url, enabled]);

  return { src, error, setError };
}

function LightboxPortal({
  src,
  alt,
  onClose,
  onPrev,
  onNext,
}: {
  src: string;
  alt: string;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
      role="dialog"
    >
      <button
        type="button"
        className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1 text-sm text-white hover:bg-white/20"
        onClick={onClose}
      >
        关闭
      </button>
      {onPrev && (
        <button
          type="button"
          className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-white hover:bg-white/20"
          onClick={(e) => {
            e.stopPropagation();
            onPrev();
          }}
        >
          ‹
        </button>
      )}
      {onNext && (
        <button
          type="button"
          className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-white hover:bg-white/20"
          onClick={(e) => {
            e.stopPropagation();
            onNext();
          }}
        >
          ›
        </button>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        className="max-h-[90vh] max-w-[95vw] object-contain"
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  );
}

function MediaImage({
  url,
  alt,
  onGenerate,
  onOpen,
}: {
  url: string;
  alt: string;
  onGenerate?: (prompt: string) => void;
  onOpen?: () => void;
}) {
  const loadable = isLoadableMediaUrl(url);
  const { src, error, setError } = useAuthedSrc(url, loadable);

  if (!loadable || error || !src) {
    return (
      <figure className="overflow-hidden rounded-xl border border-indigo-100 bg-indigo-50/50">
        <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-600" />
          <p className="text-sm font-medium text-slate-700">{alt || "配图"}</p>
          <p className="text-[11px] text-slate-400">
            {loadable ? "加载中…" : "正在自动生成真实图片…"}
          </p>
        </div>
      </figure>
    );
  }

  return (
    <figure className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50 shadow-sm">
      <button type="button" className="block w-full cursor-zoom-in" onClick={onOpen} title="点击放大预览">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={alt}
          className="max-h-[36rem] w-full object-contain bg-white"
          loading="lazy"
          onError={() => setError(true)}
        />
      </button>
      {alt && (
        <figcaption className="flex items-center justify-between gap-2 border-t border-slate-100 bg-white px-3 py-1.5 text-[11px] text-slate-500">
          <span className="truncate">{alt}</span>
          <button type="button" className="shrink-0 text-indigo-500 hover:underline" onClick={onOpen}>
            放大
          </button>
        </figcaption>
      )}
    </figure>
  );
}

export function ChatMediaContent({
  content,
  className = "",
  onGenerateImage,
  onGenerateAllImages,
  generatingAll,
}: {
  content: string;
  className?: string;
  onGenerateImage?: (prompt: string) => void;
  /** 一键生成本条消息全部占位配图 */
  onGenerateAllImages?: () => void;
  generatingAll?: boolean;
}) {
  const segs = useMemo(() => parseContent(content), [content]);
  const placeholders = useMemo(() => listImagePlaceholders(content), [content]);
  const images = useMemo(
    () => segs.filter((s): s is Extract<Segment, { kind: "image" }> => s.kind === "image"),
    [segs]
  );
  const [lightbox, setLightbox] = useState<number | null>(null);

  const loadableImages = images.filter((img) => isLoadableMediaUrl(img.url));
  const lightboxItem = lightbox != null ? loadableImages[lightbox] : null;
  const lightboxSrc = lightboxItem ? toApiUrl(lightboxItem.url) : "";

  return (
    <div className={`space-y-3 leading-6 ${className}`}>
      {placeholders.length > 0 && (
        <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-xs text-indigo-700">
          {generatingAll
            ? `正在自动生成 ${placeholders.length} 张配图并保存到本地…`
            : `检测到 ${placeholders.length} 处配图占位,正在自动生成…`}
        </div>
      )}

      {segs.map((s, i) => {
        if (s.kind === "text") {
          const t = s.text;
          if (!t.trim()) return null;
          return (
            <div key={i} className="whitespace-pre-wrap">
              {t}
            </div>
          );
        }
        if (s.kind === "image") {
          const loadableIdx = loadableImages.findIndex((img) => img.url === s.url && img.alt === s.alt);
          return (
            <MediaImage
              key={i}
              url={s.url}
              alt={s.alt}
              onGenerate={onGenerateImage}
              onOpen={
                loadableIdx >= 0
                  ? () => setLightbox(loadableIdx)
                  : undefined
              }
            />
          );
        }
        if (!isLoadableMediaUrl(s.url)) {
          return (
            <figure key={i} className="overflow-hidden rounded-xl border border-dashed border-rose-300 bg-rose-50/50">
              <div className="px-4 py-5 text-center text-sm text-slate-600">
                <p className="font-medium">{s.label || "视频占位"}</p>
                <p className="mt-1 text-[11px] text-slate-400">相对路径无法预览{s.url ? `（${s.url}）` : ""}</p>
              </div>
            </figure>
          );
        }
        return (
          <figure key={i} className="overflow-hidden rounded-xl border border-slate-200 bg-black/90">
            <video src={toApiUrl(s.url)} controls playsInline className="max-h-[28rem] w-full" preload="metadata" />
            <figcaption className="border-t border-slate-800 px-2 py-1 text-[11px] text-slate-300">
              {s.label || "视频预览"}
            </figcaption>
          </figure>
        );
      })}

      {loadableImages.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {loadableImages.map((img, idx) => (
            <button
              key={`${img.url}-${idx}`}
              type="button"
              onClick={() => setLightbox(idx)}
              className="h-16 w-16 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-white"
              title={img.alt}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={toApiUrl(img.url)} alt={img.alt} className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}

      {lightbox != null && lightboxItem && lightboxSrc && (
        <LightboxPortal
          src={lightboxSrc}
          alt={lightboxItem.alt}
          onClose={() => setLightbox(null)}
          onPrev={lightbox > 0 ? () => setLightbox(lightbox - 1) : undefined}
          onNext={lightbox < loadableImages.length - 1 ? () => setLightbox(lightbox + 1) : undefined}
        />
      )}
    </div>
  );
}

export function modalityLabel(mod?: string | null): string {
  if (mod === "image") return "图片";
  if (mod === "video") return "视频";
  return "";
}
