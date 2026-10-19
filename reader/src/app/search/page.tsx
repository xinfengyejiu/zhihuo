"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import type { NovelCard } from "@/lib/types";
import { BookRow } from "@/components/BookCard";
import { Empty, Input, Spinner } from "@/components/ui";

function SearchInner() {
  const sp = useSearchParams();
  const initTag = sp.get("tag") || "";
  const [q, setQ] = useState("");
  const [tag, setTag] = useState(initTag);
  const [sort, setSort] = useState<"hot" | "new" | "likes">("hot");
  const [books, setBooks] = useState<NovelCard[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ sort, page: "1" });
      if (q.trim()) params.set("q", q.trim());
      if (tag.trim()) params.set("tag", tag.trim());
      setBooks(await api.publicGet<NovelCard[]>(`/reader/store/novels?${params}`));
    } catch {
      setBooks([]);
    } finally {
      setLoading(false);
    }
  }, [q, tag, sort]);

  useEffect(() => {
    setTag(initTag);
  }, [initTag]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);

  return (
    <div className="mx-auto max-w-2xl px-4 pb-8 pt-[max(1rem,env(safe-area-inset-top))]">
      <div className="mb-4 flex items-center gap-2">
        <Link href="/" className="text-sm text-black/45">
          ←
        </Link>
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索书名或简介"
          autoFocus
        />
      </div>
      {tag && (
        <div className="mb-3 flex items-center gap-2 text-xs">
          <span className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-800">标签:{tag}</span>
          <button type="button" className="text-black/40" onClick={() => setTag("")}>
            清除
          </button>
        </div>
      )}
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
            className={`rounded-full px-3 py-1 ${sort === k ? "bg-amber-600 text-white" : "bg-black/5 text-black/55"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {loading ? <Spinner /> : books.length ? <BookRow books={books} /> : <Empty text="没有找到相关书籍" />}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <SearchInner />
    </Suspense>
  );
}
