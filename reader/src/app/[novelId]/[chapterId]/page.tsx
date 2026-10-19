"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { api, upsertLocalProgress } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui";

interface ChapterData {
  id: number;
  title: string;
  content: string;
  word_count: number;
  prev_id: number | null;
  next_id: number | null;
}

const THEMES = [
  { key: "paper", label: "纸张", bg: "#f7f3ea", fg: "#3b3a36" },
  { key: "green", label: "护眼", bg: "#e8f0e4", fg: "#2f3b2f" },
  { key: "dark", label: "夜间", bg: "#1a1c22", fg: "#c8ccd4" },
] as const;

type ThemeKey = (typeof THEMES)[number]["key"];

export default function ReaderChapterPage() {
  const { novelId, chapterId } = useParams<{ novelId: string; chapterId: string }>();
  const { user } = useAuth();
  const router = useRouter();
  const [chapter, setChapter] = useState<ChapterData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<ThemeKey>("paper");
  const [fontSize, setFontSize] = useState(18);
  const [speaking, setSpeaking] = useState(false);
  const [chromeVisible, setChromeVisible] = useState(true);
  const lastScrollY = useRef(0);
  const lastReport = useRef(0);

  useEffect(() => {
    const savedTheme = localStorage.getItem("reader_theme") as ThemeKey | null;
    const savedSize = localStorage.getItem("reader_font_size");
    if (savedTheme && THEMES.some((t) => t.key === savedTheme)) setTheme(savedTheme);
    if (savedSize) setFontSize(Math.min(28, Math.max(14, +savedSize)));
  }, []);

  const reportProgress = useCallback(
    async (pct: number) => {
      const payload = { novel_id: +novelId, chapter_id: +chapterId, position_pct: pct };
      upsertLocalProgress(payload);
      if (!user) return;
      const now = Date.now();
      if (now - lastReport.current < 4000) return;
      lastReport.current = now;
      try {
        await api.put(`/reader/novels/${novelId}/progress`, {
          chapter_id: +chapterId,
          position_pct: pct,
        });
      } catch {
        /* ignore */
      }
    },
    [novelId, chapterId, user]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
    }
    try {
      setChapter(await api.publicGet<ChapterData>(`/reader/novels/${novelId}/chapters/${chapterId}`));
      setChromeVisible(true);
      window.scrollTo(0, 0);
      void reportProgress(0);
    } catch (e) {
      setChapter(null);
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [novelId, chapterId, reportProgress]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
    };
  }, []);

  useEffect(() => {
    const onScroll = () => {
      const y = window.scrollY;
      if (y < 40) setChromeVisible(true);
      else if (y > lastScrollY.current + 8) setChromeVisible(false);
      else if (y < lastScrollY.current - 8) setChromeVisible(true);
      lastScrollY.current = y;
      const doc = document.documentElement;
      const max = Math.max(1, doc.scrollHeight - window.innerHeight);
      const pct = Math.min(100, Math.round((y / max) * 100));
      void reportProgress(pct);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [reportProgress]);

  function toggleSpeak() {
    if (!chapter) return;
    if (!("speechSynthesis" in window)) {
      alert("当前浏览器不支持朗读");
      return;
    }
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }
    const utter = new SpeechSynthesisUtterance(`${chapter.title}。${chapter.content}`);
    utter.lang = "zh-CN";
    utter.rate = 1;
    utter.onend = () => setSpeaking(false);
    utter.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utter);
    setSpeaking(true);
  }

  function setPref(theme_: ThemeKey, size: number) {
    setTheme(theme_);
    setFontSize(size);
    localStorage.setItem("reader_theme", theme_);
    localStorage.setItem("reader_font_size", String(size));
  }

  const themeMeta = THEMES.find((t) => t.key === theme) || THEMES[0];

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center" style={{ background: themeMeta.bg, color: themeMeta.fg }}>
        <Spinner />
      </div>
    );
  }
  if (!chapter) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 px-6 text-center" style={{ background: themeMeta.bg, color: themeMeta.fg }}>
        <p className="text-sm opacity-60">{error || "章节不存在或未审核通过"}</p>
        <Link href={`/${novelId}`} className="text-sm text-amber-700">
          返回目录
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-dvh select-text" style={{ background: themeMeta.bg, color: themeMeta.fg }}>
      <div
        className={`fixed inset-x-0 top-0 z-30 border-b transition-transform duration-200 ${
          chromeVisible ? "translate-y-0" : "-translate-y-full"
        }`}
        style={{
          background: `${themeMeta.bg}f2`,
          borderColor: theme === "dark" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)",
          paddingTop: "env(safe-area-inset-top)",
        }}
      >
        <div className="mx-auto flex h-12 max-w-2xl items-center justify-between gap-2 px-3">
          <button type="button" onClick={() => router.push(`/${novelId}`)} className="rounded-full px-2 py-1 text-sm opacity-70">
            目录
          </button>
          <span className="min-w-0 truncate text-center text-sm opacity-80">{chapter.title}</span>
          <button type="button" onClick={() => setChromeVisible((v) => !v)} className="rounded-full px-2 py-1 text-sm opacity-70">
            设置
          </button>
        </div>
      </div>

      <article
        className="mx-auto max-w-2xl px-5 pb-32 pt-[calc(3.5rem+env(safe-area-inset-top))]"
        onClick={() => setChromeVisible((v) => !v)}
      >
        <h1 className="mb-8 text-center text-[1.35rem] font-semibold leading-snug tracking-wide">{chapter.title}</h1>
        <div
          className="whitespace-pre-wrap break-words"
          style={{ fontSize, lineHeight: 1.95, letterSpacing: "0.02em", textAlign: "justify" }}
        >
          {chapter.content}
        </div>
        <p className="mt-10 text-center text-xs opacity-35">{chapter.word_count} 字</p>
      </article>

      <div
        className={`fixed inset-x-0 bottom-0 z-30 border-t transition-transform duration-200 ${
          chromeVisible ? "translate-y-0" : "translate-y-full"
        }`}
        style={{
          background: `${themeMeta.bg}f5`,
          borderColor: theme === "dark" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mx-auto max-w-2xl px-3 pb-2 pt-2">
          <div className="mb-2 flex items-center justify-between gap-2 overflow-x-auto text-xs">
            <button
              type="button"
              onClick={toggleSpeak}
              className={`shrink-0 rounded-full px-3 py-1.5 ${speaking ? "bg-amber-600 text-white" : theme === "dark" ? "bg-white/10" : "bg-black/5"}`}
            >
              {speaking ? "停止" : "朗读"}
            </button>
            {THEMES.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setPref(t.key, fontSize)}
                className={`shrink-0 rounded-full px-3 py-1.5 ${
                  theme === t.key ? "bg-amber-600 text-white" : theme === "dark" ? "bg-white/10" : "bg-black/5"
                }`}
              >
                {t.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setPref(theme, Math.max(14, fontSize - 1))}
              className={`shrink-0 rounded-full px-3 py-1.5 ${theme === "dark" ? "bg-white/10" : "bg-black/5"}`}
            >
              A-
            </button>
            <button
              type="button"
              onClick={() => setPref(theme, Math.min(28, fontSize + 1))}
              className={`shrink-0 rounded-full px-3 py-1.5 ${theme === "dark" ? "bg-white/10" : "bg-black/5"}`}
            >
              A+
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {chapter.prev_id ? (
              <Link
                href={`/${novelId}/${chapter.prev_id}`}
                className={`flex h-11 items-center justify-center rounded-full text-sm ${theme === "dark" ? "bg-white/10" : "bg-black/5"}`}
              >
                上一章
              </Link>
            ) : (
              <span className={`flex h-11 items-center justify-center rounded-full text-sm opacity-30 ${theme === "dark" ? "bg-white/5" : "bg-black/[0.03]"}`}>
                已是第一章
              </span>
            )}
            {chapter.next_id ? (
              <Link
                href={`/${novelId}/${chapter.next_id}`}
                className="flex h-11 items-center justify-center rounded-full bg-[#e8772a] text-sm font-medium text-white"
              >
                下一章
              </Link>
            ) : (
              <span className={`flex h-11 items-center justify-center rounded-full text-sm opacity-40 ${theme === "dark" ? "bg-white/5" : "bg-black/[0.03]"}`}>
                已到最新
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
