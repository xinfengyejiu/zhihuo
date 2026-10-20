"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { api, streamSSE } from "@/lib/api";
import { useTasks } from "@/lib/tasks";
import { filterSkillsForChat } from "@/lib/product";
import type { SkillInfo } from "@/lib/types";
import { Button, ChapterStatusBadge, Empty, ErrorText, Input, Modal, Spinner, Textarea } from "@/components/ui";
import DiffView from "@/components/DiffView";

interface ReviewItem {
  id: number;
  novel_id: number;
  novel_title: string;
  title: string;
  status: string;
  word_count: number;
  updated_at: string;
}

interface ReviewDetail {
  chapter: {
    id: number;
    novel_id: number;
    novel_title: string;
    title: string;
    status: string;
    word_count: number;
    outline: string | null;
    foreshadow_note: string | null;
  };
  current: { id: number; content: string } | null;
  ai_draft: { id: number; content: string } | null;
  last_reject_comment?: string | null;
  last_reject_at?: string | null;
  latest_ai_report?: { id: number; content: string; created_at: string } | null;
  records: { id: number; action: string; comment: string | null; created_at: string }[];
}

const ACTION_LABELS: Record<string, string> = {
  submitted: "提交审核",
  approved: "审核通过",
  rejected: "驳回",
  reopened: "退回修改",
};

const REVIEW_STAGES = new Set(["review", "analysis"]);

type ReviewBoard = {
  pending: ReviewItem[];
  approved: ReviewItem[];
  rejected: ReviewItem[];
  approved_total?: number;
};

function groupByNovel(items: ReviewItem[]): { novelId: number; novelTitle: string; chapters: ReviewItem[] }[] {
  const map = new Map<number, { novelId: number; novelTitle: string; chapters: ReviewItem[] }>();
  for (const c of items) {
    let g = map.get(c.novel_id);
    if (!g) {
      g = { novelId: c.novel_id, novelTitle: c.novel_title, chapters: [] };
      map.set(c.novel_id, g);
    }
    g.chapters.push(c);
  }
  return Array.from(map.values());
}

export default function ReviewPage() {
  const [data, setData] = useState<ReviewBoard | null>(null);
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectComment, setRejectComment] = useState("");
  const [acting, setActing] = useState<"approve" | "reject" | "reopen" | null>(null);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<number[]>([]);
  const [reviewInstruction, setReviewInstruction] = useState("");
  const [skillsMsg, setSkillsMsg] = useState("");
  const [skillsOpen, setSkillsOpen] = useState(true);
  const [reportExpanded, setReportExpanded] = useState(false);
  const reportRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const { getTask, startTask } = useTasks();

  const reviewTaskKey = detail ? `ai-review-${detail.chapter.id}` : "";
  const reviewTask = reviewTaskKey ? getTask(reviewTaskKey) : undefined;
  const reviewing = reviewTask?.status === "running";
  const reviewOutput = reviewTask?.output ?? "";
  const reviewReasoning = reviewTask?.reasoning ?? "";
  const reviewError = reviewTask?.status === "error" ? reviewTask.error || "审查失败" : "";
  const reviewNotice = typeof reviewTask?.meta?.notice === "string" ? reviewTask.meta.notice : "";
  // 有正文用正文;否则把思考过程也当作报告展示(推理模型常见)
  const liveReport = reviewOutput.trim() || reviewReasoning.trim() || "";
  // 驳回后重开:优先展示驳回意见(即当时用报告驳回的内容);其次落库的 AI 审查报告
  const savedProblems =
    (detail?.last_reject_comment || "").trim() ||
    (detail?.latest_ai_report?.content || "").trim() ||
    "";
  const reviewReport = liveReport || savedProblems;
  const reviewDone = (reviewTask?.status === "done" && !!liveReport) || (!!savedProblems && !liveReport);
  const showingSavedProblems = !liveReport && !!savedProblems;

  const load = useCallback(async () => {
    const res = await api.get<Partial<ReviewBoard>>("/review/pending");
    setData({
      pending: res.pending || [],
      approved: res.approved || [],
      rejected: res.rejected || [],
      approved_total: res.approved_total ?? (res.approved || []).length,
    });
    setLoading(false);
  }, []);

  const loadSkills = useCallback(async () => {
    try {
      const all = await api.get<SkillInfo[]>("/skills");
      const reviewSkills = filterSkillsForChat(all, "novel").filter((s) => REVIEW_STAGES.has(s.stage));
      setSkills(reviewSkills);
      setSelectedSkillIds((prev) => {
        if (prev.length) return prev.filter((id) => reviewSkills.some((s) => s.id === id));
        const preferred = reviewSkills.filter((s) => s.name.includes("AI味") || s.name.includes("审查官"));
        if (preferred.length) return preferred.map((s) => s.id);
        return reviewSkills.slice(0, 1).map((s) => s.id);
      });
    } catch {
      setSkills([]);
    }
  }, []);

  useEffect(() => {
    load();
    loadSkills();
  }, [load, loadSkills]);

  useEffect(() => {
    if (!stickToBottom.current || !reportRef.current) return;
    const el = reportRef.current;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [reviewOutput, reviewReasoning, reviewing]);

  // 有报告后默认收起技能区,把空间留给报告阅读
  useEffect(() => {
    if (reviewReport && !reviewing) setSkillsOpen(false);
  }, [reviewReport, reviewing]);

  async function openDetail(id: number) {
    setError("");
    setActing(null);
    setReviewInstruction("");
    setReportExpanded(false);
    setSkillsOpen(true);
    setDetail(await api.get<ReviewDetail>(`/review/chapters/${id}`));
    stickToBottom.current = true;
  }

  async function approve() {
    if (!detail || acting) return;
    setError("");
    setActing("approve");
    try {
      await api.post(`/review/chapters/${detail.chapter.id}/approve`, {});
      setDetail(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActing(null);
    }
  }

  async function reject() {
    if (!detail || acting) return;
    setError("");
    setActing("reject");
    try {
      await api.post(`/review/chapters/${detail.chapter.id}/reject`, { comment: rejectComment });
      setRejectOpen(false);
      setRejectComment("");
      setDetail(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActing(null);
    }
  }

  async function reopenChapter(chapterId: number, fromDetail = false) {
    if (acting) return;
    if (!confirm("退回后本章将退出阅读器与发布列表,需改完再重新提交审核。确定吗?")) return;
    setError("");
    setActing("reopen");
    try {
      await api.post(`/review/chapters/${chapterId}/reopen`, {});
      if (fromDetail) setDetail(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActing(null);
    }
  }

  function toggleSkill(id: number) {
    setSelectedSkillIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  async function ensureReviewSkills() {
    setSkillsMsg("");
    try {
      const res = await api.post<{ created: number }>("/skills/seed-presets");
      await loadSkills();
      setSkillsMsg(res.created > 0 ? `已导入 ${res.created} 个预置技能(含审查官)` : "预置技能已就绪");
    } catch (e) {
      setSkillsMsg(e instanceof Error ? e.message : "导入失败");
    }
  }

  function runAiReview() {
    if (!detail || reviewing) return;
    const chapterId = detail.chapter.id;
    const novelId = detail.chapter.novel_id;
    const instruction = reviewInstruction;
    const skillIds = selectedSkillIds;
    stickToBottom.current = true;
    startTask({
      key: `ai-review-${chapterId}`,
      label: `AI审查:${detail.chapter.title}`,
      href: "/review",
      run: async (r) => {
        let collected = "";
        let reasoningCollected = "";
        let reportId: number | null = null;
        let doneContent = "";
        await streamSSE(
          `/review/chapters/${chapterId}/ai-review`,
          { skill_ids: skillIds, instruction: instruction || null, save: true },
          (ev) => {
            if (ev.type === "delta") {
              const piece = String(ev.content ?? "");
              collected += piece;
              r.pushOutput(piece);
            } else if (ev.type === "reasoning") {
              const piece = String(ev.content ?? "");
              reasoningCollected += piece;
              r.pushReasoning(piece);
            } else if (ev.type === "notice") {
              r.setMeta({ notice: String(ev.detail ?? "") });
            } else if (ev.type === "error") {
              throw new Error(typeof ev.detail === "string" ? ev.detail : "审查失败");
            } else if (ev.type === "done") {
              reportId = typeof ev.report_id === "number" ? ev.report_id : null;
              if (typeof ev.content === "string" && ev.content.trim()) {
                doneContent = ev.content.trim();
              }
              r.setResult(ev);
            }
          }
        );

        // done 事件带了完整正文但流式 delta 丢了 → 直接回填
        if (!collected.trim() && doneContent) {
          r.pushOutput(doneContent);
          collected = doneContent;
        }

        // 仍无正文时,尝试从已落库报告拉取
        if (!collected.trim() && reportId) {
          try {
            const reports = await api.get<{ id: number; content: string }[]>(
              `/novels/${novelId}/chapters/${chapterId}/reports`
            );
            const hit = reports.find((x) => x.id === reportId) || reports[0];
            if (hit?.content) {
              r.pushOutput(hit.content);
              collected = hit.content;
            }
          } catch {
            /* ignore */
          }
        }

        // 推理模型:思考过程也算结果
        if (!collected.trim() && reasoningCollected.trim()) {
          r.pushOutput(reasoningCollected);
          collected = reasoningCollected;
        }

        if (!collected.trim()) {
          throw new Error("审查未生成内容。请换非推理模型,或到个人中心把「复盘分析」max_tokens 调到 8192+");
        }
        return { report_id: reportId };
      },
    });
  }

  function fillRejectFromReport() {
    if (!reviewReport.trim()) return;
    setRejectComment(reviewReport.trim().slice(0, 2000));
    setRejectOpen(true);
  }

  /** 一键用 AI 审查报告直接驳回(意见=报告全文) */
  async function rejectWithReport() {
    if (!detail || acting || !reviewReport.trim()) return;
    const comment = reviewReport.trim().slice(0, 8000);
    if (!confirm("将用当前 AI 审查报告作为驳回意见并直接驳回,确定吗?")) return;
    setRejectComment(comment);
    setError("");
    setActing("reject");
    try {
      await api.post(`/review/chapters/${detail.chapter.id}/reject`, { comment });
      setRejectOpen(false);
      setRejectComment("");
      setDetail(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActing(null);
    }
  }

  if (loading) return <Spinner />;
  const pending = data?.pending || [];
  const approved = data?.approved || [];
  const rejected = data?.rejected || [];

  function renderNovelGroups(
    items: ReviewItem[],
    emptyText: string,
    renderRow: (c: ReviewItem) => ReactNode
  ) {
    if (items.length === 0) return <Empty text={emptyText} />;
    return (
      <div className="space-y-4">
        {groupByNovel(items).map((g) => (
          <div key={g.novelId}>
            <h3 className="mb-1.5 px-0.5 text-sm font-semibold text-slate-800">《{g.novelTitle}》</h3>
            <div className="space-y-2">{g.chapters.map((c) => renderRow(c))}</div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-2">
        <h1 className="text-xl font-bold">审核台</h1>
        <span className="hidden text-sm text-slate-400 sm:inline">章节必须审核通过后才会进入发布列表和阅读器</span>
      </div>
      <ErrorText>{error}</ErrorText>

      <h2 className="mb-2 text-sm font-medium text-slate-600">待审核({pending.length})</h2>
      <div className="mb-8">
        {renderNovelGroups(pending, "没有待审核的章节", (c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => openDetail(c.id)}
            className="flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 text-left hover:border-indigo-300"
          >
            <span className="font-medium text-slate-700">{c.title}</span>
            <div className="flex items-center gap-3">
              <span className="text-xs text-slate-400">{c.word_count} 字</span>
              <ChapterStatusBadge status={c.status} />
            </div>
          </button>
        ))}
      </div>

      <h2 className="mb-2 text-sm font-medium text-slate-600">
        已审核({data?.approved_total ?? approved.length})
        <span className="ml-2 font-normal text-slate-400">每本展示最近通过的 3 章</span>
      </h2>
      <div className="mb-8">
        {renderNovelGroups(approved, "暂无已通过的章节", (c) => (
          <div
            key={c.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-emerald-100 bg-emerald-50/60 px-4 py-3"
          >
            <button type="button" onClick={() => openDetail(c.id)} className="min-w-0 flex-1 text-left">
              <span className="font-medium text-emerald-900 hover:underline">{c.title}</span>
              <span className="ml-2 text-xs text-slate-400">{c.word_count} 字</span>
            </button>
            <div className="flex shrink-0 items-center gap-2">
              <ChapterStatusBadge status={c.status} />
              {c.status === "approved" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!acting}
                  onClick={() => void reopenChapter(c.id)}
                >
                  退回修改
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>

      <h2 className="mb-2 text-sm font-medium text-slate-600">审核拒绝({rejected.length})</h2>
      <div className="mb-6">
        {renderNovelGroups(rejected, "没有被驳回的章节", (c) => (
          <div
            key={c.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-red-100 bg-red-50 px-4 py-3"
          >
            <button type="button" onClick={() => openDetail(c.id)} className="min-w-0 flex-1 text-left">
              <span className="font-medium text-red-700 hover:underline">{c.title}</span>
              <span className="ml-2 text-xs text-slate-400">点此查看存在的问题</span>
            </button>
            <div className="flex shrink-0 items-center gap-2">
              <ChapterStatusBadge status="rejected" />
              <Link
                href={`/novels/${c.novel_id}/chapters/${c.id}`}
                className="rounded-lg border border-red-200 bg-white px-2.5 py-1 text-xs text-red-700 hover:bg-red-50"
              >
                去改稿
              </Link>
            </div>
          </div>
        ))}
      </div>

      <Modal open={!!detail} title={detail ? `审核:《${detail.chapter.novel_title}》${detail.chapter.title}` : ""} onClose={() => !acting && setDetail(null)} wide tall>
        {detail && (
          <div className="relative flex min-h-0 flex-1 flex-col gap-3">
            {acting && (
              <div className="absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-white/70 backdrop-blur-[1px]">
                <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600 shadow-sm">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-500" />
                  {acting === "approve" ? "正在通过审核…" : acting === "reopen" ? "正在退回…" : "正在驳回…"}
                </div>
              </div>
            )}
            <div className="shrink-0 space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <ChapterStatusBadge status={detail.chapter.status} />
                <span className="text-slate-500">
                  《{detail.chapter.novel_title}》 · {detail.chapter.word_count} 字
                </span>
                {detail.chapter.status === "rejected" && (
                  <Link
                    href={`/novels/${detail.chapter.novel_id}/chapters/${detail.chapter.id}`}
                    className="text-xs text-indigo-600 hover:underline"
                  >
                    去章节按驳回改 →
                  </Link>
                )}
              </div>
              {detail.chapter.outline && <p className="text-xs text-slate-500">章纲:{detail.chapter.outline}</p>}
              {detail.chapter.foreshadow_note && (
                <p className="text-xs text-amber-600">伏笔任务:{detail.chapter.foreshadow_note}</p>
              )}
              {detail.chapter.status === "rejected" && detail.last_reject_comment && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2">
                  <p className="text-xs font-semibold text-rose-800">
                    存在的问题
                    {detail.last_reject_at
                      ? ` · ${new Date(detail.last_reject_at).toLocaleString("zh-CN")}`
                      : ""}
                  </p>
                  <p className="mt-0.5 text-[11px] text-rose-700/70">右侧面板为完整驳回意见 / 审查报告</p>
                </div>
              )}
            </div>

            <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)] gap-3 lg:grid-cols-2">
              <div className="flex h-full min-h-0 flex-col overflow-hidden">
                <p className="mb-1 shrink-0 text-xs font-medium text-slate-500">
                  {detail.ai_draft && detail.current && detail.ai_draft.content !== detail.current.content
                    ? "AI 初稿 vs 当前稿"
                    : "正文"}
                </p>
                <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm leading-7">
                  {detail.ai_draft && detail.current && detail.ai_draft.content !== detail.current.content ? (
                    <DiffView oldText={detail.ai_draft.content} newText={detail.current.content} className="" />
                  ) : (
                    <div className="whitespace-pre-wrap">{detail.current?.content || "(空)"}</div>
                  )}
                </div>
              </div>

              <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-indigo-100 bg-indigo-50/40">
                <div className="shrink-0 border-b border-indigo-100/80 p-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <h3 className="text-sm font-semibold text-indigo-800">AI 审查官</h3>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button size="sm" onClick={runAiReview} disabled={reviewing}>
                        {reviewing ? "审查中…" : reviewDone ? "重新审查" : "开始 AI 审查"}
                      </Button>
                      {reviewReport && detail.chapter.status === "pending_review" && (
                        <Button size="sm" variant="danger" onClick={() => void rejectWithReport()} disabled={reviewing || !!acting}>
                          {acting === "reject" ? "驳回中…" : "用报告驳回"}
                        </Button>
                      )}
                      {reviewReport && (
                        <Button size="sm" variant="outline" className="hidden sm:inline-flex" onClick={() => setReportExpanded(true)}>
                          展开报告
                        </Button>
                      )}
                      {skills.length === 0 && !reviewing && (
                        <Button size="sm" variant="outline" className="hidden sm:inline-flex" onClick={ensureReviewSkills}>
                          导入审查技能
                        </Button>
                      )}
                      {reviewReport && detail.chapter.status === "pending_review" && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="hidden sm:inline-flex"
                          onClick={fillRejectFromReport}
                          disabled={reviewing || !!acting}
                        >
                          填入意见
                        </Button>
                      )}
                      {/* 手机:次要操作收入下拉 */}
                      <details className="relative sm:hidden">
                        <summary className="list-none cursor-pointer rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600">
                          更多
                        </summary>
                        <div className="absolute right-0 z-10 mt-1 w-36 rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
                          {reviewReport && (
                            <button
                              type="button"
                              className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-50"
                              onClick={() => setReportExpanded(true)}
                            >
                              展开报告
                            </button>
                          )}
                          {skills.length === 0 && !reviewing && (
                            <button
                              type="button"
                              className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-50"
                              onClick={ensureReviewSkills}
                            >
                              导入审查技能
                            </button>
                          )}
                          {reviewReport && detail.chapter.status === "pending_review" && (
                            <button
                              type="button"
                              className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-50"
                              onClick={fillRejectFromReport}
                              disabled={reviewing || !!acting}
                            >
                              填入意见
                            </button>
                          )}
                        </div>
                      </details>
                    </div>
                  </div>

                  {reviewing ? (
                    <div className="mt-2 space-y-1">
                      <p className="truncate text-xs text-slate-500">
                        {selectedSkillIds.length
                          ? `技能:${skills
                              .filter((s) => selectedSkillIds.includes(s.id))
                              .map((s) => s.name)
                              .join("、") || `${selectedSkillIds.length} 项`}`
                          : "未选技能(将用默认审查官)"}
                        {reviewInstruction ? ` · ${reviewInstruction}` : ""}
                      </p>
                      {reviewNotice && <p className="truncate text-xs text-indigo-600">{reviewNotice}</p>}
                      {reviewError && <p className="text-xs text-red-600">{reviewError}</p>}
                    </div>
                  ) : (
                    <div className="mt-2">
                      <button
                        type="button"
                        className="text-[11px] text-indigo-600 hover:underline"
                        onClick={() => setSkillsOpen((v) => !v)}
                      >
                        {skillsOpen ? "收起技能与补充要求 ▲" : "展开技能与补充要求 ▼"}
                        {reviewDone ? ` · 已完成 ${reviewReport.length} 字` : ""}
                      </button>
                      {skillsOpen && (
                        <div className="mt-2 max-h-36 space-y-2 overflow-y-auto">
                          <p className="text-xs leading-5 text-slate-500">
                            勾选审查技能后开跑。报告读起来挤时点右上角「展开报告」。
                          </p>
                          {skillsMsg && <p className="text-xs text-indigo-600">{skillsMsg}</p>}
                          {reviewNotice && <p className="text-xs text-indigo-600">{reviewNotice}</p>}
                          <div className="flex flex-wrap gap-1.5">
                            {skills.length === 0 ? (
                              <span className="text-xs text-slate-400">暂无审查技能,点右上角导入预置</span>
                            ) : (
                              skills.map((s) => {
                                const on = selectedSkillIds.includes(s.id);
                                return (
                                  <button
                                    key={s.id}
                                    type="button"
                                    onClick={() => toggleSkill(s.id)}
                                    className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                                      on
                                        ? "border-indigo-500 bg-indigo-600 text-white"
                                        : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300"
                                    }`}
                                    title={s.system_prompt.slice(0, 120)}
                                  >
                                    {s.name}
                                    <span className="ml-1 opacity-70">{s.stage === "review" ? "审" : "复"}</span>
                                  </button>
                                );
                              })
                            )}
                          </div>
                          <Input
                            value={reviewInstruction}
                            onChange={(e) => setReviewInstruction(e.target.value)}
                            placeholder="补充要求(可选),如:重点看本章打戏是否浮夸"
                          />
                          {reviewError && (
                            <div className="rounded-lg border border-red-100 bg-red-50 p-2 text-xs text-red-600">{reviewError}</div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div
                  ref={reportRef}
                  onScroll={() => {
                    const el = reportRef.current;
                    if (!el) return;
                    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                  }}
                  className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-white p-3 text-sm leading-6 text-slate-700"
                  onDoubleClick={() => reviewReport && setReportExpanded(true)}
                  title={reviewReport ? "双击可展开大窗阅读" : undefined}
                >
                  {reviewReasoning &&
                    !!reviewOutput.trim() &&
                    reviewReasoning.trim() !== reviewOutput.trim() && (
                    <details className="mb-3 rounded-lg bg-slate-50 p-2 text-xs text-slate-500">
                      <summary className="cursor-pointer select-none text-slate-600">思考过程</summary>
                      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{reviewReasoning}</pre>
                    </details>
                  )}
                  {reviewReport ? (
                    <>
                      {showingSavedProblems && (
                        <p className="mb-2 rounded bg-rose-50 px-2 py-1 text-[11px] font-medium text-rose-700">
                          {detail.last_reject_comment
                            ? "已驳回 · 以下为当时驳回意见 / 问题清单"
                            : "以下为最近一次 AI 审查报告"}
                        </p>
                      )}
                      <pre className="whitespace-pre-wrap break-words">{reviewReport}</pre>
                    </>
                  ) : reviewing ? (
                    <div className="flex items-center gap-2 text-slate-400">
                      <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-600" />
                      正在审查…
                    </div>
                  ) : (
                    <p className="text-slate-400">审查报告会出现在这里</p>
                  )}
                </div>
              </div>
            </div>

            {detail.records.length > 0 && (
              <details className="shrink-0 border-t border-slate-100 pt-2">
                <summary className="cursor-pointer select-none text-[11px] font-medium text-slate-500">
                  审核记录({detail.records.length}) · 点击展开
                </summary>
                <div className="mt-1.5 max-h-56 space-y-1.5 overflow-y-auto">
                  {detail.records.map((r) => (
                    <details
                      key={r.id}
                      className={`rounded-lg px-2.5 py-1.5 text-xs ${
                        r.action === "rejected" ? "bg-rose-50 text-rose-800" : "bg-slate-50 text-slate-600"
                      }`}
                    >
                      <summary className="cursor-pointer select-none">
                        {new Date(r.created_at).toLocaleString("zh-CN")} · {ACTION_LABELS[r.action] || r.action}
                        {r.comment && r.action === "rejected" ? " · 存在问题" : ""}
                      </summary>
                      {r.comment && (
                        <pre className="mt-1.5 max-h-[50vh] overflow-auto whitespace-pre-wrap break-words border-t border-black/5 pt-1.5 text-[11px] leading-5 opacity-90">
                          {r.comment}
                        </pre>
                      )}
                    </details>
                  ))}
                </div>
              </details>
            )}

            {reportExpanded && reviewReport && (
              <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-3 sm:p-6">
                <div className="flex h-[min(92vh,920px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl">
                  <div className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
                    <div>
                      <h4 className="text-sm font-semibold text-slate-800">审查报告 · {detail.chapter.title}</h4>
                      <p className="text-[11px] text-slate-400">{reviewReport.length} 字 · 可滚动通读</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {detail.chapter.status === "pending_review" && (
                        <Button size="sm" variant="danger" onClick={() => void rejectWithReport()} disabled={!!acting}>
                          用报告驳回
                        </Button>
                      )}
                      <Button size="sm" variant="outline" onClick={() => setReportExpanded(false)}>
                        收起
                      </Button>
                    </div>
                  </div>
                  <pre className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words px-5 py-4 text-[15px] leading-7 text-slate-800">
                    {reviewReport}
                  </pre>
                </div>
              </div>
            )}

            {detail.chapter.status === "pending_review" && (
              <div className="flex shrink-0 justify-end gap-2 border-t border-slate-100 pt-3">
                <Button variant="outline" onClick={() => setRejectOpen(true)} disabled={!!acting}>
                  驳回
                </Button>
                <Button onClick={approve} disabled={!!acting}>
                  {acting === "approve" ? "通过中…" : "通过审核"}
                </Button>
              </div>
            )}
            {detail.chapter.status === "approved" && (
              <div className="flex shrink-0 justify-end gap-2 border-t border-slate-100 pt-3">
                <Button variant="outline" onClick={() => void reopenChapter(detail.chapter.id, true)} disabled={!!acting}>
                  {acting === "reopen" ? "退回中…" : "退回修改"}
                </Button>
                <Link href={`/novels/${detail.chapter.novel_id}/chapters/${detail.chapter.id}`}>
                  <Button variant="outline">查看章节</Button>
                </Link>
              </div>
            )}
            {detail.chapter.status === "rejected" && (
              <div className="flex shrink-0 justify-end gap-2 border-t border-slate-100 pt-3">
                <Link href={`/novels/${detail.chapter.novel_id}/chapters/${detail.chapter.id}`}>
                  <Button>去改稿(按驳回建议)</Button>
                </Link>
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal open={rejectOpen} title="驳回原因(必填)" onClose={() => !acting && setRejectOpen(false)}>
        <Textarea
          rows={6}
          value={rejectComment}
          onChange={(e) => setRejectComment(e.target.value)}
          placeholder="说明问题,便于作者修改,如:人设不符、节奏拖沓、AI味过重…"
          disabled={!!acting}
        />
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRejectOpen(false)} disabled={!!acting}>
            取消
          </Button>
          <Button variant="danger" onClick={reject} disabled={!rejectComment.trim() || !!acting}>
            {acting === "reject" ? "驳回中…" : "确认驳回"}
          </Button>
        </div>
      </Modal>
    </div>
  );
}
