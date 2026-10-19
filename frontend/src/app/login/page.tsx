"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, ErrorText, Input, Label } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");
    setBusy(true);
    try {
      if (mode === "login") {
        const data = await api.post<{ access_token: string }>("/auth/login", { username, password });
        await login(data.access_token);
      } else {
        const data = await api.post<{ access_token: string }>("/auth/register", {
          username,
          password,
          email: email || null,
        });
        await login(data.access_token);
      }
      router.push("/hub");
    } catch (e) {
      setError(e instanceof Error ? e.message : "出错了");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-16 max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-6 text-center">
        <h1 className="text-2xl font-bold text-indigo-600">织火</h1>
        <p className="mt-1 text-sm text-slate-500">多产品 AI 创作平台</p>
      </div>
      <div className="mb-4 flex rounded-lg bg-slate-100 p-1 text-sm">
        {(["login", "register"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`flex-1 rounded-md py-1.5 font-medium transition ${mode === m ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500"}`}
          >
            {m === "login" ? "登录" : "注册"}
          </button>
        ))}
      </div>
      <div className="space-y-3">
        <div>
          <Label>用户名</Label>
          <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="2-32 个字符" />
        </div>
        {mode === "register" && (
          <div>
            <Label>邮箱(可选)</Label>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="用于找回密码" />
          </div>
        )}
        <div>
          <Label>密码</Label>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="至少 6 位"
          />
        </div>
        <ErrorText>{error}</ErrorText>
        <Button onClick={submit} disabled={busy || !username || !password} className="w-full">
          {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并开始创作"}
        </Button>
      </div>
    </div>
  );
}
