"use client";

import { usePathname } from "next/navigation";

/** 首页/阅读器全宽;其余页面居中容器,手机端收紧边距。 */
export function MainShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname?.startsWith("/reader/")) {
    return <main className="min-h-screen">{children}</main>;
  }
  if (pathname === "/" || pathname === "/media/chat" || pathname === "/drama/chat") {
    return <main className="h-[calc(100dvh-3.5rem)] overflow-hidden bg-slate-50">{children}</main>;
  }
  if (pathname === "/login") {
    return <main className="mx-auto max-w-md px-4 py-10">{children}</main>;
  }
  return <main className="mx-auto max-w-6xl px-3 py-4 sm:px-4 sm:py-6">{children}</main>;
}
