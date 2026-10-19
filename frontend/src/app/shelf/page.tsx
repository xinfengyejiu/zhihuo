"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, getApiBase, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Novel, Tag } from "@/lib/types";
import { PLATFORMS, PLATFORM_LABEL } from "@/lib/types";
import { Badge, Button, Empty, ErrorText, Input, Label, Modal, Select, Spinner, Textarea } from "@/components/ui";

function ShelfCoverThumb({ url, title }: { url: string | null; title: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    if (!url) {
      setSrc("");
      return;
    }
    let revoked: string | null = null;
    let cancelled = false;
    const base = getApiBase().replace(/\/api\/?$/, "");
    const resolved =
      url.startsWith("http") || url.startsWith("data:")
        ? url
        : url.startsWith("/gen-assets/")
          ? `${base}/api${url}`
          : url.startsWith("/")
            ? `${base}${url}`
            : url;
    if (!url.includes("/gen-assets/")) {
      setSrc(resolved);
      return;
    }
    const token = getToken();
    fetch(resolved, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(async (r) => {
        if (!r.ok) throw new Error("fail");
        const blob = await r.blob();
        if (cancelled) return;
        revoked = URL.createObjectURL(blob);
        setSrc(revoked);
      })
      .catch(() => {
        if (!cancelled) setSrc(resolved);
      });
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [url]);
  return (
    <div className="mb-3 aspect-[3/4] w-full overflow-hidden rounded-lg border border-slate-100 bg-slate-50">
      {src ? (
        <img src={src} alt={`${title}封面`} className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full items-center justify-center text-[11px] text-slate-300">暂无封面</div>
      )}
    </div>
  );
}

const TAG_TYPE_LABELS: Record<string, string> = { genre: "题材", style: "风格", status: "状态", custom: "自定义" };
const NOVEL_STATUS_LABELS: Record<string, string> = { draft: "构思中", serializing: "连载中", completed: "已完本" };

export default function BookshelfPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [novels, setNovels] = useState<Novel[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importTitle, setImportTitle] = useState("");
  const [importUseAi, setImportUseAi] = useState(true);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<string>("");
  const [editTarget, setEditTarget] = useState<Novel | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Novel | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [filterTag, setFilterTag] = useState<number | null>(null);
  const [tagsManageOpen, setTagsManageOpen] = useState(false);

  // 建书表单
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [targetWords, setTargetWords] = useState("");
  const [status, setStatus] = useState("draft");
  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [newTagName, setNewTagName] = useState("");
  const [newTagType, setNewTagType] = useState("genre");

  const load = useCallback(async () => {
    try {
      const [n, t] = await Promise.all([api.get<Novel[]>("/novels"), api.get<Tag[]>("/tags")]);
      setNovels(n);
      setTags(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (user) load();
  }, [user, authLoading, router, load]);

  async function createNovel() {
    setError("");
    try {
      await api.post<Novel>("/novels", {
        title,
        summary: summary || null,
        status,
        target_words: targetWords ? parseInt(targetWords) : null,
        tag_ids: selectedTagIds,
        platforms: selectedPlatforms,
      });
      setCreateOpen(false);
      setTitle("");
      setSummary("");
      setTargetWords("");
      setSelectedTagIds([]);
      setSelectedPlatforms([]);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    }
  }

  async function importNovel() {
    if (!importFile || importing) return;
    setError("");
    setImportResult("");
    setImporting(true);
    try {
      const form = new FormData();
      form.append("file", importFile);
      if (importTitle.trim()) form.append("title", importTitle.trim());
      form.append("use_ai", importUseAi ? "true" : "false");
      const res = await api.upload<{
        novel_id: number;
        title: string;
        volumes_created: number;
        chapters_created: number;
        word_count: number;
        message?: string;
      }>("/novels/import-txt", form);
      setImportResult(
        res.message ||
          `已导入《${res.title}》: ${res.volumes_created} 卷 / ${res.chapters_created} 章 / ${res.word_count} 字`
      );
      await load();
      setTimeout(() => {
        setImportOpen(false);
        setImportFile(null);
        setImportTitle("");
        setImportResult("");
        router.push(`/novels/${res.novel_id}/outline`);
      }, 800);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
    }
  }

  async function addTag() {
    if (!newTagName.trim()) return;
    try {
      const t = await api.post<Tag>("/tags", { name: newTagName.trim(), type: newTagType });
      setTags((prev) => [...prev, t]);
      setNewTagName("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "标签创建失败");
    }
  }

  async function confirmDeleteNovel() {
    if (!deleteTarget) return;
    if (deleteConfirmText.trim() !== deleteTarget.title) {
      setError("请完整输入书名以确认删除");
      return;
    }
    setDeleting(true);
    setError("");
    try {
      await api.delete(`/novels/${deleteTarget.id}`);
      setDeleteTarget(null);
      setDeleteConfirmText("");
      setEditTarget(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  function toggleTag(id: number) {
    setSelectedTagIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  if (authLoading || loading) return <Spinner />;
  const shown = filterTag ? novels.filter((n) => n.tags.some((t) => t.id === filterTag)) : novels;

  return (
    <div>
      <div className="mb-5 flex items-center justify-between gap-3">
        <h1 className="text-xl font-bold">我的书架</h1>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            onClick={() => {
              setImportOpen(true);
              setError("");
              setImportResult("");
            }}
          >
            导入小说
          </Button>
          <Button onClick={() => setCreateOpen(true)}>+ 新建小说</Button>
        </div>
      </div>

      <div className="mb-5 flex flex-wrap items-center gap-2">
        <button
          onClick={() => setFilterTag(null)}
          className={`rounded-full px-3 py-1 text-xs ${filterTag === null ? "bg-indigo-600 text-white" : "bg-white text-slate-600 border border-slate-200"}`}
        >
          全部
        </button>
        {tags.map((t) => (
          <button
            key={t.id}
            onClick={() => setFilterTag(filterTag === t.id ? null : t.id)}
            className={`rounded-full px-3 py-1 text-xs ${filterTag === t.id ? "bg-indigo-600 text-white" : "bg-white text-slate-600 border border-slate-200"}`}
          >
            {t.name}
            <span className="ml-1 opacity-60">{TAG_TYPE_LABELS[t.type] || t.type}</span>
          </button>
        ))}
        <button
          type="button"
          onClick={() => setTagsManageOpen(true)}
          className="rounded-full border border-dashed border-slate-300 px-3 py-1 text-xs text-slate-500 hover:border-indigo-400 hover:text-indigo-600"
        >
          管理标签
        </button>
      </div>

      <ErrorText>{error}</ErrorText>

      <Modal
        open={!!deleteTarget}
        title="删除整部小说"
        onClose={() => {
          if (deleting) return;
          setDeleteTarget(null);
          setDeleteConfirmText("");
        }}
      >
        {deleteTarget && (
          <div className="space-y-3">
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-xs leading-5 text-red-700">
              <p className="font-semibold">此操作不可恢复，将永久清空该书的全部数据，包括：</p>
              <ul className="mt-1.5 list-disc space-y-0.5 pl-4">
                <li>全部章节正文与历史版本</li>
                <li>卷/大纲文档、人物、世界观、规则、伏笔</li>
                <li>书内 AI 对话记录与前情摘要</li>
                <li>发布任务、平台进度、协作与挂载资源</li>
              </ul>
            </div>
            <p className="text-sm text-slate-600">
              即将删除：<strong className="text-slate-900">《{deleteTarget.title}》</strong>
            </p>
            <div>
              <Label>请输入书名以确认删除 *</Label>
              <Input
                value={deleteConfirmText}
                onChange={(e) => setDeleteConfirmText(e.target.value)}
                placeholder={deleteTarget.title}
                disabled={deleting}
                autoFocus
              />
            </div>
            <ErrorText>{error}</ErrorText>
            <div className="flex justify-end gap-2 pt-1">
              <Button
                variant="outline"
                disabled={deleting}
                onClick={() => {
                  setDeleteTarget(null);
                  setDeleteConfirmText("");
                }}
              >
                取消
              </Button>
              <Button
                variant="danger"
                disabled={deleting || deleteConfirmText.trim() !== deleteTarget.title}
                onClick={confirmDeleteNovel}
              >
                {deleting ? "删除中…" : "确认删除全部数据"}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {shown.length === 0 ? (
        <Empty text="书架空空如也,点右上角开始创作吧" />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((n) => {
            const pct = n.target_words ? Math.min(100, Math.round((n.current_words / n.target_words) * 100)) : 0;
            return (
              <div
                key={n.id}
                className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-indigo-300 hover:shadow-md"
              >
                <Link href={`/novels/${n.id}`} className="group block">
                  <ShelfCoverThumb url={n.cover} title={n.title} />
                  <div className="flex items-start justify-between">
                    <h3 className="font-semibold text-slate-800 group-hover:text-indigo-600">{n.title}</h3>
                    <Badge color={n.status === "serializing" ? "green" : n.status === "completed" ? "blue" : "slate"}>
                      {NOVEL_STATUS_LABELS[n.status] || n.status}
                    </Badge>
                  </div>
                  <p className="mt-2 line-clamp-2 min-h-[2.5rem] text-xs text-slate-500">{n.summary || "暂无简介"}</p>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-xs text-slate-400">
                    <span>
                      {n.current_words.toLocaleString()}
                      {n.target_words ? ` / ${n.target_words.toLocaleString()} 字` : " 字"}
                    </span>
                    <div className="flex gap-1">
                      {n.my_role && n.my_role !== "owner" && <Badge color="indigo">协作</Badge>}
                      {n.tags.map((t) => (
                        <Badge key={t.id}>{t.name}</Badge>
                      ))}
                    </div>
                  </div>
                  {n.platforms.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1 border-t border-slate-50 pt-2 text-xs text-slate-400">
                      {n.platforms.map((p) => (
                        <span key={p.platform} className="rounded bg-slate-50 px-1.5 py-0.5">
                          {PLATFORM_LABEL[p.platform] || p.platform} · 已发{p.published_chapter_no}章
                        </span>
                      ))}
                    </div>
                  )}
                </Link>
                <div className="mt-2 flex items-center justify-end gap-3 border-t border-slate-50 pt-2 text-xs">
                  <button className="text-slate-400 transition hover:text-indigo-600" onClick={() => setEditTarget(n)}>
                    ✎ 编辑
                  </button>
                  {n.my_role === "owner" && (
                    <button
                      className="text-slate-400 transition hover:text-red-500"
                      onClick={() => {
                        setError("");
                        setDeleteConfirmText("");
                        setDeleteTarget(n);
                      }}
                    >
                      删除
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Modal
        open={importOpen}
        title="导入小说(TXT / Markdown)"
        onClose={() => !importing && setImportOpen(false)}
      >
        <div className="space-y-3">
          <p className="text-xs leading-5 text-slate-500">
            支持 .txt / .md。先拆分再并行清洗、按序合并。会忽略不足 100 字的目录行、作者说与重复章。超过约
            800KB 的大文件会自动关闭 AI 以加快导入。
          </p>
          <div>
            <Label>选择文件 *</Label>
            <input
              type="file"
              accept=".txt,.md,.markdown,text/plain,text/markdown"
              disabled={importing}
              className="mt-1 block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setImportFile(f);
                if (f && !importTitle.trim()) {
                  setImportTitle(f.name.replace(/\.(txt|text|md|markdown)$/i, ""));
                }
                // 大文件默认关 AI,避免卡住
                if (f && f.size > 800 * 1024) {
                  setImportUseAi(false);
                }
              }}
            />
            {importFile && (
              <p className="mt-1 text-xs text-slate-400">
                {importFile.name} · {(importFile.size / 1024).toFixed(1)} KB
                {importFile.size > 800 * 1024 ? " · 大文件将跳过 AI 以加快导入" : ""}
              </p>
            )}
          </div>
          <div>
            <Label>书名(可选,默认从文件推断)</Label>
            <Input
              value={importTitle}
              disabled={importing}
              onChange={(e) => setImportTitle(e.target.value)}
              placeholder="留空则自动识别"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={importUseAi}
              disabled={importing || (!!importFile && importFile.size > 800 * 1024)}
              onChange={(e) => setImportUseAi(e.target.checked)}
            />
            用 AI 补全书名与简介
            {importFile && importFile.size > 800 * 1024 ? (
              <span className="text-xs text-slate-400">(大文件已禁用)</span>
            ) : null}
          </label>
          {importing && (
            <p className="text-xs text-indigo-600">
              正在解析并写入章节,大部头可能需要几十秒,请勿关闭页面…
            </p>
          )}
          {importResult && <p className="text-sm text-emerald-600">{importResult}</p>}
          <ErrorText>{error}</ErrorText>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" disabled={importing} onClick={() => setImportOpen(false)}>
              取消
            </Button>
            <Button onClick={importNovel} disabled={!importFile || importing}>
              {importing ? "解析导入中…" : "开始导入"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={createOpen} title="新建小说" onClose={() => setCreateOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>书名 *</Label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如:星海剑歌" />
          </div>
          <div>
            <Label>简介</Label>
            <Textarea rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="一句话介绍这本书" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>状态</Label>
              <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-full">
                {Object.entries(NOVEL_STATUS_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label>目标字数</Label>
              <Input value={targetWords} onChange={(e) => setTargetWords(e.target.value.replace(/\D/g, ""))} placeholder="如 2000000" />
            </div>
          </div>
          <div>
            <Label>标签(可多选)</Label>
            <div className="flex flex-wrap gap-2">
              {tags.map((t) => (
                <button
                  key={t.id}
                  onClick={() => toggleTag(t.id)}
                  className={`rounded-full px-2.5 py-1 text-xs ${selectedTagIds.includes(t.id) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                  {t.name}
                </button>
              ))}
              {tags.length === 0 && <span className="text-xs text-slate-400">还没有标签,先在下面创建</span>}
            </div>
            <div className="mt-2 flex gap-2">
              <Select value={newTagType} onChange={(e) => setNewTagType(e.target.value)}>
                {Object.entries(TAG_TYPE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
              <Input value={newTagName} onChange={(e) => setNewTagName(e.target.value)} placeholder="新标签名" className="flex-1" />
              <Button variant="outline" onClick={addTag}>
                添加
              </Button>
            </div>
          </div>
          <div>
            <Label>发布平台(可多选,发布中心可改)</Label>
            <div className="flex flex-wrap gap-2">
              {PLATFORMS.map((p) => (
                <button
                  key={p.key}
                  onClick={() =>
                    setSelectedPlatforms((prev) =>
                      prev.includes(p.key) ? prev.filter((x) => x !== p.key) : [...prev, p.key]
                    )
                  }
                  className={`rounded-full px-2.5 py-1 text-xs ${selectedPlatforms.includes(p.key) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <Button onClick={createNovel} disabled={!title.trim()} className="w-full">
            创建
          </Button>
        </div>
      </Modal>

      <EditNovelModal novel={editTarget} tags={tags} onClose={() => setEditTarget(null)} onSaved={load} />

      <TagsManageModal
        open={tagsManageOpen}
        tags={tags}
        onClose={() => setTagsManageOpen(false)}
        onChanged={async () => {
          await load();
        }}
        onDeleted={(id) => {
          if (filterTag === id) setFilterTag(null);
          setSelectedTagIds((prev) => prev.filter((x) => x !== id));
        }}
      />
    </div>
  );
}

function TagsManageModal({
  open,
  tags,
  onClose,
  onChanged,
  onDeleted,
}: {
  open: boolean;
  tags: Tag[];
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  onDeleted: (id: number) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState("genre");
  const [editing, setEditing] = useState<Tag | null>(null);
  const [editName, setEditName] = useState("");
  const [editType, setEditType] = useState("genre");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) {
      setName("");
      setType("genre");
      setEditing(null);
      setError("");
    }
  }, [open]);

  async function createTag() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.post<Tag>("/tags", { name: name.trim(), type });
      setName("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  function startEdit(t: Tag) {
    setEditing(t);
    setEditName(t.name);
    setEditType(t.type);
    setError("");
  }

  async function saveEdit() {
    if (!editing || !editName.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.put<Tag>(`/tags/${editing.id}`, { name: editName.trim(), type: editType });
      setEditing(null);
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeTag(t: Tag) {
    if (!confirm(`确定删除标签「${t.name}」?已关联小说上的该标签也会一并移除。`)) return;
    setBusy(true);
    setError("");
    try {
      await api.delete(`/tags/${t.id}`);
      if (editing?.id === t.id) setEditing(null);
      onDeleted(t.id);
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} title="管理标签" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-xs leading-5 text-slate-500">可新增、重命名、改类型或删除标签。删除后书架筛选与书上的关联会同步更新。</p>
        <ErrorText>{error}</ErrorText>

        <div>
          <Label>新建标签</Label>
          <div className="mt-1 flex flex-wrap gap-2">
            <Select value={type} onChange={(e) => setType(e.target.value)} disabled={busy}>
              {Object.entries(TAG_TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="标签名,如 修仙"
              className="min-w-[10rem] flex-1"
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter") createTag();
              }}
            />
            <Button variant="outline" onClick={createTag} disabled={busy || !name.trim()}>
              添加
            </Button>
          </div>
        </div>

        <div>
          <Label>已有标签({tags.length})</Label>
          {tags.length === 0 ? (
            <p className="mt-2 text-xs text-slate-400">还没有标签</p>
          ) : (
            <ul className="mt-2 max-h-72 space-y-2 overflow-y-auto">
              {tags.map((t) => (
                <li key={t.id} className="rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2">
                  {editing?.id === t.id ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Select value={editType} onChange={(e) => setEditType(e.target.value)} disabled={busy}>
                        {Object.entries(TAG_TYPE_LABELS).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </Select>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="min-w-[8rem] flex-1"
                        disabled={busy}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") saveEdit();
                        }}
                      />
                      <button type="button" className="text-xs text-indigo-600 hover:underline" disabled={busy} onClick={saveEdit}>
                        保存
                      </button>
                      <button type="button" className="text-xs text-slate-500 hover:underline" disabled={busy} onClick={() => setEditing(null)}>
                        取消
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm text-slate-800">
                        {t.name}
                        <span className="ml-2 text-xs text-slate-400">{TAG_TYPE_LABELS[t.type] || t.type}</span>
                      </span>
                      <button type="button" className="shrink-0 text-xs text-indigo-600 hover:underline" disabled={busy} onClick={() => startEdit(t)}>
                        编辑
                      </button>
                      <button type="button" className="shrink-0 text-xs text-red-500 hover:underline" disabled={busy} onClick={() => removeTag(t)}>
                        删除
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Modal>
  );
}

function EditNovelModal({
  novel,
  tags,
  onClose,
  onSaved,
}: {
  novel: Novel | null;
  tags: Tag[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({ title: "", summary: "", status: "draft", target_words: "" });
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (novel) {
      setForm({
        title: novel.title,
        summary: novel.summary || "",
        status: novel.status,
        target_words: novel.target_words ? String(novel.target_words) : "",
      });
      setTagIds(novel.tags.map((t) => t.id));
      setPlatforms(novel.platforms.map((p) => p.platform));
      setError("");
    }
  }, [novel]);

  async function save() {
    if (!novel) return;
    setError("");
    try {
      await api.put(`/novels/${novel.id}`, {
        title: form.title,
        summary: form.summary || null,
        status: form.status,
        target_words: form.target_words ? parseInt(form.target_words) : null,
        tag_ids: tagIds,
        platforms,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  return (
    <Modal open={!!novel} title={novel ? `编辑《${novel.title}》` : "编辑"} onClose={onClose} wide>
      {novel && (
        <div className="space-y-3">
          <div>
            <Label>书名 *</Label>
            <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>
          <div>
            <Label>简介</Label>
            <Textarea rows={3} value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>状态</Label>
              <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="w-full">
                {Object.entries(NOVEL_STATUS_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label>目标字数</Label>
              <Input
                value={form.target_words}
                onChange={(e) => setForm({ ...form, target_words: e.target.value.replace(/\D/g, "") })}
              />
            </div>
          </div>
          <div>
            <Label>标签(可多选)</Label>
            <div className="flex flex-wrap gap-2">
              {tags.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTagIds((prev) => (prev.includes(t.id) ? prev.filter((x) => x !== t.id) : [...prev, t.id]))}
                  className={`rounded-full px-2.5 py-1 text-xs ${tagIds.includes(t.id) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                  {t.name}
                </button>
              ))}
              {tags.length === 0 && <span className="text-xs text-slate-400">还没有标签</span>}
            </div>
          </div>
          <div>
            <Label>发布平台</Label>
            <div className="flex flex-wrap gap-2">
              {PLATFORMS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => setPlatforms((prev) => (prev.includes(p.key) ? prev.filter((x) => x !== p.key) : [...prev, p.key]))}
                  className={`rounded-full px-2.5 py-1 text-xs ${platforms.includes(p.key) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={save} disabled={!form.title.trim()} className="w-full">
            保存
          </Button>
        </div>
      )}
    </Modal>
  );
}
