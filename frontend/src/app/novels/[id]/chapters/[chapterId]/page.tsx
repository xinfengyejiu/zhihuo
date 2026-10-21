"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { api, readerNovelUrl, streamSSE } from "@/lib/api";
import { useTasks } from "@/lib/tasks";
import type { Chapter, Character, ReportInfo, Version } from "@/lib/types";
import { Button, ChapterStatusBadge, Input, Label, Modal, Spinner, Textarea } from "@/components/ui";

type Mode = "chapter" | "continue" | "polish" | "rewrite" | "fix_reject";
const MODE_LABELS: Record<Mode, string> = {
  chapter: "生成本章",
  continue: "续写",
  polish: "润色",
  rewrite: "重写",
  fix_reject: "按驳回改",
};

export default function ChapterEditorPage() {
  const { id, chapterId } = useParams<{ id: string; chapterId: string }>();
  const router = useRouter();
  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [initialContent, setInitialContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // AI 面板(任务化:切走再回来进度不丢,可与其他 AI 功能并发)
  const { getTask, startTask, clearTask } = useTasks();
  const [mode, setMode] = useState<Mode>("chapter");
  const [instruction, setInstruction] = useState("");
  /** 手机端 AI 抽屉开关;桌面始终显示 */
  const [aiPanelOpen, setAiPanelOpen] = useState(false);
  const genTask = getTask(`gen-${chapterId}`);
  const aiBusy = genTask?.status === "running";
  const aiOutput = genTask?.output ?? "";
  const aiReasoning = genTask?.reasoning ?? "";
  const aiModel = (genTask?.meta?.model as string) || "";
  const aiError = genTask?.status === "error" ? genTask.error || "生成失败" : "";
  const aiVersionId =
    genTask?.status === "done" ? ((genTask.result as { version_id?: number } | null)?.version_id ?? null) : null;

  // 章纲编辑弹窗
  const [metaOpen, setMetaOpen] = useState(false);
  const [metaForm, setMetaForm] = useState({ title: "", outline: "", foreshadow_note: "", characters_involved: [] as number[] });

  // 版本历史
  const [versionsOpen, setVersionsOpen] = useState(false);

  // AI 复盘
  const [reportsOpen, setReportsOpen] = useState(false);
  const [reports, setReports] = useState<ReportInfo[]>([]);
  const analyzeTask = getTask(`analyze-${chapterId}`);
  const analyzing = analyzeTask?.status === "running";

  // 规则自检(镜子,不挡保存)
  type SelfCheckIssue = {
    kind: string;
    title: string;
    detail: string;
    advice: string;
    excerpt?: string | null;
    count?: number | null;
    hits?: string[];
  };
  type SelfCheckResult = {
    score: number;
    word_count: number;
    para_count: number;
    avg_para_chars: number;
    issue_count: number;
    issues: SelfCheckIssue[];
    summary: string;
  };
  const [selfCheck, setSelfCheck] = useState<SelfCheckResult | null>(null);
  const [selfChecking, setSelfChecking] = useState(false);
  const [reparagraphing, setReparagraphing] = useState(false);
  /** 勾选要 AI 改的项;默认全选 */
  const [fixSelected, setFixSelected] = useState<boolean[]>([]);
  /** 可编辑的修改建议(与 issues 一一对应) */
  const [fixAdvice, setFixAdvice] = useState<string[]>([]);
  const [fixing, setFixing] = useState(false);

  const outputRef = useRef<HTMLDivElement>(null);
  const editor = useEditor({
    extensions: [StarterKit],
    content: "",
    editorProps: {
      attributes: { class: "tiptap px-6 py-4" },
    },
    onUpdate: () => setDirty(true),
  });

  const load = useCallback(async () => {
    const [ch, chars, content] = await Promise.all([
      api.get<Chapter>(`/novels/${id}/chapters/${chapterId}`),
      api.get<Character[]>(`/novels/${id}/characters`),
      api.get<{ content: string; ai_draft: { id: number; content: string } | null }>(`/novels/${id}/chapters/${chapterId}/content`),
    ]);
    setChapter(ch);
    setCharacters(chars);
    setInitialContent(content.content);
    setMetaForm({
      title: ch.title,
      outline: ch.outline || "",
      foreshadow_note: ch.foreshadow_note || "",
      characters_involved: ch.characters_involved || [],
    });
    setLoading(false);
  }, [id, chapterId]);

  useEffect(() => {
    load();
  }, [load]);

  // 内容加载完后填入编辑器(纯文本需转成段落 HTML,否则 TipTap 会把换行吃掉/排版错乱)
  useEffect(() => {
    if (editor && !loading) {
      editor.commands.setContent(textToEditorHtml(initialContent));
      setDirty(false);
      if ((initialContent || "").trim().length >= 30) {
        void runSelfCheck(initialContent);
      } else {
        setSelfCheck(null);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, editor, initialContent]);

  // 有未消化的驳回意见时,默认切到「按驳回改」并带上意见
  useEffect(() => {
    if (!chapter?.last_reject_comment?.trim()) return;
    if (chapter.status !== "rejected" && chapter.status !== "draft") return;
    setMode("fix_reject");
    setInstruction((prev) => (prev.trim() ? prev : chapter.last_reject_comment!.trim().slice(0, 2000)));
  }, [chapter?.id, chapter?.last_reject_comment, chapter?.status]);

  // 生成任务 running→done:刷新章节(版本/字数);失败时弹出提醒
  const prevGenStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const status = genTask?.status;
    if (prevGenStatus.current === "running" && status === "done") {
      load();
    }
    if (status === "error" && genTask?.error) {
      setError(genTask.error);
    }
    prevGenStatus.current = status;
  }, [genTask?.status, genTask?.error, load]);

  const prevAnalyzeStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (prevAnalyzeStatus.current === "running" && analyzeTask?.status === "done" && reportsOpen) {
      api.get<ReportInfo[]>(`/novels/${id}/chapters/${chapterId}/reports`).then(setReports);
    }
    if (analyzeTask?.status === "error" && analyzeTask.error) {
      setError(analyzeTask.error);
    }
    prevAnalyzeStatus.current = analyzeTask?.status;
  }, [analyzeTask?.status, analyzeTask?.error, reportsOpen, id, chapterId]);

  if (loading) return <Spinner />;
  if (!chapter) return <p>章节不存在</p>;

  const canGenerate = chapter.status === "draft" || chapter.status === "rejected";
  const canSubmit = chapter.status === "draft" || chapter.status === "rejected";
  const rejectComment = (chapter.last_reject_comment || "").trim();
  const showFixReject = canGenerate && !!rejectComment;
  const visibleModes = (Object.keys(MODE_LABELS) as Mode[]).filter(
    (m) => m !== "fix_reject" || showFixReject
  );

  async function runSelfCheck(content?: string) {
    if (!chapterId) return;
    setSelfChecking(true);
    try {
      const body =
        content !== undefined
          ? { content }
          : editor
            ? { content: htmlToText(editor.getHTML()) }
            : {};
      const res = await api.post<SelfCheckResult>(
        `/novels/${id}/chapters/${chapterId}/self-check`,
        body
      );
      setSelfCheck(res);
      setFixSelected(res.issues.map(() => true));
      setFixAdvice(res.issues.map((i) => i.advice || ""));
    } catch {
      // 自检失败不影响写作
    } finally {
      setSelfChecking(false);
    }
  }

  async function save(): Promise<boolean> {
    if (!editor) return false;
    setError("");
    try {
      const html = editor.getHTML();
      const text = htmlToText(html);
      const updated = await api.put<Chapter>(`/novels/${id}/chapters/${chapterId}/content`, { content: text });
      setChapter(updated);
      setDirty(false);
      setNotice("已保存");
      setTimeout(() => setNotice(""), 2000);
      // 镜子:保存成功后再跑,不 await、不挡流程
      void runSelfCheck(text);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
      return false;
    }
  }

  async function reparagraphChapter() {
    if (reparagraphing) return;
    if (dirty && !confirm("当前有未保存修改,AI 分段会先用已保存正文。继续?")) return;
    setReparagraphing(true);
    setError("");
    setNotice("AI 分段中(单章一次调用模型,通常十几秒)…");
    try {
      const res = await api.post<{
        changed: boolean;
        message: string;
        chapter?: Chapter;
      }>(`/novels/${id}/chapters/${chapterId}/reparagraph`);
      if (res.changed) {
        const content = await api.get<{ content: string }>(`/novels/${id}/chapters/${chapterId}/content`);
        setInitialContent(content.content);
        editor?.commands.setContent(textToEditorHtml(content.content));
        setDirty(false);
        if (res.chapter) setChapter(res.chapter);
        else await load();
        setNotice(res.message || "AI 已分段");
        void runSelfCheck(content.content);
      } else {
        setNotice(res.message || "无需分段");
      }
      setTimeout(() => setNotice(""), 3500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI 分段失败(请确认已配置润色/章节模型)");
      setNotice("");
    } finally {
      setReparagraphing(false);
    }
  }

  async function submitReview() {
    setError("");
    try {
      const updated = await api.post<Chapter>(`/novels/${id}/chapters/${chapterId}/submit`);
      setChapter(updated);
      setNotice("已提交审核");
      setTimeout(() => setNotice(""), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败");
    }
  }

  async function withdraw() {
    const updated = await api.post<Chapter>(`/novels/${id}/chapters/${chapterId}/withdraw`);
    setChapter(updated);
  }

  function generate(opts?: { mode?: Mode; instruction?: string; label?: string }) {
    if (aiBusy || !chapter) return;
    const runMode = opts?.mode ?? mode;
    const runInstruction = opts?.instruction !== undefined ? opts.instruction : instruction;
    setError("");
    if (opts?.mode) setMode(opts.mode);
    if (opts?.instruction !== undefined) setInstruction(opts.instruction);
    startTask({
      key: `gen-${chapterId}`,
      label: opts?.label || `生成:${chapter.title}`,
      href: `/novels/${id}/chapters/${chapterId}`,
      run: async (r) => {
        let collected = "";
        let reasoningCollected = "";
        let done: { version_id: number; word_count: number } | null = null;
        await streamSSE(
          `/novels/${id}/chapters/${chapterId}/generate`,
          { mode: runMode, instruction: runInstruction || null },
          (ev) => {
            if (ev.type === "delta") {
              const piece = String(ev.content ?? "");
              collected += piece;
              r.pushOutput(piece);
              outputRef.current?.scrollTo(0, outputRef.current.scrollHeight);
            } else if (ev.type === "reasoning") {
              const piece = String(ev.content ?? "");
              reasoningCollected += piece;
              r.pushReasoning(piece);
            } else if (ev.type === "model") {
              r.setMeta({ model: ev.model });
            } else if (ev.type === "notice") {
              setNotice(String(ev.detail ?? ""));
              setTimeout(() => setNotice(""), 5000);
            } else if (ev.type === "error") {
              const detail = typeof ev.detail === "string" ? ev.detail : "生成失败";
              throw new Error(detail);
            } else if (ev.type === "done") {
              done = { version_id: ev.version_id as number, word_count: ev.word_count as number };
            }
          }
        );
        if (!collected.trim() && reasoningCollected.trim()) {
          // 后端未回填时,前端兜底展示思考内容并提示
          r.pushOutput(reasoningCollected);
          collected = reasoningCollected;
          setNotice("模型只返回了思考过程,已临时展示;建议在个人中心换非推理模型");
        }
        if (!collected.trim()) {
          throw new Error(
            "生成未返回正文。常见原因:①当前模型上游不可用(503) ②推理模型把字数耗在思考上。请到个人中心把「章节生成」换成可用的非推理模型后重试"
          );
        }
        return done;
      },
    });
  }

  async function useAiOutput(replace: boolean) {
    if (!editor) return;
    if (replace) {
      editor.commands.setContent(textToEditorHtml(aiOutput));
    } else {
      editor.chain().focus().insertContent("<p></p>" + textToEditorHtml(aiOutput)).run();
    }
    setDirty(true);
  }

  async function adoptAndSave() {
    if (!editor || !aiVersionId) return;
    editor.commands.setContent(textToEditorHtml(aiOutput));
    setDirty(true);
    await save();
    try {
      const updated = await api.post<Chapter>(`/novels/${id}/chapters/${chapterId}/adopt`, { version_id: aiVersionId });
      setChapter(updated);
      setNotice("已采纳 AI 稿");
      setTimeout(() => setNotice(""), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "采纳失败");
    }
  }

  async function saveMeta() {
    setError("");
    try {
      const updated = await api.put<Chapter>(`/novels/${id}/chapters/${chapterId}`, {
        title: metaForm.title,
        outline: metaForm.outline || null,
        foreshadow_note: metaForm.foreshadow_note || null,
        characters_involved: metaForm.characters_involved,
      });
      setChapter(updated);
      setMetaOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function openReports() {
    setReports(await api.get<ReportInfo[]>(`/novels/${id}/chapters/${chapterId}/reports`));
    setReportsOpen(true);
  }

  function runAnalysis() {
    if (analyzing || !chapter) return;
    startTask({
      key: `analyze-${chapterId}`,
      label: `复盘:${chapter.title}`,
      href: `/novels/${id}/chapters/${chapterId}`,
      run: async () => {
        await api.post(`/novels/${id}/chapters/${chapterId}/analyze`);
        return true;
      },
    });
  }

  function toggleCharacter(cid: number) {
    setMetaForm((prev) => ({
      ...prev,
      characters_involved: prev.characters_involved.includes(cid)
        ? prev.characters_involved.filter((x) => x !== cid)
        : [...prev.characters_involved, cid],
    }));
  }

  async function fixSelectedIssues() {
    if (!chapter || !selfCheck || aiBusy || fixing || !canGenerate) return;
    const picked = selfCheck.issues
      .map((issue, idx) => ({ issue, idx }))
      .filter(({ idx }) => fixSelected[idx]);
    if (picked.length === 0) {
      setError("请至少勾选一处建议");
      return;
    }
    setFixing(true);
    setError("");
    try {
      // 生成读库内正文:有未保存改动时先保存,避免改到旧稿
      if (dirty) {
        const ok = await save();
        if (!ok) {
          setFixing(false);
          return;
        }
      }
      const payload = {
        issues: picked.map(({ issue, idx }) => ({
          kind: issue.kind,
          title: issue.title,
          detail: issue.detail || "",
          advice: (fixAdvice[idx] ?? issue.advice ?? "").trim(),
          excerpt: issue.excerpt || null,
          hits: issue.hits || [],
        })),
      };
      const preview = await api.post<{ instruction: string; issue_count: number }>(
        `/novels/${id}/chapters/${chapterId}/self-check/fix-preview`,
        payload
      );
      setNotice(`已按 ${preview.issue_count} 处建议启动定点修改`);
      setTimeout(() => setNotice(""), 4000);
      generate({
        mode: "polish",
        instruction: preview.instruction,
        label: `自检改:${chapter.title}`,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "定点修改失败");
    } finally {
      setFixing(false);
    }
  }

  const aiDraftVersion = chapter.versions.find((v) => v.kind === "ai_draft");

  return (
    <div className="relative flex h-[calc(100dvh-7.5rem)] flex-col gap-3 md:h-[calc(100dvh-8rem)] md:flex-row md:gap-4">
      {/* 左:编辑器 + 章末自检 */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-3 py-2 sm:px-4 sm:py-2.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="truncate font-semibold">{chapter.title}</h2>
            <ChapterStatusBadge status={chapter.status} />
            <span className="text-xs text-slate-400">{editor?.getText().length || 0} 字</span>
            {dirty && <span className="text-xs text-amber-500">未保存</span>}
            {notice && <span className="text-xs text-emerald-600">{notice}</span>}
            {selfCheck && (
              <span
                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                  selfCheck.score >= 85
                    ? "bg-emerald-50 text-emerald-700"
                    : selfCheck.score >= 70
                      ? "bg-amber-50 text-amber-700"
                      : "bg-rose-50 text-rose-700"
                }`}
              >
                自检 {selfCheck.score} 分
              </span>
            )}
          </div>
          <div className="-mx-1 flex max-w-full items-center gap-1.5 overflow-x-auto px-1 pb-0.5">
            <Button size="sm" className="md:hidden" onClick={() => setAiPanelOpen(true)}>
              AI{aiBusy ? "…" : ""}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setMetaOpen(true)}>章纲</Button>
            <Button size="sm" variant="ghost" onClick={() => setVersionsOpen(true)}>版本</Button>
            <Button size="sm" variant="ghost" className="hidden sm:inline-flex" onClick={openReports}>复盘</Button>
            <Button size="sm" variant="ghost" onClick={() => runSelfCheck()} disabled={selfChecking}>
              {selfChecking ? "自检中…" : "自检"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void reparagraphChapter()} disabled={reparagraphing}>
              {reparagraphing ? "AI分段中…" : "AI分段"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="hidden sm:inline-flex"
              onClick={() => router.push(`/novels/${id}/chat?chapter_id=${chapterId}`)}
            >
              书内对话
            </Button>
            <Button size="sm" variant="outline" onClick={save}>保存</Button>
            {canSubmit && <Button size="sm" onClick={submitReview}>提交审核</Button>}
            {chapter.status === "pending_review" && (
              <Button size="sm" variant="outline" onClick={withdraw}>撤回</Button>
            )}
            {(chapter.status === "approved" || chapter.status === "published") && (
              <Button
                size="sm"
                variant="ghost"
                className="hidden sm:inline-flex"
                onClick={() => {
                  window.open(readerNovelUrl(id, chapterId), "_blank", "noopener,noreferrer");
                }}
              >
                阅读器查看
              </Button>
            )}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {rejectComment && (
            <div className="mx-4 mt-3 rounded-lg border border-rose-200 bg-rose-50/90 px-3 py-2.5">
              <div className="mb-1 flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-rose-800">审核驳回意见</p>
                {canGenerate && (
                  <button
                    type="button"
                    className="text-[11px] text-indigo-600 hover:underline"
                    onClick={() => {
                      setMode("fix_reject");
                      setInstruction(rejectComment.slice(0, 2000));
                      setAiPanelOpen(true);
                    }}
                  >
                    用「按驳回改」修改 →
                  </button>
                )}
              </div>
              <pre className="max-h-36 overflow-auto whitespace-pre-wrap text-xs leading-5 text-rose-900/85">
                {rejectComment}
              </pre>
            </div>
          )}
          <EditorContent editor={editor} />
          {(selfCheck || selfChecking) && (
            <div className="mx-4 mb-4 mt-2 rounded-xl border border-amber-200 bg-amber-50/80 px-4 py-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-amber-900">
                  【自检】{chapter.title}
                </h3>
                <span className="text-lg font-bold tabular-nums text-amber-800">
                  {selfChecking && !selfCheck ? "…" : `${selfCheck?.score ?? "—"}分`}
                </span>
              </div>
              {selfCheck && (
                <>
                  <p className="text-xs leading-5 text-amber-900/80">{selfCheck.summary}</p>
                  {selfCheck.issues.length > 0 && (
                    <>
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2 text-[11px] text-amber-900/70">
                          <button
                            type="button"
                            className="underline-offset-2 hover:underline"
                            onClick={() => setFixSelected(selfCheck.issues.map(() => true))}
                          >
                            全选
                          </button>
                          <span>·</span>
                          <button
                            type="button"
                            className="underline-offset-2 hover:underline"
                            onClick={() => setFixSelected(selfCheck.issues.map(() => false))}
                          >
                            清空
                          </button>
                          <span>
                            已选 {fixSelected.filter(Boolean).length}/{selfCheck.issues.length}
                          </span>
                        </div>
                        <Button
                          size="sm"
                          onClick={() => void fixSelectedIssues()}
                          disabled={
                            fixing ||
                            aiBusy ||
                            !canGenerate ||
                            fixSelected.filter(Boolean).length === 0
                          }
                        >
                          {fixing || aiBusy ? "AI 修改中…" : "AI 改选中处"}
                        </Button>
                      </div>
                      <ul className="mt-3 space-y-2.5">
                        {selfCheck.issues.map((issue, idx) => (
                          <li
                            key={`${issue.kind}-${idx}`}
                            className={`rounded-lg border px-3 py-2 ${
                              fixSelected[idx]
                                ? "border-amber-200 bg-white/90"
                                : "border-amber-100/80 bg-white/40 opacity-70"
                            }`}
                          >
                            <label className="flex cursor-pointer items-start gap-2">
                              <input
                                type="checkbox"
                                className="mt-1"
                                checked={!!fixSelected[idx]}
                                onChange={(e) =>
                                  setFixSelected((prev) => {
                                    const next = [...prev];
                                    next[idx] = e.target.checked;
                                    return next;
                                  })
                                }
                              />
                              <div className="min-w-0 flex-1">
                                <div className="text-sm font-medium text-slate-800">{issue.title}</div>
                                {issue.detail && (
                                  <p className="mt-0.5 text-xs leading-5 text-slate-600">{issue.detail}</p>
                                )}
                                {issue.hits && issue.hits.length > 0 && (
                                  <p className="mt-1 text-[11px] text-slate-500">
                                    命中:{issue.hits.slice(0, 8).join("、")}
                                    {issue.hits.length > 8 ? "…" : ""}
                                  </p>
                                )}
                                {issue.excerpt && (
                                  <p className="mt-1 truncate text-[11px] text-slate-400">
                                    原文片段:{issue.excerpt}
                                  </p>
                                )}
                              </div>
                            </label>
                            <Textarea
                              rows={2}
                              className="mt-2 text-xs"
                              value={fixAdvice[idx] ?? issue.advice}
                              onChange={(e) =>
                                setFixAdvice((prev) => {
                                  const next = [...prev];
                                  next[idx] = e.target.value;
                                  return next;
                                })
                              }
                              placeholder="可改写这条修改要求,再点「AI 改选中处」"
                              disabled={!fixSelected[idx]}
                            />
                          </li>
                        ))}
                      </ul>
                      {!canGenerate && (
                        <p className="mt-2 text-[11px] text-amber-800/70">
                          当前章节状态不可生成;撤回审核或回到草稿后再用定点修改。
                        </p>
                      )}
                    </>
                  )}
                  <p className="mt-3 text-[11px] text-amber-800/60">
                    自检是镜子不是关卡:正文已照常保存。勾选并改写建议后,点「AI 改选中处」只动这几处,结果在右侧预览,再决定替换/采纳。
                  </p>
                </>
              )}
            </div>
          )}
        </div>
        {(chapter.status === "approved" || chapter.status === "published") && (
          <div className="border-t border-emerald-100 bg-emerald-50 px-4 py-2 text-xs text-emerald-700">
            本章已审核通过,修改后需重新提交审核才会生效到阅读器。
          </div>
        )}
      </div>

      {/* 手机:AI 抽屉遮罩 */}
      {aiPanelOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          aria-label="关闭 AI 面板"
          onClick={() => setAiPanelOpen(false)}
        />
      )}

      {/* 右:AI 面板 — 桌面常驻;手机底部/侧滑抽屉 */}
      <div
        className={`flex flex-col border-slate-200 bg-white ${
          aiPanelOpen ? "fixed inset-x-0 bottom-0 z-50 h-[min(88dvh,720px)] rounded-t-2xl border-t shadow-2xl md:static md:z-auto md:h-auto md:rounded-xl md:border md:shadow-none" : "hidden md:flex"
        } md:w-96 md:shrink-0 md:rounded-xl md:border`}
      >
        <div className="border-b border-slate-100 px-4 py-3">
          <div className="mb-2 flex items-center justify-between md:mb-0">
            <h3 className="text-sm font-semibold">AI 写作</h3>
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-50 md:hidden"
              onClick={() => setAiPanelOpen(false)}
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
          <div className={`mt-2 grid gap-1 ${visibleModes.length >= 5 ? "grid-cols-5" : "grid-cols-4"}`}>
            {visibleModes.map((m) => (
              <button
                key={m}
                disabled={!canGenerate}
                onClick={() => {
                  setMode(m);
                  if (m === "fix_reject" && rejectComment && !instruction.trim()) {
                    setInstruction(rejectComment.slice(0, 2000));
                  }
                }}
                className={`rounded-md px-1 py-1.5 text-xs ${mode === m ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"} disabled:opacity-40`}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>
          {mode === "fix_reject" && (
            <p className="mt-1.5 text-[11px] leading-4 text-rose-700/80">
              将按上方驳回意见修改正文;可在指令框补充作者要求。生成后在下方预览再替换/采纳。
            </p>
          )}
          <Textarea
            rows={mode === "fix_reject" ? 4 : 2}
            className="mt-2"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder={
              mode === "fix_reject"
                ? "驳回意见已填入,可再补充你想强调的改法"
                : "本次生成指令(可选):如 加一段打斗,突出林晚舟的冷静"
            }
          />
          <Button className="mt-2 w-full" onClick={generate} disabled={aiBusy || !canGenerate}>
            {aiBusy ? "生成中…" : aiError ? "重试生成" : mode === "fix_reject" ? "按驳回建议修改" : "开始生成"}
          </Button>
          {!canGenerate && (
            <p className="mt-1 text-xs text-slate-400">当前状态不支持生成(审核通过后如需改动,请先重新提交审核流程)</p>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3" ref={outputRef}>
          {aiModel && <p className="mb-2 text-xs text-slate-400">模型:{aiModel}</p>}
          {aiError && (
            <div className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm leading-6 text-red-700">
              <div className="mb-1 flex items-start justify-between gap-2">
                <span className="font-medium">生成失败</span>
                <button
                  type="button"
                  className="shrink-0 text-xs text-red-400 hover:text-red-600"
                  onClick={() => clearTask(`gen-${chapterId}`)}
                >
                  关闭
                </button>
              </div>
              <p className="whitespace-pre-wrap break-words text-xs">{aiError}</p>
            </div>
          )}
          {aiReasoning && (
            <details open={!aiOutput} className="mb-2 rounded-lg bg-slate-50 p-2">
              <summary className="cursor-pointer select-none text-xs text-slate-400">
                {aiBusy && !aiOutput ? "思考中…" : "思考过程"}
              </summary>
              <div className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-slate-400">
                {aiReasoning}
              </div>
            </details>
          )}
          {aiOutput ? (
            <pre className="whitespace-pre-wrap text-sm leading-7 text-slate-700">{aiOutput}</pre>
          ) : (
            !aiBusy &&
            !aiError && (
              <p className="pt-6 text-center text-xs text-slate-400">
                AI 生成的内容会显示在这里
                <br />
                长篇策略:规则 + 人物卡 + 滚动前情 + 邻近章纲 + 上章短尾巴
                <br />
                不会把全书前文塞进模型,可支撑百万字连载
              </p>
            )
          )}
        </div>
        {aiOutput && (
          <div className="grid grid-cols-3 gap-1.5 border-t border-slate-100 p-3">
            <Button size="sm" variant="outline" onClick={() => useAiOutput(true)}>替换正文</Button>
            <Button size="sm" variant="outline" onClick={() => useAiOutput(false)}>追加正文</Button>
            {aiVersionId && canGenerate && (
              <Button size="sm" onClick={adoptAndSave}>采纳并保存</Button>
            )}
          </div>
        )}
        {aiDraftVersion && (
          <div className="border-t border-slate-100 px-3 py-2 text-xs text-slate-400">
            已有 AI 稿:{aiDraftVersion.word_count} 字({aiDraftVersion.model_name || "未知模型"})
          </div>
        )}
      </div>

      <ErrorModal error={error} onClose={() => setError("")} />

      {/* 章纲编辑 */}
      <Modal open={metaOpen} title="章纲与章节信息" onClose={() => setMetaOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>章节标题</Label>
            <Input value={metaForm.title} onChange={(e) => setMetaForm({ ...metaForm, title: e.target.value })} />
          </div>
          <div>
            <Label>本章目标 / 剧情要点</Label>
            <Textarea rows={4} value={metaForm.outline} onChange={(e) => setMetaForm({ ...metaForm, outline: e.target.value })} />
          </div>
          <div>
            <Label>伏笔任务(需埋 / 需回收)</Label>
            <Textarea rows={2} value={metaForm.foreshadow_note} onChange={(e) => setMetaForm({ ...metaForm, foreshadow_note: e.target.value })} />
          </div>
          <div>
            <Label>出场人物(生成时注入其人物卡)</Label>
            <div className="flex flex-wrap gap-2">
              {characters.map((c) => (
                <button
                  key={c.id}
                  onClick={() => toggleCharacter(c.id)}
                  className={`rounded-full px-2.5 py-1 text-xs ${metaForm.characters_involved.includes(c.id) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                  {c.name}
                </button>
              ))}
              {characters.length === 0 && <span className="text-xs text-slate-400">还没有人物,去设定库添加</span>}
            </div>
          </div>
          <Button onClick={saveMeta} disabled={!metaForm.title.trim()} className="w-full">保存</Button>
        </div>
      </Modal>

      {/* AI 复盘 */}
      <Modal open={reportsOpen} title="章节 AI 复盘" onClose={() => setReportsOpen(false)} wide>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-slate-400">检查钩子、伏笔、节奏、人物一致性与前后矛盾(需在个人中心配置「复盘分析」用途的模型)</p>
            <Button size="sm" onClick={runAnalysis} disabled={analyzing}>
              {analyzing ? "复盘生成中,约 30-60 秒…" : "生成本章复盘"}
            </Button>
          </div>
          {reports.length === 0 ? (
            <p className="py-6 text-center text-xs text-slate-400">还没有复盘报告</p>
          ) : (
            reports.map((r) => (
              <div key={r.id} className="rounded-lg bg-slate-50 p-3">
                <div className="mb-1 text-xs text-slate-400">
                  {new Date(r.created_at).toLocaleString("zh-CN")} · {r.model_name || "未知模型"}
                </div>
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-sm leading-6 text-slate-700">{r.content}</pre>
              </div>
            ))
          )}
        </div>
      </Modal>

      {/* 版本历史 */}
      <Modal open={versionsOpen} title="版本历史" onClose={() => setVersionsOpen(false)}>        <div className="space-y-2">
          {[...chapter.versions].reverse().map((v: Version) => (
            <div key={v.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
              <div>
                <span className="font-medium">{v.kind === "ai_draft" ? "AI 初稿" : "人工编辑"}</span>
                <span className="ml-2 text-xs text-slate-400">
                  {v.word_count} 字 · {new Date(v.created_at).toLocaleString("zh-CN")}
                </span>
                {v.model_name && <div className="text-xs text-slate-400">{v.model_name}</div>}
              </div>
              {chapter.current_version_id === v.id ? (
                <span className="text-xs text-emerald-600">当前版本</span>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    const updated = await api.post<Chapter>(`/novels/${id}/chapters/${chapterId}/adopt`, { version_id: v.id });
                    setChapter(updated);
                    const c = await api.get<{ content: string }>(`/novels/${id}/chapters/${chapterId}/content`);
                    editor?.commands.setContent(textToEditorHtml(c.content));
                    setDirty(false);
                  }}
                >
                  恢复此版本
                </Button>
              )}
            </div>
          ))}
          {chapter.versions.length === 0 && <p className="py-6 text-center text-xs text-slate-400">还没有版本</p>}
        </div>
      </Modal>
    </div>
  );
}

function ErrorModal({ error, onClose }: { error: string; onClose: () => void }) {
  if (!error) return null;
  return (
    <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-red-600 px-4 py-2 text-sm text-white shadow-lg" onClick={onClose}>
      {error}
    </div>
  );
}

/** 转义后写入 TipTap,避免正文里的 <>& 被当成标签。 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** TipTap HTML → 纯文本。段与段之间空一行,阅读器/复载时更稳。 */
function htmlToText(html: string): string {
  if (typeof document === "undefined") return html;
  const div = document.createElement("div");
  div.innerHTML = html;
  const blocks = Array.from(div.querySelectorAll("p, h1, h2, h3, h4, li, blockquote, pre"));
  if (blocks.length === 0) {
    return (div.textContent || "").replace(/\u00a0/g, " ").trim();
  }
  return blocks
    .map((el) => (el.textContent || "").replace(/\u00a0/g, " ").replace(/\s+$/g, ""))
    .filter((line) => line.length > 0)
    .join("\n\n")
    .trim();
}

/** 库里的纯文本 / AI 输出 → TipTap 段落 HTML。已是 HTML 则原样用。 */
function textToEditorHtml(text: string): string {
  const raw = (text || "").replace(/\r\n/g, "\n").trim();
  if (!raw) return "<p></p>";
  if (/^\s*</.test(raw) && /<\/(p|h[1-6]|div|li|blockquote)>/i.test(raw)) {
    return raw;
  }
  return raw
    .split(/\n+/)
    .map((p) => p.replace(/[ \t]+$/g, ""))
    .filter((p) => p.length > 0)
    .map((p) => `<p>${escapeHtml(p)}</p>`)
    .join("");
}
