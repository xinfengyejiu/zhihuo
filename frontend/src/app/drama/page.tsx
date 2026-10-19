"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProduct } from "@/lib/product";
import type { DramaProject, Novel } from "@/lib/types";
import { Badge, Button, Empty, ErrorText, Input, Label, Modal, Select, Spinner, Textarea } from "@/components/ui";

const SOURCE_LABEL: Record<string, string> = {
  novel: "已有小说",
  upload: "上传剧本",
  paste: "粘贴文本",
};

const STATUS_META: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "slate" },
  generating: { label: "生成中", color: "amber" },
  scripted: { label: "已出剧本", color: "blue" },
  ready: { label: "已有首帧", color: "green" },
};

export default function DramaListPage() {
  const { user, loading: authLoading } = useAuth();
  const { setCurrent } = useProduct();
  const router = useRouter();
  const [items, setItems] = useState<DramaProject[]>([]);
  const [novels, setNovels] = useState<Novel[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    title: "",
    source_type: "novel",
    novel_id: "",
    source_text: "",
    style_prompt: "",
    episode_count: 5,
  });

  const load = useCallback(async () => {
    const [ps, ns] = await Promise.all([api.get<DramaProject[]>("/drama/projects"), api.get<Novel[]>("/novels")]);
    setItems(ps);
    setNovels(ns);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (user) {
      setCurrent("drama");
      load().catch((e) => {
        setError(e instanceof Error ? e.message : "加载失败");
        setLoading(false);
      });
    }
  }, [authLoading, user, load, router, setCurrent]);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const payload: Record<string, unknown> = {
        title: form.title || undefined,
        source_type: form.source_type,
        style_prompt: form.style_prompt || undefined,
        episode_count: form.episode_count,
      };
      if (form.source_type === "novel") {
        if (!form.novel_id) throw new Error("请选择一部小说");
        payload.novel_id = +form.novel_id;
      } else if (form.source_type === "paste") {
        if ((form.source_text || "").trim().length < 40) throw new Error("请粘贴至少约 40 字的故事或剧本");
        payload.source_text = form.source_text;
      }
      const created = await api.post<DramaProject>("/drama/projects", payload);
      setOpen(false);
      router.push(`/drama/${created.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!confirm("删除该漫剧项目？")) return;
    await api.delete(`/drama/projects/${id}`);
    setItems((xs) => xs.filter((x) => x.id !== id));
  }

  if (authLoading || loading) return <Spinner />;
  if (!user) return null;

  return (
    <div className="mx-auto max-w-5xl py-6">
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">漫剧创作</h1>
          <p className="mt-1 text-xs text-slate-500">
            从已有小说或上传/粘贴剧本生成分集圣经、分镜与首帧。对话可随时改剧。
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>新建项目</Button>
      </div>
      <ErrorText>{error}</ErrorText>
      {items.length === 0 ? (
        <Empty text="还没有漫剧项目,点右上角新建" />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map((p) => {
            const st = STATUS_META[p.status] || STATUS_META.draft;
            return (
              <div key={p.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <Link href={`/drama/${p.id}`} className="font-medium text-indigo-600 hover:underline">
                    {p.title}
                  </Link>
                  <Badge color={st.color}>{st.label}</Badge>
                </div>
                <p className="text-xs text-slate-500">
                  {SOURCE_LABEL[p.source_type] || p.source_type} · {p.episode_count} 集
                  {p.shot_count ? ` · ${p.ready_count}/${p.shot_count} 首帧` : ""}
                </p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => router.push(`/drama/${p.id}`)}>
                    打开
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => remove(p.id)}>
                    删除
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Modal open={open} title="新建漫剧项目" onClose={() => setOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>标题</Label>
            <Input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="可留空,从小说/剧本自动带出"
            />
          </div>
          <div>
            <Label>来源</Label>
            <Select
              className="mt-1 w-full"
              value={form.source_type}
              onChange={(e) => setForm({ ...form, source_type: e.target.value })}
            >
              <option value="novel">选择已有小说</option>
              <option value="paste">粘贴剧本/故事</option>
              <option value="upload">先建空项目,稍后上传文件</option>
            </Select>
          </div>
          {form.source_type === "novel" && (
            <div>
              <Label>小说</Label>
              <Select
                className="mt-1 w-full"
                value={form.novel_id}
                onChange={(e) => setForm({ ...form, novel_id: e.target.value })}
              >
                <option value="">请选择</option>
                {novels.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.title}
                  </option>
                ))}
              </Select>
            </div>
          )}
          {form.source_type === "paste" && (
            <div>
              <Label>故事或剧本</Label>
              <Textarea
                rows={8}
                value={form.source_text}
                onChange={(e) => setForm({ ...form, source_text: e.target.value })}
                placeholder="粘贴小说片段或分场剧本…"
              />
            </div>
          )}
          <div>
            <Label>画风(可选,可沿用小说全书画风)</Label>
            <Input
              value={form.style_prompt}
              onChange={(e) => setForm({ ...form, style_prompt: e.target.value })}
              placeholder="如:中国动画风格,二维赛璐璐"
            />
          </div>
          <div>
            <Label>集数</Label>
            <Select
              className="mt-1"
              value={form.episode_count}
              onChange={(e) => setForm({ ...form, episode_count: +e.target.value })}
            >
              {[3, 4, 5, 6, 8].map((n) => (
                <option key={n} value={n}>
                  {n} 集
                </option>
              ))}
            </Select>
          </div>
          <ErrorText>{error}</ErrorText>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button onClick={create} disabled={busy}>
              {busy ? "创建中…" : "创建并打开"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
