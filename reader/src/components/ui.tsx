import { ReactNode } from "react";

export function Spinner({ text }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-black/40">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-black/15 border-t-amber-600" />
      {text || "加载中…"}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="py-16 text-center text-sm text-black/40">{text}</div>;
}

export function ErrorText({ children }: { children?: ReactNode }) {
  if (!children) return null;
  return <p className="mt-1 text-xs text-red-600">{children}</p>;
}

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
  variant?: "primary" | "ghost" | "outline";
  size?: "sm" | "md";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const variants: Record<string, string> = {
    primary: "bg-[#e8772a] text-white hover:bg-[#d66a22] disabled:opacity-50",
    ghost: "text-black/60 hover:bg-black/5",
    outline: "border border-black/15 bg-white text-black/80 hover:bg-black/[0.03]",
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
      className={`rounded-full font-medium transition disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
    >
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-xl border border-black/10 bg-white px-3 py-2.5 text-sm outline-none focus:border-amber-500 ${props.className || ""}`}
    />
  );
}
