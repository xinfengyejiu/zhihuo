"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Button, ErrorText, Input } from "@/components/ui";

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [nickname, setNickname] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await register(username.trim(), password, nickname.trim() || undefined);
      router.replace("/library");
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 pb-8 pt-[max(2rem,env(safe-area-inset-top))]">
      <h1 className="mb-1 text-xl font-bold">注册读者账号</h1>
      <p className="mb-6 text-xs text-black/40">仅用于阅读器,与创作台账号分开</p>
      <form onSubmit={onSubmit} className="space-y-3">
        <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="用户名(至少2位)" />
        <Input value={nickname} onChange={(e) => setNickname(e.target.value)} placeholder="昵称(可选)" />
        <Input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="密码(至少6位)"
        />
        <ErrorText>{error}</ErrorText>
        <Button type="submit" className="w-full" disabled={busy || username.length < 2 || password.length < 6}>
          {busy ? "注册中…" : "注册"}
        </Button>
      </form>
      <p className="mt-4 text-center text-sm text-black/45">
        已有账号?{" "}
        <Link href="/login" className="text-amber-700">
          登录
        </Link>
      </p>
    </div>
  );
}
