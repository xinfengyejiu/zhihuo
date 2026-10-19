"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "发现", match: (p: string) => p === "/" || p.startsWith("/search") },
  { href: "/library", label: "我的书城", match: (p: string) => p.startsWith("/library") },
  { href: "/me", label: "我的", match: (p: string) => p.startsWith("/me") || p.startsWith("/login") || p.startsWith("/register") },
];

export function BottomNav() {
  const pathname = usePathname() || "/";
  // 正文页不显示底栏
  if (/^\/\d+\/\d+/.test(pathname)) return null;

  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-black/5 bg-[#f7f3ea]/95 pb-[env(safe-area-inset-bottom)] backdrop-blur">
      <div className="mx-auto flex h-14 max-w-2xl items-stretch">
        {ITEMS.map((item) => {
          const active = item.match(pathname);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex flex-1 items-center justify-center text-sm ${
                active ? "font-semibold text-amber-700" : "text-black/45"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
