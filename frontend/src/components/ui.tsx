"use client";

import { ReactNode } from "react";

export function Button({
  children,
  onClick,
  variant = "primary",
  size = "md",
  disabled,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const variants: Record<string, string> = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-indigo-300",
    ghost: "text-slate-600 hover:bg-slate-100",
    danger: "bg-red-600 text-white hover:bg-red-700 disabled:bg-red-300",
    outline: "border border-slate-300 text-slate-700 hover:bg-slate-50 bg-white",
  };
  const sizes: Record<string, string> = {
    sm: "px-2.5 py-1 text-xs",
    md: "px-4 py-2 text-sm",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg font-medium transition-colors disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
    >
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 ${props.className || ""}`}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 ${props.className || ""}`}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={`rounded-lg border border-slate-300 px-2 py-2 text-sm outline-none focus:border-indigo-500 ${props.className || ""}`}
    />
  );
}

export function Label({ children }: { children: ReactNode }) {
  return <label className="mb-1 block text-xs font-medium text-slate-500">{children}</label>;
}

export function Modal({
  open,
  title,
  onClose,
  children,
  wide,
  tall,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  tall?: boolean;
}) {
  if (!open) return null;
  // 只能通过标题栏 ✕ 或表单里的 保存/取消 按钮关闭,点击遮罩不关闭,避免误触丢内容
  // 手机:<md 近全屏;桌面保持原宽高
  const panelSize =
    wide || tall
      ? "h-dvh max-h-dvh w-full max-w-none rounded-none p-4 pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))] md:h-[min(92vh,900px)] md:max-h-[92vh] md:max-w-6xl md:rounded-xl md:p-5"
      : "max-h-[min(92dvh,88vh)] w-full max-w-lg rounded-xl p-4 sm:p-5";
  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/40 p-0 md:items-center md:p-4">
      <div className={`flex w-full flex-col overflow-hidden bg-white shadow-xl ${panelSize}`}>
        <div className="mb-3 flex shrink-0 items-center justify-between gap-2 md:mb-4">
          <h3 className="min-w-0 truncate text-base font-semibold text-slate-800">{title}</h3>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-50 hover:text-slate-600"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>
        <div className={`min-h-0 ${tall || wide ? "flex flex-1 flex-col overflow-hidden" : "overflow-auto"}`}>
          {children}
        </div>
      </div>
    </div>
  );
}

export function Badge({ children, color = "slate" }: { children: ReactNode; color?: string }) {
  const colors: Record<string, string> = {
    slate: "bg-slate-100 text-slate-600",
    green: "bg-emerald-100 text-emerald-700",
    amber: "bg-amber-100 text-amber-700",
    red: "bg-red-100 text-red-700",
    blue: "bg-sky-100 text-sky-700",
    indigo: "bg-indigo-100 text-indigo-700",
  };
  return <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${colors[color] || colors.slate}`}>{children}</span>;
}

export const CHAPTER_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "slate" },
  pending_review: { label: "待审核", color: "amber" },
  approved: { label: "审核通过", color: "green" },
  scheduled: { label: "已排期", color: "blue" },
  published: { label: "已发布", color: "green" },
  rejected: { label: "被驳回", color: "red" },
};

export function ChapterStatusBadge({ status }: { status: string }) {
  const meta = CHAPTER_STATUS_LABELS[status] || { label: status, color: "slate" };
  return <Badge color={meta.color}>{meta.label}</Badge>;
}

export function Spinner({ text }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-500" />
      {text || "加载中…"}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="py-16 text-center text-sm text-slate-400">{text}</div>;
}

export function ErrorText({ children }: { children?: ReactNode }) {
  if (!children) return null;
  return <p className="mt-1 text-xs text-red-600">{children}</p>;
}
