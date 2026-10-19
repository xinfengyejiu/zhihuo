"use client";

import { useMemo } from "react";
import { diffChars } from "diff";

export default function DiffView({
  oldText,
  newText,
  className = "max-h-[55vh] overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3",
}: {
  oldText: string;
  newText: string;
  className?: string;
}) {
  const parts = useMemo(() => diffChars(oldText || "", newText || ""), [oldText, newText]);
  return (
    <div className={`whitespace-pre-wrap text-sm leading-7 ${className}`}>
      {parts.map((p, i) => {
        if (p.added) {
          return (
            <span key={i} className="bg-emerald-100 text-emerald-800">
              {p.value}
            </span>
          );
        }
        if (p.removed) {
          return (
            <span key={i} className="bg-red-100 text-red-700 line-through">
              {p.value}
            </span>
          );
        }
        return <span key={i}>{p.value}</span>;
      })}
    </div>
  );
}
