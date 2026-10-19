"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { filterKbsForWorkshop, useProduct } from "@/lib/product";
import type { KbDocInfo, KbInfo } from "@/lib/types";
import { Badge, Button, Empty, ErrorText, Input, Label, Modal, Spinner, Textarea } from "@/components/ui";

const SCOPE_OPTIONS = [
  { key: "shared", label: "共享" },
  { key: "novel", label: "小说" },
  { key: "media", label: "自媒体" },
  { key: "drama", label: "漫剧" },
];

const PRODUCT_HINT: Record<string, string> = {
  novel: "在书的「概览 → AI 配置」里挂载后,AI 生成时会检索注入相关片段",
  media: "在自媒体账号工作台挂载后,聊天与成稿时会检索注入相关片段",
  drama: "在漫剧项目里可作创作参考;标签请勾选「漫剧」,避免与其它产品混在一起",
};

export default function KnowledgePage() {
  const { current } = useProduct();
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [docs, setDocs] = useState<KbDocInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [scopeTags, setScopeTags] = useState<string[]>(["shared"]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<{ content: string; score: number }[] | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const zipRef = useRef<HTMLInputElement>(null);

  const visibleKbs = useMemo(() => filterKbsForWorkshop(kbs, current), [kbs, current]);

  useEffect(() => {
    const el = folderRef.current;
    if (!el) return;
    el.setAttribute("webkitdirectory", "");
    el.setAttribute("directory", "");
  }, [activeId]);

  const load = useCallback(async () => {
    const q = current ? `?scope=${encodeURIComponent(current)}` : "";
    const list = await api.get<KbInfo[]>(`/kb${q}`);
    setKbs(list);
    setLoading(false);
  }, [current]);

  useEffect(() => {
    if (!activeId) return;
    if (!visibleKbs.some((k) => k.id === activeId)) {
      setActiveId(null);
      setDocs([]);
      setHits(null);
    }
  }, [visibleKbs, activeId]);

  const loadDocs = useCallback(async () => {
    if (!activeId) return setDocs([]);
    setDocs(await api.get<KbDocInfo[]>(`/kb/${activeId}/documents`));
  }, [activeId]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  function openCreate() {
    const tags = ["shared", ...(current && current !== "shared" ? [current] : [])];
    setScopeTags(Array.from(new Set(tags)));
    setCreateOpen(true);
  }

  async function createKb() {
    setError("");
    try {
      const kb = await api.post<KbInfo>("/kb", { name, description: desc || null, scope_tags: scopeTags });
      setCreateOpen(false);
      setName("");
      setDesc("");
      setScopeTags(["shared", ...(current && current !== "shared" ? [current] : [])]);
      await load();
      setActiveId(kb.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    }
  }

  async function upload(files: FileList | null) {
    if (!files || !activeId) return;
    setUploading(true);
    setError("");
    setMsg("");
    try {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        await api.upload(`/kb/${activeId}/upload`, form);
      }
      setMsg("上传完成:已解析分块入库(配置了 Embedding 模型则自动向量化)");
      await Promise.all([load(), loadDocs()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function importFolder(files: FileList | null) {
    if (!files?.length || !activeId) return;
    setUploading(true);
    setError("");
    setMsg("");
    try {
      const list = Array.from(files);
      const form = new FormData();
      const paths: string[] = [];
      for (const f of list) {
        const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
        paths.push(rel);
        form.append("files", f);
      }
      form.append("paths", JSON.stringify(paths));
      const res = await api.upload<{
        created: number;
        failed: number;
        skipped: number;
        errors?: { filename: string; error?: string }[];
      }>(`/kb/${activeId}/import-folder`, form);
      const parts = [`已导入 ${res.created} 个文件`];
      if (res.failed) parts.push(`失败 ${res.failed}`);
      if (res.skipped) parts.push(`跳过 ${res.skipped}(非支持格式)`);
      setMsg(parts.join(" · "));
      if (res.errors?.length) {
        setError(res.errors.map((e) => `${e.filename}:${e.error || "失败"}`).slice(0, 3).join("; "));
      }
      await Promise.all([load(), loadDocs()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "文件夹导入失败");
    } finally {
      setUploading(false);
      if (folderRef.current) folderRef.current.value = "";
    }
  }

  async function importZip(files: FileList | null) {
    const file = files?.[0];
    if (!file || !activeId) return;
    setUploading(true);
    setError("");
    setMsg("");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api.upload<{
        created: number;
        failed: number;
        skipped: number;
        errors?: { filename: string; error?: string }[];
      }>(`/kb/${activeId}/import-zip`, form);
      const parts = [`zip 已导入 ${res.created} 个文件`];
      if (res.failed) parts.push(`失败 ${res.failed}`);
      if (res.skipped) parts.push(`跳过 ${res.skipped}`);
      setMsg(parts.join(" · "));
      if (res.errors?.length) {
        setError(res.errors.map((e) => `${e.filename}:${e.error || "失败"}`).slice(0, 3).join("; "));
      }
      await Promise.all([load(), loadDocs()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "zip 导入失败");
    } finally {
      setUploading(false);
      if (zipRef.current) zipRef.current.value = "";
    }
  }

  async function search() {
    if (!activeId || !query.trim()) return;
    setError("");
    try {
      const res = await api.post<{ content: string; score: number }[]>(`/kb/${activeId}/search`, {
        query,
        top_k: 6,
      });
      setHits(res);
    } catch (e) {
      setHits([]);
      setError(e instanceof Error ? e.message : "检索失败");
    }
  }

  async function deleteDoc(docId: number) {
    if (!activeId || !confirm("确定删除该文档及其全部分块?")) return;
    await api.delete(`/kb/${activeId}/documents/${docId}`);
    await Promise.all([load(), loadDocs()]);
  }

  async function deleteKb(kbId: number) {
    if (!confirm("确定删除该知识库及全部文档?")) return;
    await api.delete(`/kb/${kbId}`);
    if (activeId === kbId) setActiveId(null);
    load();
  }

  if (loading) return <Spinner />;

  const active = visibleKbs.find((k) => k.id === activeId);
  const productLabel =
    current === "drama" ? "漫剧" : current === "media" ? "自媒体" : current === "novel" ? "小说" : "当前产品";

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">知识库</h1>
          <p className="mt-0.5 text-xs text-slate-400">
            按产品分割:当前仅显示「{productLabel}」与「共享」。上传 TXT/MD/PDF/DOCX/EPUB 后自动分块向量化。
            {current ? ` ${PRODUCT_HINT[current] || ""}` : ""}
          </p>
        </div>
        <Button onClick={openCreate}>+ 新建知识库</Button>
      </div>
      <ErrorText>{error}</ErrorText>

      {visibleKbs.length === 0 ? (
        <Empty text={`还没有「${productLabel}」或共享知识库`} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <div className="space-y-2">
            {visibleKbs.map((kb) => (
              <button
                key={kb.id}
                onClick={() => {
                  setActiveId(kb.id);
                  setHits(null);
                  setQuery("");
                }}
                className={`w-full rounded-xl border p-3 text-left transition ${activeId === kb.id ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-white hover:border-indigo-200"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-slate-700">{kb.name}</span>
                  <span
                    className="text-xs text-red-400 hover:text-red-600"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteKb(kb.id);
                    }}
                  >
                    删除
                  </span>
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {kb.document_count} 个文档 · {kb.chunk_count} 个片段
                  {(kb.scope_tags || []).length > 0 ? ` · ${(kb.scope_tags || []).join(",")}` : ""}
                </p>
              </button>
            ))}
          </div>

          <div className="space-y-4">
            {active ? (
              <>
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold">{active.name}</h3>
                    <div className="flex flex-wrap gap-2">
                      <input
                        ref={fileRef}
                        type="file"
                        multiple
                        accept=".txt,.md,.markdown,.pdf,.docx,.epub"
                        className="hidden"
                        onChange={(e) => upload(e.target.files)}
                      />
                      <input
                        ref={folderRef}
                        type="file"
                        multiple
                        className="hidden"
                        onChange={(e) => importFolder(e.target.files)}
                      />
                      <input
                        ref={zipRef}
                        type="file"
                        accept=".zip,application/zip"
                        className="hidden"
                        onChange={(e) => importZip(e.target.files)}
                      />
                      <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading}>
                        上传文档
                      </Button>
                      <Button size="sm" onClick={() => folderRef.current?.click()} disabled={uploading}>
                        {uploading ? "导入中…" : "导入文件夹"}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => zipRef.current?.click()} disabled={uploading}>
                        导入 zip
                      </Button>
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">
                    支持 TXT / MD / PDF / DOCX / EPUB;单文件 ≤ 10MB,文件夹/zip 合计 ≤ 50MB、最多 100 个文件。相对路径会保留为文档名。
                  </p>
                  {msg && <p className="mt-2 text-xs text-emerald-600">{msg}</p>}
                  <div className="mt-3 space-y-2">
                    {docs.length === 0 ? (
                      <p className="py-4 text-center text-xs text-slate-400">还没有文档</p>
                    ) : (
                      docs.map((d) => (
                        <div key={d.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                          <div className="min-w-0">
                            <span className="truncate font-medium text-slate-700">{d.filename}</span>
                            <span className="ml-2 text-xs text-slate-400">
                              {(d.size / 1024).toFixed(1)}KB · {d.chunk_count} 片段
                            </span>
                            {d.error && <div className="text-xs text-amber-600">{d.error}</div>}
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge color={d.status === "ready" ? "green" : "red"}>{d.status === "ready" ? "就绪" : "失败"}</Badge>
                            <button className="text-xs text-red-500 hover:underline" onClick={() => deleteDoc(d.id)}>
                              删除
                            </button>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <h3 className="mb-2 text-sm font-semibold">检索测试</h3>
                  <p className="mb-2 text-xs text-slate-400">输入一句话,看会命中哪些片段(与生成时注入的内容一致)</p>
                  <div className="flex gap-2">
                    <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="如:主角的功法叫什么" onKeyDown={(e) => e.key === "Enter" && search()} />
                    <Button onClick={search} disabled={!query.trim()}>
                      检索
                    </Button>
                  </div>
                  {hits !== null && (
                    <div className="mt-3 space-y-2">
                      {hits.length === 0 ? (
                        <p className="text-xs text-slate-400">没有命中(相关度过低或未向量化)</p>
                      ) : (
                        hits.map((h, i) => (
                          <div key={i} className="rounded-lg bg-slate-50 p-3">
                            <div className="mb-1 text-xs text-indigo-500">相关度 {h.score}</div>
                            <p className="whitespace-pre-wrap text-sm text-slate-600">{h.content}</p>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <Empty text="选择左侧知识库查看文档" />
            )}
          </div>
        </div>
      )}

      <Modal open={createOpen} title="新建知识库" onClose={() => setCreateOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>名称 *</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如:修真体系资料" />
          </div>
          <div>
            <Label>描述</Label>
            <Textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div>
            <Label>产品标签</Label>
            <p className="mb-1 text-[11px] text-slate-400">勾选所属产品;只勾「共享」则各产品都能看到</p>
            <div className="mt-1 flex flex-wrap gap-3 text-sm text-slate-600">
              {SCOPE_OPTIONS.map((opt) => (
                <label key={opt.key} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={scopeTags.includes(opt.key)}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...scopeTags, opt.key]
                        : scopeTags.filter((t) => t !== opt.key);
                      setScopeTags(next.length ? next : ["shared"]);
                    }}
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          </div>
          <Button onClick={createKb} disabled={!name.trim()} className="w-full">
            创建
          </Button>
        </div>
      </Modal>
    </div>
  );
}
