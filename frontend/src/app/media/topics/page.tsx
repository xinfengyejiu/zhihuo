"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProduct } from "@/lib/product";
import { Badge, Button, Empty, ErrorText, Input, Select, Spinner, Textarea } from "@/components/ui";

interface Topic {
  id: number;
  title: string;
  summary: string | null;
  source: string;
  source_url: string | null;
  account_id: number | null;
  status: string;
  created_at: string;
}

interface Account {
  id: number;
  name: string;
}

interface RssSource {
  id: number;
  name: string;
  url: string;
  platform_hint: string | null;
  enabled: boolean;
  last_fetched_at: string | null;
}

export default function MediaTopicsPage() {
  const { user, loading: authLoading } = useAuth();
  const { setCurrent } = useProduct();
  const router = useRouter();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [rss, setRss] = useState<RssSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [manual, setManual] = useState({ title: "", summary: "", account_id: "" });
  const [rssForm, setRssForm] = useState({ name: "", url: "", platform_hint: "" });
  const [importText, setImportText] = useState("");

  const load = useCallback(async () => {
    const [t, a, r] = await Promise.all([
      api.get<Topic[]>("/media/topics?status=active"),
      api.get<Account[]>("/media/accounts"),
      api.get<RssSource[]>("/media/rss"),
    ]);
    setTopics(t);
    setAccounts(a);
    setRss(r);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (user) {
      setCurrent("media");
      load().catch((e) => setError(e instanceof Error ? e.message : "加载失败"));
    }
  }, [user, authLoading, router, load, setCurrent]);

  async function addManual() {
    if (!manual.title.trim()) return;
    setError("");
    try {
      await api.post("/media/topics", {
        title: manual.title.trim(),
        summary: manual.summary || null,
        account_id: manual.account_id ? +manual.account_id : null,
        source: "manual",
      });
      setManual({ title: "", summary: "", account_id: "" });
      setMsg("选题已添加");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "添加失败");
    }
  }

  async function importJson() {
    setError("");
    try {
      const parsed = JSON.parse(importText);
      const items = Array.isArray(parsed) ? parsed : parsed.items;
      if (!Array.isArray(items)) throw new Error("需要 JSON 数组或 {items:[...]}");
      const res = await api.post<{ created: number }>("/media/topics/import", {
        items: items.map((x: { title?: string; summary?: string; source_url?: string; platform_hint?: string }) => ({
          title: String(x.title || "").trim(),
          summary: x.summary || null,
          source: "import",
          source_url: x.source_url || null,
          platform_hint: x.platform_hint || null,
        })).filter((x: { title: string }) => x.title),
      });
      setMsg(`已导入 ${res.created} 条`);
      setImportText("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    }
  }

  async function addRss() {
    if (!rssForm.name.trim() || !rssForm.url.trim()) return;
    setError("");
    try {
      await api.post("/media/rss", {
        name: rssForm.name.trim(),
        url: rssForm.url.trim(),
        platform_hint: rssForm.platform_hint || null,
      });
      setRssForm({ name: "", url: "", platform_hint: "" });
      setMsg("RSS 已添加");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "添加失败");
    }
  }

  async function fetchRss(id: number) {
    setError("");
    try {
      const res = await api.post<{ created: number; seen: number }>(`/media/rss/${id}/fetch`);
      setMsg(`拉取完成:新增 ${res.created} / 见到 ${res.seen}`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "拉取失败");
    }
  }

  async function expand(id: number) {
    setError("");
    try {
      const res = await api.post<{ created: number }>(`/media/topics/${id}/expand`, { count: 5 });
      setMsg(`AI 扩写 ${res.created} 条`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "扩写失败");
    }
  }

  async function setStatus(id: number, status: string) {
    await api.put(`/media/topics/${id}/status?status=${status}`);
    load();
  }

  if (authLoading || loading) return <Spinner />;
  if (!user) return null;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h1 className="text-xl font-bold">选题库</h1>
        <p className="mt-0.5 text-xs text-slate-400">
          支持手动录入、RSS、JSON 导入与 AI 扩写。不做站内爬取。
        </p>
      </div>
      <ErrorText>{error}</ErrorText>
      {msg && <p className="text-xs text-emerald-600">{msg}</p>}

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold">手动新建</h3>
        <div className="grid gap-2">
          <Input value={manual.title} onChange={(e) => setManual({ ...manual, title: e.target.value })} placeholder="标题 *" />
          <Input value={manual.summary} onChange={(e) => setManual({ ...manual, summary: e.target.value })} placeholder="摘要" />
          <Select value={manual.account_id} onChange={(e) => setManual({ ...manual, account_id: e.target.value })} className="w-full">
            <option value="">不绑定账号</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>
          <Button onClick={addManual} disabled={!manual.title.trim()} className="w-fit">
            添加
          </Button>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold">RSS 源</h3>
        <div className="mb-3 grid gap-2 sm:grid-cols-[1fr_1.5fr_auto]">
          <Input value={rssForm.name} onChange={(e) => setRssForm({ ...rssForm, name: e.target.value })} placeholder="名称" />
          <Input value={rssForm.url} onChange={(e) => setRssForm({ ...rssForm, url: e.target.value })} placeholder="https://…/feed.xml" />
          <Button onClick={addRss} disabled={!rssForm.name.trim() || !rssForm.url.trim()}>
            添加源
          </Button>
        </div>
        {rss.length === 0 ? (
          <p className="text-xs text-slate-400">还没有 RSS</p>
        ) : (
          <ul className="space-y-2">
            {rss.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="font-medium">{r.name}</div>
                  <div className="truncate text-xs text-slate-400">{r.url}</div>
                </div>
                <Button size="sm" variant="outline" onClick={() => fetchRss(r.id)}>
                  立即拉取
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold">JSON / 榜单导入</h3>
        <p className="mb-2 text-xs text-slate-400">格式: [{'{'}&quot;title&quot;:&quot;…&quot;{'}'}] 或 {'{'}items:[…]{'}'}</p>
        <Textarea value={importText} onChange={(e) => setImportText(e.target.value)} rows={5} placeholder="粘贴 JSON…" />
        <Button className="mt-2" onClick={importJson} disabled={!importText.trim()}>
          导入
        </Button>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">活跃选题 ({topics.length})</h3>
        {topics.length === 0 ? (
          <Empty text="暂无选题" />
        ) : (
          <div className="space-y-2">
            {topics.map((t) => (
              <div key={t.id} className="rounded-xl border border-slate-200 bg-white p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{t.title}</span>
                      <Badge color="slate">{t.source}</Badge>
                    </div>
                    {t.summary && <p className="mt-1 text-xs text-slate-500">{t.summary}</p>}
                    {t.source_url && (
                      <a href={t.source_url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-xs text-indigo-500">
                        {t.source_url}
                      </a>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-1">
                    {t.account_id && (
                      <Link href={`/media/${t.account_id}`}>
                        <Button size="sm">去写作</Button>
                      </Link>
                    )}
                    <Button size="sm" variant="outline" onClick={() => expand(t.id)}>
                      AI 扩写
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setStatus(t.id, "ignored")}>
                      忽略
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
