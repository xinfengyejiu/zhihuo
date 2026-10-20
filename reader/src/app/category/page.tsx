"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { CategoryItem, NovelCard } from "@/lib/types";
import { RankGrid } from "@/components/RankGrid";
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

const CAT_ACCENTS = [
  "bg-[#f3e6d8] text-[#8a5a2b]",
  "bg-[#ebe4f5] text-[#5c4a7a]",
  "bg-[#e4efe8] text-[#3d6b52]",
  "bg-[#f5e4e4] text-[#7a4545]",
  "bg-[#e4eaf5] text-[#3d5270]",
  "bg-[#f5efd8] text-[#7a6a2b]",
  "bg-[#e8f0f5] text-[#3d5a6b]",
  "bg-[#f0e8e4] text-[#6b4a3d]",
];

type SortKey = "hot" | "new" | "likes";

export default function CategoryPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-[50vh] items-center justify-center">
          <Spinner />
        </div>
      }
    >
      <CategoryInner />
    </Suspense>
  );
}

function CategoryInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const keyFromUrl = (sp.get("key") || "").trim();
  const [categories, setCategories] = useState<CategoryItem[]>(FALLBACK_CATEGORIES);
  const [activeKey, setActiveKey] = useState(keyFromUrl || "");
  const [sort, setSort] = useState<SortKey>("hot");
  const [books, setBooks] = useState<NovelCard[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const loadingMoreRef = useRef(false);

  const PAGE_SIZE = 20;

  useEffect(() => {
    setActiveKey(keyFromUrl || "");
  }, [keyFromUrl]);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.publicGet<{ categories: CategoryItem[] }>("/reader/store/categories");
        if (data.categories?.length) setCategories(data.categories);
      } catch {
        /* keep fallback */
      }
    })();
  }, []);

  const loadList = useCallback(
    async (catKey: string, sortKey: SortKey, pageNum: number, append: boolean) => {
      if (!catKey) {
        setBooks([]);
        setHasMore(false);
        return;
      }
      if (append) {
        if (loadingMoreRef.current) return;
        loadingMoreRef.current = true;
        setLoadingMore(true);
      } else {
        setLoading(true);
      }
      try {
        const params = new URLSearchParams({
          sort: sortKey,
          page: String(pageNum),
          page_size: String(PAGE_SIZE),
        });
        if (catKey !== "all") params.set("category", catKey);
        const list = await api.publicGet<NovelCard[]>(`/reader/store/novels?${params}`);
        setBooks((prev) => {
          if (!append) return list;
          const seen = new Set(prev.map((b) => b.id));
          return [...prev, ...list.filter((b) => !seen.has(b.id))];
        });
        setPage(pageNum);
        setHasMore(list.length >= PAGE_SIZE);
      } catch {
        if (!append) setBooks([]);
        setHasMore(false);
      } finally {
        setLoading(false);
        setLoadingMore(false);
        loadingMoreRef.current = false;
      }
    },
    []
  );

  useEffect(() => {
    if (!activeKey) return;
    void loadList(activeKey, sort, 1, false);
  }, [activeKey, sort, loadList]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !activeKey) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && hasMore && !loadingMoreRef.current) {
          void loadList(activeKey, sort, page + 1, true);
        }
      },
      { rootMargin: "200px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [activeKey, sort, page, hasMore, loadList, books.length]);

  function selectCategory(key: string) {
    setActiveKey(key);
    const qs = key && key !== "all" ? `?key=${encodeURIComponent(key)}` : "?key=all";
    router.replace(`/category${qs}`);
  }

  const activeLabel =
    categories.find((c) => c.key === activeKey)?.label ||
    (activeKey === "all" ? "全部" : activeKey) ||
    "分类";

  // 未选分类:展示网格
  if (!activeKey) {
    const gridCats = categories.filter((c) => c.key !== "all");
    return (
      <div className="mx-auto max-w-2xl px-4 pb-8 pt-[max(1rem,env(safe-area-inset-top))]">
        <h1 className="mb-1 text-xl font-bold">分类</h1>
        <p className="mb-5 text-xs text-black/40">按类型找书</p>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
          <button
            type="button"
            onClick={() => selectCategory("all")}
            className="flex aspect-[4/3] flex-col items-center justify-center rounded-2xl bg-[#f3e6d8] text-[#8a5a2b] ring-1 ring-black/5"
          >
            <span className="text-base font-semibold">全部</span>
            <span className="mt-1 text-[10px] opacity-60">热门好书</span>
          </button>
          {gridCats.map((c, i) => (
            <button
              key={c.key}
              type="button"
              onClick={() => selectCategory(c.key)}
              className={`flex aspect-[4/3] flex-col items-center justify-center rounded-2xl ring-1 ring-black/5 ${
                CAT_ACCENTS[i % CAT_ACCENTS.length]
              }`}
            >
              <span className="text-base font-semibold">{c.label}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 pb-8 pt-[max(1rem,env(safe-area-inset-top))]">
      <div className="mb-4 flex items-center gap-3">
        <button
          type="button"
          onClick={() => {
            setActiveKey("");
            router.replace("/category");
          }}
          className="text-sm text-black/45"
        >
          ← 分类
        </button>
        <h1 className="text-lg font-bold">{activeLabel}</h1>
      </div>

      <div className="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1 scrollbar-none">
        {categories.map((c) => {
          const active = activeKey === c.key;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => selectCategory(c.key)}
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

      <div className="mb-4 flex gap-2 text-xs">
        {(
          [
            ["hot", "热门"],
            ["new", "最新"],
            ["likes", "喜欢"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => setSort(k)}
            className={`rounded-full px-3 py-1.5 ${
              sort === k ? "bg-amber-600 text-white" : "bg-black/5 text-black/55"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : books.length ? (
        <>
          <RankGrid books={books} />
          <div ref={sentinelRef} className="flex h-14 items-center justify-center">
            {loadingMore ? (
              <span className="text-xs text-black/35">加载更多…</span>
            ) : hasMore ? (
              <button
                type="button"
                onClick={() => void loadList(activeKey, sort, page + 1, true)}
                className="text-xs text-amber-700"
              >
                加载更多
              </button>
            ) : (
              <span className="text-xs text-black/30">已全部加载</span>
            )}
          </div>
        </>
      ) : (
        <Empty text="该分类暂无书籍" />
      )}

      <p className="mt-4 text-center text-[11px] text-black/30">
        <Link href="/" className="text-amber-700">
          回书城
        </Link>
      </p>
    </div>
  );
}
