"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useProduct } from "@/lib/product";
import { Spinner } from "@/components/ui";

export default function HubPage() {
  const { user, loading: authLoading } = useAuth();
  const { products, current, setCurrent, loading } = useProduct();
  const router = useRouter();

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [authLoading, user, router]);

  if (authLoading || loading) return <Spinner />;
  if (!user) return null;

  const last = products.find((p) => p.key === current);

  return (
    <div className="mx-auto max-w-3xl py-8">
      <div className="mb-8 text-center">
        <h1 className="text-2xl font-bold text-slate-800">选择要进入的产品</h1>
        <p className="mt-2 text-sm text-slate-500">模型、Skills、知识库在个人中心统一配置,各产品共用</p>
        {last && (
          <button
            type="button"
            className="mt-3 text-sm text-indigo-600 hover:underline"
            onClick={() => {
              setCurrent(last.key);
              router.push(last.href);
            }}
          >
            继续上次：{last.name} →
          </button>
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {products.map((p) => (
          <Link
            key={p.key}
            href={p.href}
            onClick={() => setCurrent(p.key)}
            className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:border-indigo-300 hover:shadow-md"
          >
            <h2 className="text-lg font-semibold text-indigo-600">{p.name}</h2>
            <p className="mt-2 text-sm leading-6 text-slate-500">{p.description}</p>
            <span className="mt-4 inline-block text-xs font-medium text-indigo-500">进入 →</span>
          </Link>
        ))}
      </div>
      <div className="mt-8 flex flex-wrap justify-center gap-4 text-sm">
        <Link href="/profile" className="text-slate-500 hover:text-indigo-600">
          个人中心 · 模型配置
        </Link>
        <Link href="/skills" className="text-slate-500 hover:text-indigo-600">
          Skills
        </Link>
        <Link href="/knowledge" className="text-slate-500 hover:text-indigo-600">
          知识库
        </Link>
      </div>
    </div>
  );
}
