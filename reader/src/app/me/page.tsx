"use client";

import Link from "next/link";
import { getStudioBase } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Spinner } from "@/components/ui";

export default function MePage() {
  const { user, loading, logout } = useAuth();
  if (loading) return <Spinner />;

  return (
    <div className="mx-auto max-w-2xl px-4 pb-8 pt-[max(1rem,env(safe-area-inset-top))]">
      <h1 className="mb-4 text-xl font-bold">我的</h1>
      {user ? (
        <div className="space-y-4">
          <div className="rounded-2xl bg-white/80 p-4 ring-1 ring-black/5">
            <p className="text-lg font-semibold">{user.nickname || user.username}</p>
            <p className="text-xs text-black/40">@{user.username}</p>
          </div>
          <Link href="/library" className="block rounded-xl bg-black/[0.04] px-4 py-3 text-sm">
            我的书城
          </Link>
          <Button variant="outline" className="w-full" onClick={logout}>
            退出登录
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-black/50">登录后同步阅读进度、收藏与喜欢</p>
          <Link href="/login">
            <Button className="w-full">登录</Button>
          </Link>
          <Link href="/register">
            <Button variant="outline" className="w-full">
              注册读者账号
            </Button>
          </Link>
        </div>
      )}
      <a
        href={`${typeof window !== "undefined" ? getStudioBase() : "http://localhost:3000"}/shelf`}
        className="mt-8 block text-center text-xs text-black/35"
      >
        打开创作台 →
      </a>
    </div>
  );
}
