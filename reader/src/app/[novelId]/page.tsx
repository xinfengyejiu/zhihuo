"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  api,
  getLocalFavIds,
  getLocalLikeIds,
  getLocalProgress,
  toggleLocalFav,
  toggleLocalLike,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { NovelCard } from "@/lib/types";
import { CoverImg } from "@/components/CoverImg";
import { Button, Empty, Spinner } from "@/components/ui";

interface Catalog {
  novel: { id: number; title: string; summary: string | null; cover: string | null };
  progress_chapter_id: number | null;
  chapters: {
    id: number;
    title: string;
    word_count: number;
    volume_title: string | null;
  }[];
}

export default function NovelDetailPage() {
  const { novelId } = useParams<{ novelId: string }>();
  const { user } = useAuth();
  const [detail, setDetail] = useState<NovelCard | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [favorited, setFavorited] = useState(false);
  const [liked, setLiked] = useState(false);
  const [shareTip, setShareTip] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [d, c] = await Promise.all([
        api.publicGet<NovelCard>(`/reader/store/novels/${novelId}`),
        api.publicGet<Catalog>(`/reader/novels/${novelId}/catalog`),
      ]);
      setDetail(d);
      setCatalog(c);
      if (user) {
        setFavorited(!!d.favorited);
        setLiked(!!d.liked);
      } else {
        setFavorited(getLocalFavIds().includes(+novelId));
        setLiked(getLocalLikeIds().includes(+novelId));
        const local = getLocalProgress().find((p) => p.novel_id === +novelId);
        if (local && !c.progress_chapter_id) {
          setCatalog({ ...c, progress_chapter_id: local.chapter_id });
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setDetail(null);
      setCatalog(null);
    } finally {
      setLoading(false);
    }
  }, [novelId, user]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  async function toggleFav() {
    if (!user) {
      setFavorited(toggleLocalFav(+novelId));
      return;
    }
    if (favorited) {
      await api.delete(`/reader/novels/${novelId}/favorite`);
      setFavorited(false);
    } else {
      await api.post(`/reader/novels/${novelId}/favorite`);
      setFavorited(true);
    }
  }

  async function toggleLike() {
    if (!user) {
      setLiked(toggleLocalLike(+novelId));
      return;
    }
    if (liked) {
      await api.delete(`/reader/novels/${novelId}/like`);
      setLiked(false);
    } else {
      await api.post(`/reader/novels/${novelId}/like`);
      setLiked(true);
    }
  }

  async function share() {
    const url = typeof window !== "undefined" ? window.location.href : "";
    const title = detail?.title || "织火好书";
    try {
      if (navigator.share) {
        await navigator.share({ title, text: detail?.summary || title, url });
        setShareTip("已打开系统分享");
      } else {
        await navigator.clipboard.writeText(url);
        setShareTip("链接已复制,推荐给朋友吧");
      }
    } catch {
      try {
        await navigator.clipboard.writeText(url);
        setShareTip("链接已复制");
      } catch {
        setShareTip(url);
      }
    }
    setTimeout(() => setShareTip(""), 2500);
  }

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (!detail || !catalog) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 px-6">
        <Empty text={error || "小说不存在"} />
        <Link href="/" className="text-sm text-amber-700">
          回发现
        </Link>
      </div>
    );
  }

  let lastVolume: string | null = null;
  const continueId = catalog.progress_chapter_id || catalog.chapters[0]?.id;

  return (
    <div className="min-h-dvh pb-28">
      <div className="sticky top-0 z-20 border-b border-black/5 bg-[#f7f3ea]/95 px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] backdrop-blur">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-3">
          <Link href="/" className="text-sm text-black/45">
            ← 发现
          </Link>
          <span className="truncate text-sm font-medium">{detail.title}</span>
          <button type="button" onClick={() => void share()} className="text-sm text-amber-700">
            分享
          </button>
        </div>
      </div>

      <div className="mx-auto max-w-2xl px-4 pt-6">
        <div className="mb-6 flex gap-4">
          <div className="h-40 w-28 shrink-0 overflow-hidden rounded-md shadow-sm ring-1 ring-black/5">
            <CoverImg url={detail.cover || catalog.novel.cover} title={detail.title} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold leading-snug">{detail.title}</h1>
            {detail.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {detail.tags.map((t) => (
                  <Link
                    key={t}
                    href={`/search?tag=${encodeURIComponent(t)}`}
                    className="rounded-full bg-black/[0.05] px-2 py-0.5 text-[11px] text-black/50"
                  >
                    {t}
                  </Link>
                ))}
              </div>
            )}
            <p className="mt-2 text-xs text-black/40">
              {detail.chapter_count} 章 · {detail.word_count.toLocaleString()} 字 · {detail.like_count} 喜欢 ·{" "}
              {detail.favorite_count} 收藏
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant={favorited ? "primary" : "outline"} onClick={() => void toggleFav()}>
                {favorited ? "已收藏" : "收藏"}
              </Button>
              <Button size="sm" variant={liked ? "primary" : "outline"} onClick={() => void toggleLike()}>
                {liked ? "已喜欢" : "喜欢"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void share()}>
                推荐给朋友
              </Button>
            </div>
            {shareTip && <p className="mt-1 text-[11px] text-emerald-600">{shareTip}</p>}
          </div>
        </div>

        {detail.summary && <p className="mb-6 text-sm leading-6 text-black/55">{detail.summary}</p>}

        <h2 className="mb-2 text-sm font-semibold">目录</h2>
        {catalog.chapters.length === 0 ? (
          <Empty text="还没有审核通过的章节" />
        ) : (
          <div className="overflow-hidden rounded-2xl bg-white/80 shadow-sm ring-1 ring-black/5">
            {catalog.chapters.map((c) => {
              const showVolume = c.volume_title && c.volume_title !== lastVolume;
              lastVolume = c.volume_title;
              const isProgress = c.id === catalog.progress_chapter_id;
              return (
                <div key={c.id}>
                  {showVolume && (
                    <div className="bg-black/[0.03] px-4 py-2 text-xs font-medium text-black/40">{c.volume_title}</div>
                  )}
                  <Link
                    href={`/${novelId}/${c.id}`}
                    className={`flex items-center justify-between gap-3 border-b border-black/[0.04] px-4 py-3.5 text-[15px] last:border-b-0 ${
                      isProgress ? "text-amber-700" : ""
                    }`}
                  >
                    <span className="min-w-0 truncate">
                      {c.title}
                      {isProgress && <span className="ml-2 text-xs">续读</span>}
                    </span>
                    <span className="shrink-0 text-xs text-black/30">{c.word_count} 字</span>
                  </Link>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {continueId && (
        <div className="fixed inset-x-0 bottom-14 z-30 border-t border-black/5 bg-[#f7f3ea]/95 px-4 pb-3 pt-3 backdrop-blur">
          <div className="mx-auto max-w-2xl">
            <Link
              href={`/${novelId}/${continueId}`}
              className="flex h-12 items-center justify-center rounded-full bg-[#e8772a] text-base font-medium text-white shadow-md"
            >
              {catalog.progress_chapter_id ? "继续阅读" : "开始阅读"}
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
