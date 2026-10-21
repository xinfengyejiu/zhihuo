"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ChapterStoryboardModal } from "@/components/ChapterStoryboardModal";
import { api, streamSSE } from "@/lib/api";
import { parseLiveCatalog } from "@/lib/liveCatalog";
import { useTasks } from "@/lib/tasks";
import type { Chapter, Volume } from "@/lib/types";
import { Button, ChapterStatusBadge, Empty, ErrorText, Input, Label, Modal, Select, Spinner, Textarea } from "@/components/ui";

const CHAPTER_PAGE_SIZE = 30;

export default function OutlinePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [volumes, setVolumes] = useState<Volume[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [volumeModal, setVolumeModal] = useState(false);
  const [vForm, setVForm] = useState({ title: "", outline: "" });
  const [chapterModal, setChapterModal] = useState(false);
  const [cForm, setCForm] = useState<{ title: string; volume_id: number | null; outline: string; foreshadow_note: string }>({ title: "", volume_id: null, outline: "", foreshadow_note: "" });
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  // AI 章纲任务化:切走再回来,输出和进度都在
  const { getTask, startTask, clearTask } = useTasks();
  const outlineTask = getTask(`outline-gen-${id}`);
  const aiBusy = outlineTask?.status === "running";
  const aiOutput = outlineTask?.output ?? "";
  const aiError = outlineTask?.status === "error" ? outlineTask.error || "生成失败" : "";
  const [manualOpen, setManualOpen] = useState(false);
  const [manualText, setManualText] = useState("");
  const [manualVolume, setManualVolume] = useState("");
  const [newVolumeTitle, setNewVolumeTitle] = useState("");
  const [manualBusy, setManualBusy] = useState(false);
  const [manualMsg, setManualMsg] = useState("");
  const [manualError, setManualError] = useState("");
  // 批量导入章节正文(多 txt)
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [batchVolume, setBatchVolume] = useState("");
  const [batchApproved, setBatchApproved] = useState(true);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchMsg, setBatchMsg] = useState("");
  const [batchError, setBatchError] = useState("");
  const batchFileRef = useRef<HTMLInputElement>(null);
  const batchFolderRef = useRef<HTMLInputElement>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [reparaBusy, setReparaBusy] = useState(false);
  const [reparaChapterId, setReparaChapterId] = useState<number | null>(null);
  const [reparaTip, setReparaTip] = useState("");
  // 总大纲文档(创作蓝图)
  const [outlineDocs, setOutlineDocs] = useState<{ id: number; title: string; word_count: number; updated_at: string }[]>([]);
  const [docsOpen, setDocsOpen] = useState(false);
  const [editingDoc, setEditingDoc] = useState<{ id: number | null; title: string; content: string } | null>(null);
  const [docBusy, setDocBusy] = useState(false);
  const docFileRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [applyError, setApplyError] = useState("");
  const [summary, setSummary] = useState<string | null>(null);
  /** 各卷章节分页: volumeId -> 当前页(从 1 起); 0 表示未分卷 */
  const [chapterPage, setChapterPage] = useState<Record<number, number>>({});
  const [storyboardChapter, setStoryboardChapter] = useState<Chapter | null>(null);
  const outputRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  interface OutlinePreview {
    created: number;
    volumes_created?: number;
    truncated?: boolean;
    message?: string | null;
    volumes?: { title: string; chapters: { title: string; outline?: string | null }[] }[];
  }
  const preview: OutlinePreview | null =
    outlineTask?.status === "done" && outlineTask.result && typeof outlineTask.result === "object"
      ? (outlineTask.result as OutlinePreview)
      : null;

  const liveVolumes = useMemo(() => {
    if (preview?.volumes?.length) return preview.volumes;
    return parseLiveCatalog(aiOutput);
  }, [aiOutput, preview]);
  const liveChapterCount = liveVolumes.reduce((n, v) => n + v.chapters.length, 0);

  const load = useCallback(async () => {
    const [v, c, docs] = await Promise.all([
      api.get<Volume[]>(`/novels/${id}/volumes`),
      api.get<Chapter[]>(`/novels/${id}/chapters`),
      api.get<{ id: number; title: string; word_count: number; updated_at: string }[]>(`/novels/${id}/outline-docs`),
    ]);
    setVolumes(v);
    setChapters(c);
    setOutlineDocs(docs);
    setLoading(false);
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const el = batchFolderRef.current;
    if (el) el.setAttribute("webkitdirectory", "");
  }, [batchOpen]);

  useEffect(() => {
    if (!stickToBottom.current || !outputRef.current) return;
    const el = outputRef.current;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [aiOutput, liveVolumes, outlineTask?.reasoning, aiBusy]);

  function onOutputScroll() {
    const el = outputRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  // 切走再回来:若该书有进行中/已完成的章纲任务,自动打开弹窗恢复现场
  useEffect(() => {
    if (getTask(`outline-gen-${id}`)) setAiOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function createVolume() {
    await api.post(`/novels/${id}/volumes`, vForm);
    setVolumeModal(false);
    setVForm({ title: "", outline: "" });
    load();
  }

  async function createChapter() {
    setError("");
    try {
      await api.post(`/novels/${id}/chapters`, {
        title: cForm.title,
        volume_id: cForm.volume_id ?? volumes[0]?.id ?? null,
        outline: cForm.outline || null,
        foreshadow_note: cForm.foreshadow_note || null,
      });
      setChapterModal(false);
      setCForm({ title: "", volume_id: null, outline: "", foreshadow_note: "" });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    }
  }

  async function deleteChapter(chapterId: number) {
    if (!confirm("确定删除该章节及其所有版本?")) return;
    await api.delete(`/novels/${id}/chapters/${chapterId}`);
    load();
  }

  async function reparagraphOne(chapterId: number) {
    if (reparaBusy) return;
    setReparaBusy(true);
    setReparaChapterId(chapterId);
    setError("");
    try {
      const res = await api.post<{ message: string; changed: boolean }>(
        `/novels/${id}/chapters/${chapterId}/reparagraph`
      );
      setNoticeTip(res.message || (res.changed ? "已分段" : "无需调整"));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI 分段失败");
    } finally {
      setReparaBusy(false);
      setReparaChapterId(null);
    }
  }

  async function reparagraphAll() {
    if (reparaBusy) return;
    if (
      !confirm(
        `将对本书全部 ${chapters.length} 章用 AI 按网文格式分段(对话独立、短段落,尽量不改字)。\n章数多时会较慢并消耗模型额度,确定继续?`
      )
    ) {
      return;
    }
    setReparaBusy(true);
    setReparaChapterId(null);
    setError("");
    setReparaTip("AI 全书分段进行中…");
    try {
      let lastMsg = "";
      await streamSSE(`/novels/${id}/chapters/reparagraph-all`, {}, (ev) => {
        if (ev.type === "status") {
          setReparaTip(String(ev.detail || "进行中…"));
        } else if (ev.type === "progress") {
          const cur = Number(ev.current || 0);
          const tot = Number(ev.total || chapters.length);
          const title = String(ev.title || "");
          const detail = String(ev.detail || "");
          setReparaTip(`AI 分段 ${cur}/${tot}${title ? ` · ${title}` : ""}${detail ? ` · ${detail}` : ""}`);
        } else if (ev.type === "done") {
          lastMsg = String(ev.message || "完成");
        } else if (ev.type === "error") {
          throw new Error(String(ev.detail || "全书分段失败"));
        }
      });
      setNoticeTip(lastMsg || "全书 AI 分段完成");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "全书分段失败");
      setReparaTip("");
    } finally {
      setReparaBusy(false);
    }
  }

  function setNoticeTip(text: string) {
    setReparaTip(text);
    setTimeout(() => setReparaTip(""), 5000);
  }

  function runAI() {
    if (aiBusy) return;
    const instruction = aiInstruction.trim();
    const continueKeys = ["继续", "往后", "接着", "后续", "再生成", "下一", "续写", "补全后面", "从后面", "往下"];
    const wantContinue =
      chapters.length >= 40 || (instruction.length > 0 && continueKeys.some((k) => instruction.includes(k)));
    const mode = wantContinue ? "next" : "catalog";
    const count = wantContinue ? 16 : 12;
    setApplyError("");
    stickToBottom.current = true;
    startTask({
      key: `outline-gen-${id}`,
      label: wantContinue ? "AI 续写章纲" : "AI 生成章纲",
      href: `/novels/${id}/outline`,
      run: async (r) => {
        let output = "";
        r.setMeta({ outlineMode: mode, existingChapters: chapters.length });
        await streamSSE(
          `/novels/${id}/generate-outline`,
          { instruction: instruction || null, mode, count },
          (ev) => {
            if (ev.type === "status") {
              r.setMeta({ notice: String(ev.detail || "") });
              return;
            }
            if (ev.type === "delta") {
              const piece = String(ev.content ?? "");
              output += piece;
              r.pushOutput(piece);
            } else if (ev.type === "reasoning") r.pushReasoning(String(ev.content ?? ""));
            else if (ev.type === "error") throw new Error((ev.detail as string) || "生成失败");
          }
        );
        try {
          return await api.post<OutlinePreview>(`/novels/${id}/apply-outline`, { text: output, dry_run: true });
        } catch (e) {
          return { created: -1, message: e instanceof Error ? e.message : "预览失败" };
        }
      },
    });
    setAiOpen(true);
  }

  async function applyOutline() {
    setApplyError("");
    try {
      const res = await api.post<{ created: number; volumes_created?: number; message?: string }>(`/novels/${id}/apply-outline`, {
        text: aiOutput,
      });
      if (!res.created) {
        setApplyError(res.message || "没有解析出章节,目录没有变化");
        return;
      }
      setAiOpen(false);
      clearTask(`outline-gen-${id}`);
      await load();
      const volText = res.volumes_created ? `${res.volumes_created} 卷、` : "";
      alert(`已创建 ${volText}${res.created} 章`);
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "套用失败");
    }
  }

  async function applyManual() {
    setManualBusy(true);
    setManualError("");
    setManualMsg("");
    try {
      const body: Record<string, unknown> = { text: manualText };
      if (manualVolume === "new") body.new_volume_title = newVolumeTitle.trim() || "新卷";
      else if (manualVolume) body.volume_id = +manualVolume;
      const res = await api.post<{ created: number; volume_title: string }>(`/novels/${id}/apply-outline`, body);
      if (res.created === 0) {
        setManualError("没有解析出任何章节:请确认每行至少有章节标题");
      } else {
        setManualMsg(`已创建 ${res.created} 章,归入「${res.volume_title}」`);
        setManualText("");
        await load();
      }
    } catch (e) {
      setManualError(e instanceof Error ? e.message : "套用失败");
    } finally {
      setManualBusy(false);
    }
  }

  function pickBatchFiles(list: FileList | null) {
    if (!list?.length) return;
    const next = Array.from(list).filter((f) => /\.(txt|text|md|markdown)$/i.test(f.name));
    setBatchFiles(next);
    setBatchMsg("");
    setBatchError("");
  }

  async function exportChaptersZip() {
    if (exportBusy || chapters.length === 0) return;
    setExportBusy(true);
    setError("");
    try {
      await api.download(`/novels/${id}/export-chapters`, `novel_${id}_chapters.zip`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExportBusy(false);
    }
  }

  async function importBatchChapters() {
    if (!batchFiles.length || batchBusy) return;
    setBatchBusy(true);
    setBatchError("");
    setBatchMsg("");
    try {
      const form = new FormData();
      for (const f of batchFiles) form.append("files", f);
      if (batchVolume) form.append("volume_id", batchVolume);
      form.append("status", batchApproved ? "approved" : "draft");
      const res = await api.upload<{
        chapters_created: number;
        skipped_count: number;
        message: string;
        skipped?: { file: string; reason: string }[];
      }>(`/novels/${id}/import-chapters`, form);
      let tip = res.message || `已导入 ${res.chapters_created} 章`;
      if (res.skipped?.length) {
        const sample = res.skipped
          .slice(0, 5)
          .map((s) => `${s.file}: ${s.reason}`)
          .join("；");
        tip += `。跳过示例: ${sample}${res.skipped.length > 5 ? "…" : ""}`;
      }
      setBatchMsg(tip);
      setBatchFiles([]);
      if (batchFileRef.current) batchFileRef.current.value = "";
      if (batchFolderRef.current) batchFolderRef.current.value = "";
      await load();
    } catch (e) {
      setBatchError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function importDocFile(files: FileList | null) {
    if (!files || files.length === 0) return;
    setDocBusy(true);
    try {
      for (const f of Array.from(files)) {
        const text = await f.text();
        const title = f.name.replace(/\.(md|markdown|txt)$/i, "");
        await api.post(`/novels/${id}/outline-docs`, { title, content: text });
      }
      await load();
    } catch {
      alert("导入失败,请确认文件是文本格式(.md/.txt)");
    } finally {
      setDocBusy(false);
      if (docFileRef.current) docFileRef.current.value = "";
    }
  }

  async function saveDoc() {
    if (!editingDoc) return;
    setDocBusy(true);
    try {
      if (editingDoc.id) {
        await api.put(`/novels/${id}/outline-docs/${editingDoc.id}`, { title: editingDoc.title, content: editingDoc.content });
      } else {
        await api.post(`/novels/${id}/outline-docs`, { title: editingDoc.title, content: editingDoc.content });
      }
      setEditingDoc(null);
      await load();
    } finally {
      setDocBusy(false);
    }
  }

  async function deleteDoc(docId: number) {
    if (!confirm("确定删除该大纲文档?")) return;
    await api.delete(`/novels/${id}/outline-docs/${docId}`);
    load();
  }

  async function loadSummary() {
    const s = await api.get<{ content: string }>(`/novels/${id}/summary`);
    setSummary(s.content || "(暂无前情摘要)");
  }

  async function refreshSummary() {
    setError("");
    try {
      const s = await api.post<{ content: string }>(`/novels/${id}/summary/refresh`);
      setSummary(s.content);
    } catch (e) {
      setError(e instanceof Error ? e.message : "刷新失败");
    }
  }

  if (loading) return <Spinner />;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => { setVForm({ title: "", outline: "" }); setVolumeModal(true); }}>+ 新卷</Button>
        <Button size="sm" variant="outline" onClick={() => { setCForm({ title: "", volume_id: volumes[0]?.id ?? null, outline: "", foreshadow_note: "" }); setChapterModal(true); }}>+ 新章节</Button>
        <Button size="sm" variant="outline" onClick={() => setDocsOpen(true)}>
          📖 总大纲({outlineDocs.length})
        </Button>
        <Button size="sm" variant="outline" onClick={() => { setManualOpen(true); setManualMsg(""); setManualError(""); }}>📋 批量建章</Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            setBatchOpen(true);
            setBatchMsg("");
            setBatchError("");
            setBatchFiles([]);
            setBatchVolume(volumes[0]?.id ? String(volumes[0].id) : "");
          }}
        >
          ⬆ 批量导入正文
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void exportChaptersZip()}
          disabled={exportBusy || chapters.length === 0}
        >
          {exportBusy ? "导出中…" : "⬇ 导出正文"}
        </Button>
        <Button size="sm" variant="outline" onClick={() => setAiOpen(true)}>✨ AI 生成章纲</Button>
        <Button size="sm" variant="outline" onClick={() => void reparagraphAll()} disabled={reparaBusy || chapters.length === 0}>
          {reparaBusy && reparaChapterId === null ? "AI 全书分段中…" : "✨ AI 一键分段"}
        </Button>
        <Button size="sm" variant="ghost" onClick={loadSummary}>查看前情摘要</Button>
      </div>
      {reparaTip && <p className="mb-2 text-sm text-emerald-600">✓ {reparaTip}</p>}
      <ErrorText>{error}</ErrorText>

      {summary !== null && (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-medium">前情摘要(每章审核通过后自动更新)</h3>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={refreshSummary}>AI 刷新</Button>
              <Button size="sm" variant="ghost" onClick={() => setSummary(null)}>收起</Button>
            </div>
          </div>
          <p className="whitespace-pre-wrap text-sm text-slate-600">{summary}</p>
        </div>
      )}

      {chapters.length === 0 && volumes.length === 0 ? (
        <Empty text="先创建第一卷,然后添加章节或用 AI 生成章纲" />
      ) : (
        <div className="space-y-5">
          {volumes.map((v) => {
            const volChapters = chapters.filter((c) => c.volume_id === v.id);
            const total = volChapters.length;
            const totalPages = Math.max(1, Math.ceil(total / CHAPTER_PAGE_SIZE));
            const page = Math.min(Math.max(1, chapterPage[v.id] || 1), totalPages);
            const start = (page - 1) * CHAPTER_PAGE_SIZE;
            const pageChapters = volChapters.slice(start, start + CHAPTER_PAGE_SIZE);
            return (
              <div key={v.id} className="rounded-xl border border-slate-200 bg-white">
                <div className="border-b border-slate-100 px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="font-semibold">
                      {v.title}
                      {total > 0 && (
                        <span className="ml-2 text-xs font-normal text-slate-400">
                          {total} 章
                          {total > CHAPTER_PAGE_SIZE ? ` · 第 ${page}/${totalPages} 页` : ""}
                        </span>
                      )}
                    </h3>
                    <button
                      className="text-xs text-indigo-600 hover:underline"
                      onClick={() => { setCForm({ title: "", volume_id: v.id, outline: "", foreshadow_note: "" }); setChapterModal(true); }}
                    >
                      + 加章节
                    </button>
                  </div>
                  {v.outline && <p className="mt-1 text-xs text-slate-500">{v.outline}</p>}
                </div>
                {total === 0 ? (
                  <p className="px-4 py-3 text-xs text-slate-400">本卷还没有章节</p>
                ) : (
                  <>
                    <ul className="divide-y divide-slate-50">
                      {pageChapters.map((c) => (
                        <li key={c.id} className="group flex items-center justify-between px-4 py-2.5 text-sm hover:bg-slate-50">
                          <Link href={`/novels/${id}/chapters/${c.id}`} className="flex min-w-0 flex-1 items-center gap-3">
                            <span className="truncate font-medium text-slate-700 group-hover:text-indigo-600">{c.title}</span>
                            <ChapterStatusBadge status={c.status} />
                            <span className="shrink-0 text-xs text-slate-400">{c.word_count} 字</span>
                            {c.outline && <span className="hidden truncate text-xs text-slate-400 md:block">章纲:{c.outline}</span>}
                          </Link>
                          <div className="flex shrink-0 gap-2 text-xs">
                            <button
                              className="text-amber-700 hover:underline disabled:opacity-40"
                              disabled={reparaBusy}
                              onClick={() => void reparagraphOne(c.id)}
                            >
                              {reparaChapterId === c.id ? "AI分段中…" : "AI分段"}
                            </button>
                            <button
                              className="text-violet-600 hover:underline"
                              onClick={() => setStoryboardChapter(c)}
                            >
                              分镜
                            </button>
                            <Link href={`/novels/${id}/chapters/${c.id}`} className="text-indigo-600 hover:underline">写作</Link>
                            <button className="text-red-500 hover:underline" onClick={() => deleteChapter(c.id)}>删除</button>
                          </div>
                        </li>
                      ))}
                    </ul>
                    {totalPages > 1 && (
                      <ChapterPager
                        page={page}
                        totalPages={totalPages}
                        total={total}
                        pageSize={CHAPTER_PAGE_SIZE}
                        onChange={(p) => setChapterPage((prev) => ({ ...prev, [v.id]: p }))}
                      />
                    )}
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 未分卷章节 */}
      {chapters.some((c) => !c.volume_id) && (() => {
        const orphan = chapters.filter((c) => !c.volume_id);
        const total = orphan.length;
        const totalPages = Math.max(1, Math.ceil(total / CHAPTER_PAGE_SIZE));
        const page = Math.min(Math.max(1, chapterPage[0] || 1), totalPages);
        const start = (page - 1) * CHAPTER_PAGE_SIZE;
        const pageChapters = orphan.slice(start, start + CHAPTER_PAGE_SIZE);
        return (
          <div className="mt-5 rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium">
              未分卷章节
              <span className="ml-2 text-xs font-normal text-slate-400">
                {total} 章
                {total > CHAPTER_PAGE_SIZE ? ` · 第 ${page}/${totalPages} 页` : ""}
              </span>
            </div>
            <ul className="divide-y divide-slate-50">
              {pageChapters.map((c) => (
                <li key={c.id} className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-slate-50">
                  <Link href={`/novels/${id}/chapters/${c.id}`} className="flex flex-1 items-center gap-3">
                    <span className="font-medium text-slate-700">{c.title}</span>
                    <ChapterStatusBadge status={c.status} />
                    <span className="shrink-0 text-xs text-slate-400">{c.word_count} 字</span>
                  </Link>
                  <div className="flex shrink-0 gap-2 text-xs">
                    <button
                      className="text-amber-700 hover:underline disabled:opacity-40"
                      disabled={reparaBusy}
                      onClick={() => void reparagraphOne(c.id)}
                    >
                      {reparaChapterId === c.id ? "AI分段中…" : "AI分段"}
                    </button>
                    <button className="text-violet-600 hover:underline" onClick={() => setStoryboardChapter(c)}>
                      分镜
                    </button>
                    <button className="text-red-500 hover:underline" onClick={() => deleteChapter(c.id)}>删除</button>
                  </div>
                </li>
              ))}
            </ul>
            {totalPages > 1 && (
              <ChapterPager
                page={page}
                totalPages={totalPages}
                total={total}
                pageSize={CHAPTER_PAGE_SIZE}
                onChange={(p) => setChapterPage((prev) => ({ ...prev, 0: p }))}
              />
            )}
          </div>
        );
      })()}

      <Modal open={volumeModal} title="新建卷" onClose={() => setVolumeModal(false)}>
        <div className="space-y-3">
          <div>
            <Label>卷名 *</Label>
            <Input value={vForm.title} onChange={(e) => setVForm({ ...vForm, title: e.target.value })} placeholder="如:第一卷 出山" />
          </div>
          <div>
            <Label>本卷大纲</Label>
            <Textarea rows={4} value={vForm.outline} onChange={(e) => setVForm({ ...vForm, outline: e.target.value })} />
          </div>
          <Button onClick={createVolume} disabled={!vForm.title.trim()} className="w-full">创建</Button>
        </div>
      </Modal>

      <Modal open={chapterModal} title="新建章节" onClose={() => setChapterModal(false)}>
        <div className="space-y-3">
          <div>
            <Label>章节标题 *</Label>
            <Input value={cForm.title} onChange={(e) => setCForm({ ...cForm, title: e.target.value })} placeholder="如:第一章 雪夜" />
          </div>
          <div>
            <Label>所属卷</Label>
            <Select value={cForm.volume_id ?? ""} onChange={(e) => setCForm({ ...cForm, volume_id: e.target.value ? +e.target.value : null })} className="w-full">
              <option value="">未分卷</option>
              {volumes.map((v) => (
                <option key={v.id} value={v.id}>{v.title}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label>本章目标 / 剧情要点(生成时注入)</Label>
            <Textarea rows={3} value={cForm.outline} onChange={(e) => setCForm({ ...cForm, outline: e.target.value })} />
          </div>
          <div>
            <Label>伏笔任务(本章需埋 / 需回收)</Label>
            <Textarea rows={2} value={cForm.foreshadow_note} onChange={(e) => setCForm({ ...cForm, foreshadow_note: e.target.value })} />
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={createChapter} disabled={!cForm.title.trim()} className="w-full">创建</Button>
        </div>
      </Modal>

      <Modal open={aiOpen} title="AI 生成章纲" onClose={() => setAiOpen(false)} wide tall>
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="shrink-0 space-y-3">
            <p className="rounded-lg bg-indigo-50 p-2 text-xs text-indigo-700">
              按「总大纲」拆卷章目录;确认后再写入,同名章自动跳过。章节很多时会自动压缩已有目录。
              填写「继续往后生成」或已有 ≥40 章时,走续写模式(只规划后续新章)。
              {chapters.length > 0 ? ` 当前已有 ${chapters.length} 章。` : ""}
            </p>
            <div>
              <Label>整体要求(可选)</Label>
              <Input
                value={aiInstruction}
                onChange={(e) => setAiInstruction(e.target.value)}
                placeholder="如:继续往后生成 / 围绕觉醒血脉展开,节奏加快"
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={runAI} disabled={aiBusy}>{aiBusy ? "生成中…" : "开始生成"}</Button>
              {aiOutput && (
                <Button variant="outline" onClick={applyOutline} disabled={aiBusy || preview?.created === 0}>
                  {preview && preview.created > 0
                    ? `套用为章节（${preview.volumes_created || 0} 卷 ${preview.created} 章）`
                    : liveChapterCount > 0
                      ? `套用为章节（已识别 ${liveVolumes.length} 卷 ${liveChapterCount} 章）`
                      : "套用为章节"}
                </Button>
              )}
              {(aiBusy || liveChapterCount > 0 || typeof outlineTask?.meta?.notice === "string") && (
                <span className="text-xs text-slate-500">
                  {typeof outlineTask?.meta?.notice === "string" && outlineTask.meta.notice
                    ? `${outlineTask.meta.notice} · `
                    : ""}
                  {aiBusy ? "生成中 · " : liveChapterCount > 0 ? "已完成 · " : ""}
                  已识别 {liveVolumes.length} 卷 / {liveChapterCount} 章
                </span>
              )}
            </div>
            <ErrorText>{aiError || applyError}</ErrorText>
            {preview && preview.created === 0 && (
              <div className="rounded-lg border border-amber-100 bg-amber-50 p-2 text-sm text-amber-800">
                {preview.message || "没有解析出章节"}
              </div>
            )}
            {preview?.truncated && (
              <div className="rounded-lg border border-amber-100 bg-amber-50 p-2 text-xs text-amber-800">
                输出可能未写完,套用后可再生成一次,已有章节不会重复创建。
              </div>
            )}
          </div>

          {(aiBusy || aiOutput || outlineTask?.reasoning) && (
            <div
              ref={outputRef}
              onScroll={onOutputScroll}
              className="min-h-0 flex-1 overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-4"
            >
              {outlineTask?.reasoning && (
                <details className="mb-3 rounded-lg bg-white/80 p-2 text-xs text-slate-500">
                  <summary className="cursor-pointer select-none text-slate-600">模型思考过程</summary>
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{outlineTask.reasoning}</pre>
                </details>
              )}

              {liveVolumes.length > 0 ? (
                <div className="space-y-4">
                  {liveVolumes.map((v, i) => (
                    <div key={`${v.title}-${i}`} className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
                      <div className="mb-2 flex items-baseline justify-between gap-2 border-b border-slate-100 pb-2">
                        <h4 className="font-semibold text-slate-800">{v.title}</h4>
                        <span className="shrink-0 text-xs text-slate-400">{v.chapters.length} 章</span>
                      </div>
                      <ol className="space-y-1.5">
                        {v.chapters.map((c, j) => (
                          <li key={`${c.title}-${j}`} className="text-sm leading-relaxed text-slate-700">
                            <span className="mr-2 inline-block min-w-[1.5rem] text-xs text-slate-400">{j + 1}.</span>
                            <span className="font-medium">{c.title}</span>
                            {c.outline && <span className="mt-0.5 block pl-6 text-xs text-slate-500">{c.outline}</span>}
                          </li>
                        ))}
                      </ol>
                    </div>
                  ))}
                  {aiBusy && <div className="py-2 text-center text-xs text-indigo-500">正在继续生成…</div>}
                </div>
              ) : aiBusy ? (
                <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-2 text-sm text-slate-500">
                  <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-600" />
                  正在生成目录,解析出卷章后会显示在这里…
                </div>
              ) : aiOutput ? (
                <pre className="whitespace-pre-wrap text-sm text-slate-600">{aiOutput}</pre>
              ) : null}
            </div>
          )}
        </div>
      </Modal>

      <Modal
        open={batchOpen}
        title="批量导入章节正文"
        onClose={() => !batchBusy && setBatchOpen(false)}
        wide
      >
        <div className="space-y-3">
          <p className="rounded-lg bg-indigo-50 p-2 text-xs text-indigo-700">
            选择多个 .txt / .md(或整个文件夹)。每个文件名即章名(如「第一章 起源.txt」),文件内容写入该章正文;
            按章序号自动排序后追加到本书现有章节之后。正文不足 100 字的文件会跳过。
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              ref={batchFileRef}
              type="file"
              multiple
              accept=".txt,.text,.md,.markdown,text/plain,text/markdown"
              className="hidden"
              disabled={batchBusy}
              onChange={(e) => pickBatchFiles(e.target.files)}
            />
            <input
              ref={batchFolderRef}
              type="file"
              multiple
              className="hidden"
              disabled={batchBusy}
              onChange={(e) => pickBatchFiles(e.target.files)}
            />
            <Button size="sm" variant="outline" disabled={batchBusy} onClick={() => batchFileRef.current?.click()}>
              选择多个文件
            </Button>
            <Button size="sm" variant="outline" disabled={batchBusy} onClick={() => batchFolderRef.current?.click()}>
              选择文件夹
            </Button>
          </div>
          {batchFiles.length > 0 && (
            <div className="max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              <p className="mb-1 font-medium text-slate-700">已选 {batchFiles.length} 个文件</p>
              <ul className="space-y-0.5">
                {batchFiles.slice(0, 30).map((f) => (
                  <li key={f.name + f.size} className="truncate">
                    {f.name}
                  </li>
                ))}
                {batchFiles.length > 30 && <li>…还有 {batchFiles.length - 30} 个</li>}
              </ul>
            </div>
          )}
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <Label>归入卷(可选)</Label>
              <Select value={batchVolume} onChange={(e) => setBatchVolume(e.target.value)} disabled={batchBusy}>
                <option value="">不指定卷</option>
                {volumes.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.title}
                  </option>
                ))}
              </Select>
            </div>
            <label className="flex items-center gap-2 pb-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={batchApproved}
                disabled={batchBusy}
                onChange={(e) => setBatchApproved(e.target.checked)}
              />
              导入后直接过审(阅读器可见)
            </label>
          </div>
          {batchBusy && (
            <p className="text-sm text-indigo-600">正在导入 {batchFiles.length} 个文件,请稍候…</p>
          )}
          {batchMsg && <div className="rounded-lg bg-emerald-50 p-2.5 text-sm text-emerald-700">✓ {batchMsg}</div>}
          {batchError && (
            <div className="rounded-lg border border-red-100 bg-red-50 p-2.5 text-sm text-red-600">{batchError}</div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" disabled={batchBusy} onClick={() => setBatchOpen(false)}>
              关闭
            </Button>
            <Button onClick={() => void importBatchChapters()} disabled={!batchFiles.length || batchBusy}>
              {batchBusy ? "导入中…" : `开始导入${batchFiles.length ? ` (${batchFiles.length})` : ""}`}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={manualOpen} title="录入大纲(粘贴批量建章)" onClose={() => setManualOpen(false)} wide>
        <div className="space-y-3">
          <p className="rounded-lg bg-indigo-50 p-2 text-xs text-indigo-700">
            每行一章,推荐「第N章 标题|剧情要点|出场人物」;也支持简写「标题|要点」、纯标题行、markdown 标题(##),会自动去掉「第N章」前缀。
            <br />
            注意:这里每一行都会真的创建一个章节。如果要导入整份 md 大纲作为创作参考(不拆成章节),请用「📖 总大纲」导入。
          </p>
          <Textarea
            rows={10}
            value={manualText}
            onChange={(e) => setManualText(e.target.value)}
            placeholder={"第一章 雪夜|雪夜初遇,埋下身世伏笔|林晚舟、苏轻眉\n第二章 下山|主角离山,路遇劫匪\n第三章 入门|拜入天衡宗,遭遇刁难"}
          />
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <Label>归入卷</Label>
              <Select value={manualVolume} onChange={(e) => setManualVolume(e.target.value)}>
                <option value="">自动(第一卷,没有则新建「第一卷」)</option>
                {volumes.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.title}
                  </option>
                ))}
                <option value="new">新建卷…</option>
              </Select>
            </div>
            {manualVolume === "new" && (
              <div>
                <Label>新卷名</Label>
                <Input value={newVolumeTitle} onChange={(e) => setNewVolumeTitle(e.target.value)} placeholder="如:第二卷 出山" />
              </div>
            )}
            <Button onClick={applyManual} disabled={manualBusy || !manualText.trim()}>
              {manualBusy ? "创建中…" : "套用为章节"}
            </Button>
          </div>
          {manualMsg && <div className="rounded-lg bg-emerald-50 p-2.5 text-sm text-emerald-700">✓ {manualMsg}</div>}
          {manualError && <div className="rounded-lg border border-red-100 bg-red-50 p-2.5 text-sm text-red-600">{manualError}</div>}
        </div>
      </Modal>

      <Modal open={docsOpen} title="总大纲文档(创作蓝图)" onClose={() => setDocsOpen(false)} wide>
        <div className="space-y-3">
          <p className="rounded-lg bg-indigo-50 p-2 text-xs text-indigo-700">
            这里保存全书大纲(如 md 文档),与下方的卷/章节结构相互独立,不会拆成章节。AI 生成章节正文和章纲时,会自动参考这里的大纲内容来把握剧情走向。
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              ref={docFileRef}
              type="file"
              multiple
              accept=".md,.markdown,.txt"
              className="hidden"
              onChange={(e) => importDocFile(e.target.files)}
            />
            <Button size="sm" onClick={() => docFileRef.current?.click()} disabled={docBusy}>
              ⬆ 导入 .md 文件
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditingDoc({ id: null, title: "大纲", content: "" })}>
              + 新建文档
            </Button>
          </div>
          <div className="space-y-2">
            {outlineDocs.length === 0 ? (
              <p className="py-6 text-center text-xs text-slate-400">还没有大纲文档,点上方导入 md 文件或新建</p>
            ) : (
              outlineDocs.map((d) => (
                <div key={d.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <span className="font-medium text-slate-700">{d.title}</span>
                    <span className="ml-2 text-xs text-slate-400">
                      {d.word_count.toLocaleString()} 字 · 更新于 {new Date(d.updated_at).toLocaleString("zh-CN")}
                    </span>
                  </div>
                  <div className="flex shrink-0 gap-2 text-xs">
                    <button
                      className="text-indigo-600 hover:underline"
                      onClick={async () => {
                        const full = await api.get<{ id: number; title: string; content: string }>(`/novels/${id}/outline-docs/${d.id}`);
                        setEditingDoc({ id: full.id, title: full.title, content: full.content });
                      }}
                    >
                      查看 / 编辑
                    </button>
                    <button className="text-red-500 hover:underline" onClick={() => deleteDoc(d.id)}>
                      删除
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </Modal>

      <Modal open={!!editingDoc} title={editingDoc?.id ? "编辑大纲文档" : "新建大纲文档"} onClose={() => setEditingDoc(null)} wide>
        {editingDoc && (
          <div className="space-y-3">
            <div>
              <Label>文档标题</Label>
              <Input value={editingDoc.title} onChange={(e) => setEditingDoc({ ...editingDoc, title: e.target.value })} />
            </div>
            <div>
              <Label>大纲内容(支持 markdown,保存后 AI 生成时自动参考)</Label>
              <Textarea
                rows={18}
                className="font-mono text-xs"
                value={editingDoc.content}
                onChange={(e) => setEditingDoc({ ...editingDoc, content: e.target.value })}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">{editingDoc.content.length.toLocaleString()} 字</span>
              <Button onClick={saveDoc} disabled={docBusy || !editingDoc.title.trim()}>
                保存
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <ChapterStoryboardModal
        open={!!storyboardChapter}
        novelId={id}
        chapterId={storyboardChapter?.id || 0}
        chapterTitle={storyboardChapter?.title || ""}
        onClose={() => setStoryboardChapter(null)}
      />
    </div>
  );
}

function ChapterPager({
  page,
  totalPages,
  total,
  pageSize,
  onChange,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
}) {
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  const pages = useMemo(() => {
    const window = 5;
    let start = Math.max(1, page - Math.floor(window / 2));
    let end = Math.min(totalPages, start + window - 1);
    start = Math.max(1, end - window + 1);
    return Array.from({ length: end - start + 1 }, (_, i) => start + i);
  }, [page, totalPages]);

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-2.5 text-xs text-slate-500">
      <span>
        显示 {from}-{to} / 共 {total} 章
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          disabled={page <= 1}
          className="rounded px-2 py-1 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={() => onChange(page - 1)}
        >
          上一页
        </button>
        {pages[0] > 1 && (
          <>
            <button type="button" className="rounded px-2 py-1 hover:bg-slate-100" onClick={() => onChange(1)}>
              1
            </button>
            {pages[0] > 2 && <span className="px-1">…</span>}
          </>
        )}
        {pages.map((p) => (
          <button
            key={p}
            type="button"
            className={`rounded px-2 py-1 ${p === page ? "bg-indigo-600 text-white" : "hover:bg-slate-100"}`}
            onClick={() => onChange(p)}
          >
            {p}
          </button>
        ))}
        {pages[pages.length - 1] < totalPages && (
          <>
            {pages[pages.length - 1] < totalPages - 1 && <span className="px-1">…</span>}
            <button type="button" className="rounded px-2 py-1 hover:bg-slate-100" onClick={() => onChange(totalPages)}>
              {totalPages}
            </button>
          </>
        )}
        <button
          type="button"
          disabled={page >= totalPages}
          className="rounded px-2 py-1 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={() => onChange(page + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
