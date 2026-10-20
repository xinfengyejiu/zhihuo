"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, getLocalProgress } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { CategoryItem, NovelCard, StoreHome } from "@/lib/types";
import { FeaturedRow, RankGrid } from "@/components/RankGrid";
import { Empty, Spinner } from "@/components/ui";

const FALLBACK_CATEGORIES: CategoryItem[] = [
  { key: "all", label: "全部" },
  { key: "dushi", label: "都市" },
  { key: "xuanhuan", label: "玄幻" },
  { key: "naodong", label: "脑洞" },
  { key: "chuanyue", label: "穿越" },
  { key: "xitong", label: "系统" },
  { key: "yanqing", label: "言情" },
  { key: "lishi", label: "历史" },
  { key: "kehuan", label: "科幻" },
];

export default function StoreHomePage() {
  const { user } = useAuth();
  const [home, setHome] = useState<StoreHome | null>(null);
  const [category, setCategory] = useState("all");
  const [channel, setChannel] = useState<"novel" | "discover">("novel");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [guestContinue, setGuestContinue] = useState<NovelCard[]>([]);
  const featuredRef = useRef<HTMLElement>(null);

  const load = useCallback(async (cat: string) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (cat && cat !== "all") params.set("category", cat);
      const qs = params.toString();
      const data = await api.publicGet<StoreHome>(`/reader/store/home${qs ? `?${qs}` : ""}`);
      setHome({
        ...data,
        rank: data.rank || data.hot || [],
        featured: data.featured || data.for_you || [],
        categories: data.categories?.length ? data.categories : FALLBACK_CATEGORIES,
      });
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
        } else {
          setGuestContinue([]);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setHome(null);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void load(category);
  }, [category, load]);

  function selectChannel(next: "novel" | "discover") {
    setChannel(next);
    if (next === "discover") {
      featuredRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  const continueList = user ? home?.continue_reading || [] : guestContinue;
  const categories = home?.categories?.length ? home.categories : FALLBACK_CATEGORIES;

  return (
    <div className="mx-auto max-w-2xl pb-8 pt-[max(0.75rem,env(safe-area-inset-top))]">
      <div className="px-4">
        <Link
          href="/search"
          className="mb-3 flex h-10 items-center gap-2 rounded-full bg-white/90 px-4 text-sm text-black/40 ring-1 ring-black/5"
        >
          <span className="opacity-50">⌕</span>
          <span>搜索书名、作者或关键词</span>
        </Link>

        <div className="mb-3 flex items-center gap-5 text-[15px]">
          {(
            [
              ["novel", "小说"],
              ["discover", "发现"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => selectChannel(k)}
              className={`relative pb-1.5 font-medium ${
                channel === k ? "text-[#3b3a36]" : "text-black/35"
              }`}
            >
              {label}
              {channel === k && (
                <span className="absolute inset-x-1 -bottom-0.5 h-0.5 rounded-full bg-[#e8772a]" />
              )}
            </button>
          ))}
        </div>

        <div className="-mx-4 mb-4 flex gap-2 overflow-x-auto px-4 pb-1 scrollbar-none">
          {categories.map((c) => {
            const active = category === c.key || (category === "all" && c.key === "all");
            return (
              <button
                key={c.key}
                type="button"
                onClick={() => setCategory(c.key)}
                className={`shrink-0 rounded-full px-3.5 py-1.5 text-xs ${
                  active
                    ? "bg-[#e8772a] font-medium text-white"
                    : "bg-white/80 text-black/55 ring-1 ring-black/5"
                }`}
              >
                {c.label}
              </button>
            );
          })}
        </div>
      </div>

      {loading && !home ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : !home ? (
        <div className="px-4">
          <Empty text={error || "暂无内容"} />
        </div>
      ) : (
        <>
          {continueList.length > 0 && (
            <section className="mb-5 px-4">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-sm font-semibold">继续阅读</h2>
                <Link href="/library" className="text-xs text-amber-700">
                  书架
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

          <section className="mb-6 px-4">
            <div className="mb-3 flex items-baseline justify-between">
              <h2 className="text-sm font-semibold">热门榜</h2>
              <Link
                href={`/category?key=${encodeURIComponent(category || "all")}`}
                className="text-xs text-black/35"
              >
                更多 →
              </Link>
            </div>
            {loading ? (
              <div className="flex justify-center py-8">
                <Spinner />
              </div>
            ) : (home.rank || []).length ? (
              <RankGrid books={home.rank} />
            ) : (
              <Empty text="该分类暂无书籍" />
            )}
          </section>

          <section ref={featuredRef} className="mb-6 scroll-mt-4">
            <div className="mb-3 flex items-baseline justify-between px-4">
              <div>
                <h2 className="text-sm font-semibold">精选推荐</h2>
                <p className="mt-0.5 text-[11px] text-black/35">好看，读得下去</p>
              </div>
            </div>
            {(home.featured || []).length ? (
              <FeaturedRow books={home.featured} />
            ) : (
              <div className="px-4">
                <Empty text="暂无推荐" />
              </div>
            )}
          </section>

          {(home.newest || []).length > 0 && (
            <section className="mb-4 px-4">
              <h2 className="mb-3 text-sm font-semibold">最新更新</h2>
              <RankGrid books={home.newest.slice(0, 6)} />
            </section>
          )}
        </>
      )}
    </div>
  );
}
