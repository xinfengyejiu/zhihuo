"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Button, ErrorText, Input } from "@/components/ui";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
      router.replace("/library");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 pb-8 pt-[max(2rem,env(safe-area-inset-top))]">
      <h1 className="mb-1 text-xl font-bold">读者登录</h1>
      <p className="mb-6 text-xs text-black/40">与创作台账号独立 · 登录后可云端续读</p>
      <form onSubmit={onSubmit} className="space-y-3">
        <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="用户名" autoComplete="username" />
        <Input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="密码"
          autoComplete="current-password"
        />
        <ErrorText>{error}</ErrorText>
        <Button type="submit" className="w-full" disabled={busy || !username || !password}>
          {busy ? "登录中…" : "登录"}
        </Button>
      </form>
      <p className="mt-4 text-center text-sm text-black/45">
        没有账号?{" "}
        <Link href="/register" className="text-amber-700">
          注册
        </Link>
      </p>
    </div>
  );
}
