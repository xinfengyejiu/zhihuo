"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { readerNovelUrl } from "@/lib/api";

export default function NovelLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: { id: string };
}) {
  const pathname = usePathname();
  const novelId = params.id;
  const base = `/novels/${novelId}`;
  const [readerHref, setReaderHref] = useState(`/reader/${novelId}`);

  useEffect(() => {
    setReaderHref(readerNovelUrl(novelId));
  }, [novelId]);

  const items = [
    { href: base, label: "概览", exact: true, external: false },
    { href: `${base}/settings`, label: "设定库", exact: false, external: false },
    { href: `${base}/outline`, label: "大纲·章节", exact: false, external: false },
    { href: `${base}/chat`, label: "书内对话", exact: false, external: false },
    { href: readerHref, label: "阅读器", exact: false, external: true },
  ];

  return (
    <div className="flex flex-col gap-4 md:flex-row md:gap-6">
      <aside className="md:w-40 md:shrink-0">
        <nav className="flex gap-1 overflow-x-auto pb-1 md:sticky md:top-20 md:flex-col md:space-y-1 md:overflow-visible md:pb-0">
          {items.map((item) => {
            const active = !item.external && (item.exact ? pathname === item.href : pathname.startsWith(item.href));
            const className = `whitespace-nowrap rounded-full px-3 py-2 text-sm md:rounded-lg ${
              active ? "bg-indigo-50 font-medium text-indigo-700" : "text-slate-600 hover:bg-slate-100"
            }`;
            if (item.external) {
              return (
                <a key={item.label} href={item.href} target="_blank" rel="noreferrer" className={className}>
                  {item.label}
                </a>
              );
            }
            return (
              <Link key={item.href} href={item.href} className={className}>
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
