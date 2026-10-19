"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatMediaContent } from "@/components/ChatMediaContent";
import { api, streamSSE } from "@/lib/api";
import { filterSkillsForChat } from "@/lib/product";
import type { SkillInfo, Storyboard } from "@/lib/types";
import { Button, ErrorText, Label, Modal, Select, Spinner } from "@/components/ui";

export function ChapterStoryboardModal({
  open,
  novelId,
  chapterId,
  chapterTitle,
  onClose,
}: {
  open: boolean;
  novelId: string | number;
  chapterId: number;
  chapterTitle: string;
  onClose: () => void;
}) {
  const [board, setBoard] = useState<Storyboard | null>(null);
  const [frameCount, setFrameCount] = useState(4);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillId, setSkillId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [regenFrameId, setRegenFrameId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const visualSkills = useMemo(() => filterSkillsForChat(skills, "novel"), [skills]);

  const load = useCallback(async () => {
    if (!open || !chapterId) return;
    setLoading(true);
    setError("");
    try {
      const [b, sk] = await Promise.all([
        api.get<Storyboard>(`/novels/${novelId}/chapters/${chapterId}/storyboard`),
        api.get<SkillInfo[]>("/skills"),
      ]);
      setBoard(b);
      if (b.frame_count) setFrameCount(b.frame_count);
      setSkills(sk);
      setSkillId((prev) => {
        if (prev != null && sk.some((s) => s.id === prev)) return prev;
        return null;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [open, novelId, chapterId]);

  useEffect(() => {
    load();
  }, [load]);

  const currentFrameN = board?.frames?.length || 0;
  const countDirty = currentFrameN > 0 && currentFrameN !== frameCount;

  async function plan(count = frameCount) {
    setBusy("plan");
    setError("");
    setStatus(`正在按 ${count} 张规划分镜…`);
    try {
      const b = await api.post<Storyboard>(`/novels/${novelId}/chapters/${chapterId}/storyboard/plan`, {
        frame_count: count,
      });
      setBoard(b);
      setFrameCount(b.frame_count || b.frames.length || count);
      setStatus(`已规划 ${b.frames.length} 个镜头`);
      return b;
    } catch (e) {
      setError(e instanceof Error ? e.message : "规划失败");
      return null;
    } finally {
      setBusy("");
    }
  }

  async function ensurePlannedForCount(): Promise<Storyboard | null> {
    if (board?.frames?.length && board.frames.length === frameCount) {
      return board;
    }
    setStatus(
      board?.frames?.length
        ? `张数已改为 ${frameCount}(当前 ${board.frames.length} 张),正在按新数量重新规划…`
        : `正在按 ${frameCount} 张规划分镜…`
    );
    return plan(frameCount);
  }

  async function runGenerate(opts: {
    regenerateAll?: boolean;
    frameIds?: number[];
    skipReplan?: boolean;
  }) {
    const { regenerateAll = false, frameIds, skipReplan = false } = opts;
    setBusy(frameIds?.length === 1 ? "regen" : "gen");
    if (frameIds?.length === 1) setRegenFrameId(frameIds[0]);
    setError("");
    setStatus("准备生图…");
    try {
      if (!skipReplan && !frameIds?.length) {
        const planned = await ensurePlannedForCount();
        if (!planned?.frames?.length) {
          throw new Error("分镜规划失败,请重试");
        }
        setBusy("gen");
        setStatus(`按 ${planned.frames.length} 张开始生图…`);
      } else if (!board?.frames?.length) {
        throw new Error("还没有分镜,请先规划");
      }

      const payload: {
        regenerate_all: boolean;
        frame_ids?: number[];
        skill_id?: number | null;
      } = {
        regenerate_all: regenerateAll,
        skill_id: skillId,
      };
      if (frameIds?.length) payload.frame_ids = frameIds;

      await streamSSE(`/novels/${novelId}/chapters/${chapterId}/storyboard/generate`, payload, (ev) => {
        if (ev.type === "status") setStatus(String(ev.detail || ""));
        if (ev.type === "frame_start") {
          setStatus(`生成镜头 ${ev.index}/${ev.total}:${ev.caption || ""}`);
        }
        if (ev.type === "error") throw new Error(String(ev.detail || "生图失败"));
      });
      await load();
      setStatus(frameIds?.length === 1 ? "该镜头已重绘" : "分镜图生成完成");
    } catch (e) {
      setError(e instanceof Error ? e.message : "生图失败");
      await load();
    } finally {
      setBusy("");
      setRegenFrameId(null);
    }
  }

  async function generateVideo() {
    setBusy("video");
    setError("");
    setStatus("视频生成中…");
    try {
      await streamSSE(`/novels/${novelId}/chapters/${chapterId}/storyboard/video`, {}, (ev) => {
        if (ev.type === "status") setStatus(String(ev.detail || ""));
        if (ev.type === "video" && ev.url) {
          setBoard((prev) => (prev ? { ...prev, video_url: String(ev.url) } : prev));
        }
        if (ev.type === "error") throw new Error(String(ev.detail || "视频失败"));
      });
      await load();
      setStatus("视频已生成");
    } catch (e) {
      setError(e instanceof Error ? e.message : "视频生成失败");
    } finally {
      setBusy("");
    }
  }

  const skillName = skillId ? visualSkills.find((s) => s.id === skillId)?.name : null;

  return (
    <Modal open={open} title={`分镜 · ${chapterTitle}`} onClose={onClose} wide>
      <div className="space-y-3">
        <p className="text-xs leading-5 text-slate-500">
          定妆/设定稿只用来锁人物长相,剧情图必须画成单幅故事瞬间,不会做成设定稿多格。请先保存全书画风并生成设定稿,再规划分镜并生图。改张数后批量生成会按新数量自动重新规划。
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <Label>镜头张数</Label>
            <select
              className="mt-1 rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
              value={frameCount}
              disabled={!!busy}
              onChange={(e) => setFrameCount(Number(e.target.value))}
            >
              {[2, 3, 4, 5, 6, 8, 10, 12].map((n) => (
                <option key={n} value={n}>
                  {n} 张
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-[10rem]">
            <Label>生图技能</Label>
            <Select
              className="mt-1"
              value={skillId ?? ""}
              disabled={!!busy}
              onChange={(e) => setSkillId(e.target.value ? +e.target.value : null)}
            >
              <option value="">不使用技能</option>
              {visualSkills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.stage ? ` · ${s.stage}` : ""}
                </option>
              ))}
            </Select>
          </div>
          <Button size="sm" onClick={() => plan()} disabled={!!busy}>
            {busy === "plan" ? "规划中…" : "规划分镜"}
          </Button>
          <Button size="sm" variant="outline" onClick={() => runGenerate({})} disabled={!!busy}>
            {busy === "gen" ? "生图中…" : "生成图片"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => runGenerate({ regenerateAll: true })}
            disabled={!!busy || !board?.frames?.length}
          >
            全部重绘
          </Button>
          <Button size="sm" variant="outline" onClick={generateVideo} disabled={!!busy || !board?.frames?.length}>
            {busy === "video" ? "视频中…" : "生成视频"}
          </Button>
        </div>
        {skillName && !busy && (
          <p className="text-xs text-violet-600">当前生图技能:「{skillName}」(批量与单镜重绘均生效)</p>
        )}
        {countDirty && !busy && (
          <p className="text-xs text-amber-600">
            已选 {frameCount} 张,当前分镜仍是 {currentFrameN} 张 — 点「生成图片/全部重绘」将按 {frameCount}{" "}
            张重新规划后再出图。
          </p>
        )}
        {status && <p className="text-xs text-indigo-600">{status}</p>}
        <ErrorText>{error}</ErrorText>
        {loading ? (
          <Spinner />
        ) : !board?.frames?.length ? (
          <p className="py-8 text-center text-xs text-slate-400">
            还没有分镜,选择张数后点「规划分镜」或直接「生成图片」
          </p>
        ) : (
          <div className="max-h-[28rem] space-y-3 overflow-auto">
            {board.frames.map((f) => (
              <div key={f.id} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                <div className="mb-1 flex items-center justify-between gap-2 text-xs text-slate-500">
                  <span className="font-medium text-slate-700">
                    #{f.sort + 1} {f.caption || "镜头"}
                  </span>
                  <div className="flex items-center gap-2">
                    <span>{f.status === "ready" ? "已出图" : f.status === "failed" ? "失败" : "待生成"}</span>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!!busy}
                      onClick={() => runGenerate({ frameIds: [f.id], skipReplan: true })}
                    >
                      {busy === "regen" && regenFrameId === f.id ? "重绘中…" : "重绘此镜"}
                    </Button>
                  </div>
                </div>
                <p className="mb-2 text-xs leading-5 text-slate-600">{f.shot_prompt}</p>
                {f.error && <p className="mb-2 text-xs text-red-500">{f.error}</p>}
                {f.image_url ? (
                  <ChatMediaContent content={`![${f.caption || "分镜"}](${f.image_url})`} />
                ) : (
                  <div className="flex h-28 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-white text-xs text-slate-400">
                    尚未生成
                  </div>
                )}
              </div>
            ))}
            {board.video_url && (
              <div className="rounded-lg border border-slate-200 bg-black/90 p-2">
                <p className="mb-1 px-1 text-xs text-slate-300">章节短视频</p>
                <ChatMediaContent content={`🎥 [章节视频](${board.video_url})`} />
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
