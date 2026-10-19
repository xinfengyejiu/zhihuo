"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, getLocalFavIds, getLocalLikeIds, getLocalProgress } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { NovelCard } from "@/lib/types";
import { BookRow } from "@/components/BookCard";
import { Empty, Spinner } from "@/components/ui";

type Tab = "reading" | "favorite" | "like" | "history";
const TABS: { key: Tab; label: string }[] = [
  { key: "reading", label: "继续阅读" },
  { key: "favorite", label: "收藏" },
  { key: "like", label: "喜欢" },
  { key: "history", label: "浏览历史" },
];

export default function LibraryPage() {
  const { user, loading: authLoading } = useAuth();
  const [tab, setTab] = useState<Tab>("reading");
  const [books, setBooks] = useState<NovelCard[]>([]);
  const [loading, setLoading] = useState(true);

  const loadGuest = useCallback(async () => {
    setLoading(true);
    try {
      if (tab === "reading" || tab === "history") {
        const local = getLocalProgress();
        const cards: NovelCard[] = [];
        for (const p of local.slice(0, 40)) {
          try {
            const d = await api.publicGet<NovelCard>(`/reader/store/novels/${p.novel_id}`);
            cards.push({ ...d, progress_chapter_id: p.chapter_id });
          } catch {
            /* skip */
          }
        }
        setBooks(cards);
      } else if (tab === "favorite") {
        const ids = getLocalFavIds();
        const cards: NovelCard[] = [];
        for (const id of ids) {
          try {
            cards.push(await api.publicGet<NovelCard>(`/reader/store/novels/${id}`));
          } catch {
            /* skip */
          }
        }
        setBooks(cards);
      } else {
        const ids = getLocalLikeIds();
        const cards: NovelCard[] = [];
        for (const id of ids) {
          try {
            cards.push(await api.publicGet<NovelCard>(`/reader/store/novels/${id}`));
          } catch {
            /* skip */
          }
        }
        setBooks(cards);
      }
    } finally {
      setLoading(false);
    }
  }, [tab]);

  const loadUser = useCallback(async () => {
    setLoading(true);
    try {
      setBooks(await api.get<NovelCard[]>(`/reader/me/bookshelf?tab=${tab}`));
    } catch {
      setBooks([]);
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    if (authLoading) return;
    if (user) void loadUser();
    else void loadGuest();
  }, [user, authLoading, loadUser, loadGuest]);

  return (
    <div className="mx-auto max-w-2xl px-4 pb-8 pt-[max(1rem,env(safe-area-inset-top))]">
      <h1 className="mb-1 text-xl font-bold">我的书城</h1>
      <p className="mb-4 text-xs text-black/40">
        {user ? `你好,${user.nickname || user.username}` : "游客模式 · 登录后可云端同步"}
      </p>
      {!user && (
        <Link href="/login" className="mb-4 inline-block text-sm text-amber-700">
          登录同步书架 →
        </Link>
      )}
      <div className="mb-4 flex gap-1 overflow-x-auto text-xs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`shrink-0 rounded-full px-3 py-1.5 ${
              tab === t.key ? "bg-amber-600 text-white" : "bg-black/5 text-black/55"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {loading ? <Spinner /> : books.length ? <BookRow books={books} /> : <Empty text="这里还是空的" />}
    </div>
  );
}
