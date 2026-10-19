"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, getLocalProgress } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { NovelCard, StoreHome } from "@/lib/types";
import { BookRow } from "@/components/BookCard";
import { Empty, Spinner } from "@/components/ui";

export default function DiscoverPage() {
  const { user } = useAuth();
  const [home, setHome] = useState<StoreHome | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [guestContinue, setGuestContinue] = useState<NovelCard[]>([]);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const data = await api.publicGet<StoreHome>("/reader/store/home");
        setHome(data);
        if (!user) {
          const local = getLocalProgress();
          if (local.length) {
            const cards: NovelCard[] = [];
            for (const p of local.slice(0, 6)) {
              try {
                const d = await api.publicGet<NovelCard>(`/reader/store/novels/${p.novel_id}`);
                cards.push({ ...d, progress_chapter_id: p.chapter_id });
              } catch {
                /* skip */
              }
            }
            setGuestContinue(cards);
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        setLoading(false);
      }
    })();
  }, [user]);

  const continueList = user ? home?.continue_reading || [] : guestContinue;

  if (loading) return <Spinner />;
  if (!home) return <Empty text={error || "暂无内容"} />;

  return (
    <div className="mx-auto max-w-2xl px-4 pb-8 pt-[max(1rem,env(safe-area-inset-top))]">
      <header className="mb-5 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-wide">织火阅读</h1>
          <p className="text-xs text-black/40">发现好书 · 随时续读</p>
        </div>
        <Link
          href="/search"
          className="rounded-full bg-black/[0.05] px-3 py-1.5 text-xs text-black/55"
        >
          搜索
        </Link>
      </header>

      {continueList.length > 0 && (
        <section className="mb-6">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">继续阅读</h2>
            <Link href="/library" className="text-xs text-amber-700">
              我的书城
            </Link>
          </div>
          <div className="overflow-hidden rounded-2xl bg-white/80 ring-1 ring-black/5">
            {continueList.slice(0, 3).map((b) => (
              <Link
                key={b.id}
                href={b.progress_chapter_id ? `/${b.id}/${b.progress_chapter_id}` : `/${b.id}`}
                className="flex items-center justify-between gap-3 border-b border-black/[0.04] px-4 py-3 last:border-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{b.title}</p>
                  <p className="truncate text-[11px] text-black/40">
                    {b.progress_chapter_title || "打开目录"}
                  </p>
                </div>
                <span className="shrink-0 text-xs text-amber-700">续读</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {home.tags.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 text-sm font-semibold">标签</h2>
          <div className="flex flex-wrap gap-2">
            {home.tags.map((t) => (
              <Link
                key={t.name}
                href={`/search?tag=${encodeURIComponent(t.name)}`}
                className="rounded-full bg-white/80 px-3 py-1 text-xs text-black/60 ring-1 ring-black/5"
              >
                {t.name}
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="mb-6">
        <h2 className="mb-3 text-sm font-semibold">猜你喜欢</h2>
        <BookRow books={home.for_you} />
        {!home.for_you.length && <Empty text="暂无推荐" />}
      </section>

      <section className="mb-6">
        <h2 className="mb-3 text-sm font-semibold">热门</h2>
        <BookRow books={home.hot} />
        {!home.hot.length && <Empty text="还没有可阅读的书" />}
      </section>

      <section className="mb-6">
        <h2 className="mb-3 text-sm font-semibold">最新更新</h2>
        <BookRow books={home.newest} />
      </section>
    </div>
  );
}
