"use client";

import { ReactNode } from "react";

/** 桌面常驻侧栏;手机为可开关抽屉。 */
export function ChatSessionDrawer({
  open,
  onClose,
  children,
  className = "",
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <>
      {open && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          aria-label="关闭会话列表"
          onClick={onClose}
        />
      )}
      <aside
        className={`flex w-[min(16rem,85vw)] shrink-0 flex-col overflow-y-auto border-slate-200 bg-white ${
          open
            ? "fixed inset-y-0 left-0 z-50 border-r shadow-xl md:static md:z-auto md:shadow-none"
            : "hidden md:flex md:border-r"
        } ${className}`}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2 md:hidden">
          <span className="text-xs font-medium text-slate-500">会话</span>
          <button
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-50"
            onClick={onClose}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>
        {children}
      </aside>
    </>
  );
}
