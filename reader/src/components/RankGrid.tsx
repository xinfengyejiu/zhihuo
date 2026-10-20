"use client";

import Link from "next/link";
import type { NovelCard } from "@/lib/types";
import { CoverImg } from "./CoverImg";

function formatReads(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万人读过`;
  if (n > 0) return `${n}人读过`;
  return "";
}

export function RankGrid({ books, offset = 0 }: { books: NovelCard[]; offset?: number }) {
  if (!books.length) return null;
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-4">
      {books.map((b, i) => {
        const rank = offset + i + 1;
        const tag = b.tags?.[0];
        return (
          <Link key={b.id} href={`/${b.id}`} className="flex gap-2.5">
            <div className="relative h-[4.75rem] w-[3.4rem] shrink-0 overflow-hidden rounded-md bg-black/[0.04] ring-1 ring-black/5">
              <CoverImg url={b.cover} title={b.title} />
            </div>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex items-start gap-1.5">
                <span
                  className={`shrink-0 text-lg font-bold leading-none ${
                    rank <= 3 ? "text-[#e8772a]" : "text-black/25"
                  }`}
                >
                  {rank}
                </span>
                <div className="min-w-0">
                  <h3 className="line-clamp-2 text-[13px] font-semibold leading-snug text-[#3b3a36]">
                    {b.title}
                  </h3>
                  <p className="mt-1 line-clamp-1 text-[11px] text-black/40">
                    {tag || formatReads(b.read_count) || `${b.chapter_count}章`}
                  </p>
                </div>
              </div>
            </div>
          </Link>
        );
      })}
    </div>
  );
}

export function FeaturedRow({ books }: { books: NovelCard[] }) {
  if (!books.length) return null;
  return (
    <div className="-mx-4 flex gap-3 overflow-x-auto px-4 pb-1 scrollbar-none">
      {books.map((b) => (
        <Link key={b.id} href={`/${b.id}`} className="w-[7.5rem] shrink-0">
          <div className="relative aspect-[3/4] overflow-hidden rounded-lg bg-black/[0.04] shadow-sm ring-1 ring-black/5">
            <CoverImg url={b.cover} title={b.title} />
            {b.read_count > 0 && (
              <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/65 to-transparent px-1.5 pb-1.5 pt-5">
                <p className="truncate text-[10px] text-white/90">{formatReads(b.read_count)}</p>
              </div>
            )}
          </div>
          <h3 className="mt-1.5 line-clamp-2 text-[12px] font-medium leading-snug text-[#3b3a36]">
            {b.title}
          </h3>
          {b.tags?.[0] && <p className="mt-0.5 truncate text-[10px] text-black/35">{b.tags[0]}</p>}
        </Link>
      ))}
    </div>
  );
}
