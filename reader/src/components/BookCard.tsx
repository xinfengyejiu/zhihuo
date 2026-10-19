"use client";

import Link from "next/link";
import type { NovelCard } from "@/lib/types";
import { CoverImg } from "./CoverImg";

export function BookCard({ book, subtitle }: { book: NovelCard; subtitle?: string }) {
  return (
    <Link href={`/${book.id}`} className="group block">
      <div className="aspect-[3/4] overflow-hidden rounded-lg bg-black/[0.04] shadow-sm ring-1 ring-black/5">
        <CoverImg url={book.cover} title={book.title} />
      </div>
      <h3 className="mt-2 line-clamp-2 text-sm font-medium leading-snug text-[#3b3a36] group-hover:text-amber-700">
        {book.title}
      </h3>
      <p className="mt-0.5 text-[11px] text-black/40">
        {subtitle ||
          (book.progress_chapter_title
            ? `续读 · ${book.progress_chapter_title}`
            : `${book.chapter_count} 章 · ${book.like_count} 喜欢`)}
      </p>
    </Link>
  );
}

export function BookRow({ books }: { books: NovelCard[] }) {
  if (!books.length) return null;
  return (
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6">
      {books.map((b) => (
        <BookCard key={b.id} book={b} />
      ))}
    </div>
  );
}
