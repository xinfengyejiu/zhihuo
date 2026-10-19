"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useProduct } from "@/lib/product";
import { useTasks } from "@/lib/tasks";
import { Badge, Button } from "./ui";

const NOVEL_NAV = [
  { href: "/", label: "聊天" },
  { href: "/shelf", label: "书架" },
  { href: "/review", label: "审核台" },
  { href: "/publish", label: "发布中心" },
];

const MEDIA_NAV = [
  { href: "/media/chat", label: "聊天" },
  { href: "/media", label: "账号" },
  { href: "/media/topics", label: "选题库" },
];

const DRAMA_NAV = [
  { href: "/drama/chat", label: "聊天" },
  { href: "/drama", label: "项目" },
];

const SHARED_NAV = [
  { href: "/knowledge", label: "知识库" },
  { href: "/skills", label: "技能工坊" },
];

export default function TopBar() {
  const { user, logout, loading } = useAuth();
  const { tasks, runningCount, clearTask } = useTasks();
  const { current, products, setCurrent } = useProduct();
  const [taskOpen, setTaskOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();
  const hide = pathname?.startsWith("/reader/") || pathname === "/login";

  useEffect(() => {
    setMenuOpen(false);
    setTaskOpen(false);
  }, [pathname]);

  if (hide) return null;

  const productNav = current === "media" ? MEDIA_NAV : current === "drama" ? DRAMA_NAV : NOVEL_NAV;
  const NAV = [...productNav, ...SHARED_NAV];
  const taskList = Object.values(tasks).sort((a, b) => b.startedAt - a.startedAt);

  function navClass(href: string) {
    let active = false;
    if (href === "/") {
      active = pathname === "/";
    } else if (href === "/media/chat") {
      active = pathname === "/media/chat";
    } else if (href === "/media") {
      active =
        pathname === "/media" ||
        (/^\/media\/\d+/.test(pathname || "") && !pathname.startsWith("/media/topics") && !pathname.startsWith("/media/chat"));
    } else if (href === "/drama/chat") {
      active = pathname === "/drama/chat";
    } else if (href === "/drama") {
      active = pathname === "/drama" || /^\/drama\/\d+/.test(pathname || "");
    } else {
      active = pathname.startsWith(href);
    }
    return `block rounded-lg px-3 py-2 text-sm md:inline-block md:rounded-none md:px-0 md:py-0 ${
      active
        ? "bg-indigo-50 font-medium text-indigo-600 md:bg-transparent"
        : "text-slate-600 hover:bg-slate-50 md:hover:bg-transparent md:hover:text-indigo-600"
    }`;
  }

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-3 px-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-2 sm:gap-4">
          <button
            type="button"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 md:hidden"
            aria-label="打开菜单"
            onClick={() => setMenuOpen((v) => !v)}
          >
            {menuOpen ? "✕" : "☰"}
          </button>
          <Link href="/hub" className="shrink-0 text-lg font-bold text-indigo-600">
            织火
          </Link>
          {user && (
            <select
              className="hidden max-w-[7.5rem] truncate rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 md:block"
              value={current || "novel"}
              onChange={(e) => {
                const key = e.target.value;
                setCurrent(key);
                const p = products.find((x) => x.key === key);
                if (p) window.location.href = p.href;
              }}
            >
              {products.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
          <nav className="hidden items-center gap-4 text-sm md:flex">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className={navClass(item.href)}>
                {item.label}
              </Link>
            ))}
          </nav>
        </div>

        <div className="flex shrink-0 items-center gap-2 text-sm sm:gap-3">
          {loading ? null : user ? (
            <>
              <div className="relative">
                <button
                  onClick={() => setTaskOpen((v) => !v)}
                  className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-600 hover:border-indigo-300 hover:text-indigo-600 sm:gap-1.5 sm:px-2.5"
                >
                  {runningCount > 0 ? (
                    <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-600" />
                  ) : (
                    <span className="text-slate-400">☑</span>
                  )}
                  <span className="hidden sm:inline">任务{runningCount > 0 ? `(${runningCount})` : "中心"}</span>
                  <span className="sm:hidden">{runningCount > 0 ? runningCount : "任务"}</span>
                </button>
                {taskOpen && (
                  <div className="absolute right-0 top-10 z-50 w-[min(24rem,calc(100vw-1.5rem))] rounded-xl border border-slate-200 bg-white p-2 shadow-xl">
                    {taskList.length === 0 ? (
                      <p className="p-4 text-center text-xs text-slate-400">暂无 AI 任务</p>
                    ) : (
                      taskList.map((t) => (
                        <div key={t.key} className="flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-50">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              {t.status === "running" && (
                                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-600" />
                              )}
                              <span className="truncate font-medium text-slate-700">{t.label}</span>
                            </div>
                            <div className="mt-0.5 text-xs">
                              {t.status === "running" && (
                                <span className="text-slate-400">
                                  进行中{t.output ? ` · 已输出 ${t.output.length} 字` : "…"}
                                </span>
                              )}
                              {t.status === "done" && <Badge color="green">完成</Badge>}
                              {t.status === "error" && <span className="text-red-500">{(t.error || "失败").slice(0, 60)}</span>}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-2 text-xs">
                            {t.href && (
                              <Link href={t.href} onClick={() => setTaskOpen(false)} className="text-indigo-600 hover:underline">
                                查看
                              </Link>
                            )}
                            {t.status !== "running" && (
                              <button onClick={() => clearTask(t.key)} className="text-slate-300 hover:text-slate-500">
                                清除
                              </button>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
              <Link href="/profile" className="max-w-[5.5rem] truncate text-slate-600 hover:text-indigo-600 sm:max-w-none">
                {user.nickname || user.username}
              </Link>
              <Button variant="ghost" size="sm" onClick={logout} className="hidden sm:inline-flex">
                退出
              </Button>
            </>
          ) : (
            <Link href="/login">
              <Button size="sm">登录</Button>
            </Link>
          )}
        </div>
      </div>

      {menuOpen && (
        <div className="border-t border-slate-100 bg-white px-3 py-2 md:hidden">
          <nav className="flex flex-col gap-1">
            <Link href="/hub" className={navClass("/hub")} onClick={() => setMenuOpen(false)}>
              产品台
            </Link>
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className={navClass(item.href)} onClick={() => setMenuOpen(false)}>
                {item.label}
              </Link>
            ))}
            {user && (
              <button
                type="button"
                className="rounded-lg px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => {
                  setMenuOpen(false);
                  logout();
                }}
              >
                退出登录
              </button>
            )}
          </nav>
        </div>
      )}
    </header>
  );
}
