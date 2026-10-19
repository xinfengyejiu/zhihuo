"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ChatMediaContent } from "@/components/ChatMediaContent";
import { api, streamSSE } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { filterSkillsForChat, useProduct } from "@/lib/product";
import type { DramaProject, DramaShot, Novel, SkillInfo } from "@/lib/types";
import { Badge, Button, ErrorText, Input, Label, Select, Spinner, Textarea } from "@/components/ui";

type Tab = "source" | "chat" | "script" | "frames";

interface ChatMsg {
  id?: number;
  role: "user" | "assistant";
  content: string;
}

const SHOT_SIZE_CN: Record<string, string> = {
  ecu: "大特写",
  cu: "特写",
  ms: "中景",
  fs: "全景",
  ls: "远景",
  els: "大远景",
};
const CAMERA_CN: Record<string, string> = {
  static: "固定",
  push: "推进",
  pull: "拉远",
  pan: "横摇",
  tilt: "俯仰",
  follow: "跟随",
  orbit: "环绕",
};
const ANGLE_CN: Record<string, string> = {
  eye: "平视",
  high: "俯拍",
  low: "仰拍",
  over: "过肩",
};

function lensLabel(size?: string | null, move?: string | null, angle?: string | null) {
  const a = SHOT_SIZE_CN[size || ""] || size || "中景";
  const b = CAMERA_CN[move || ""] || move || "固定";
  const c = ANGLE_CN[angle || ""] || angle || "平视";
  return `${a} · ${b} · ${c}`;
}

function timeRange(start?: number | null, end?: number | null) {
  const s = typeof start === "number" ? start : 0;
  const e = typeof end === "number" ? end : s + 5;
  return `${s}–${e}秒`;
}

export default function DramaWorkspacePage() {
  const params = useParams();
  const id = Number(params.id);
  const { user, loading: authLoading } = useAuth();
  const { setCurrent } = useProduct();
  const router = useRouter();

  const [tab, setTab] = useState<Tab>("source");
  const [proj, setProj] = useState<DramaProject | null>(null);
  const [novels, setNovels] = useState<Novel[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillId, setSkillId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState("");
  const [regenId, setRegenId] = useState<number | null>(null);
  const [refineDrafts, setRefineDrafts] = useState<Record<number, string>>({});

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const chatEnd = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const visualSkills = filterSkillsForChat(skills, "drama");

  const load = useCallback(async () => {
    if (!id) return;
    const [p, ns, sk] = await Promise.all([
      api.get<DramaProject>(`/drama/projects/${id}`),
      api.get<Novel[]>("/novels"),
      api.get<SkillInfo[]>("/skills"),
    ]);
    setProj(p);
    setNovels(ns);
    setSkills(sk);
    setSkillId((prev) => {
      if (prev && sk.some((s) => s.id === prev)) return prev;
      const dramaSk = filterSkillsForChat(sk, "drama");
      const preferred =
        dramaSk.find((s) => s.name.includes("Seedance") || s.name.includes("导演级分镜")) ||
        dramaSk.find((s) => s.stage === "script") ||
        dramaSk[0];
      return preferred?.id ?? null;
    });
    setLoading(false);
  }, [id]);

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

  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, tab]);

  async function loadChat() {
    const data = await api.get<{ messages: ChatMsg[] }>(`/drama/projects/${id}/chat`);
    setMessages(data.messages || []);
  }

  useEffect(() => {
    if (tab === "chat" && id) loadChat().catch(() => {});
  }, [tab, id]);

  async function saveSource(patch: Partial<DramaProject>) {
    if (!proj) return;
    setBusy("save");
    setError("");
    try {
      const next = await api.put<DramaProject>(`/drama/projects/${id}`, {
        title: patch.title ?? proj.title,
        source_text: patch.source_text ?? proj.source_text,
        style_prompt: patch.style_prompt ?? proj.style_prompt,
        episode_count: patch.episode_count ?? proj.episode_count,
        novel_id: patch.novel_id === undefined ? undefined : patch.novel_id,
        source_type: patch.source_type,
      });
      setProj(next);
      setStatus("已保存");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy("");
    }
  }

  async function importNovel(novelId: number) {
    setBusy("import");
    setError("");
    try {
      const next = await api.post<DramaProject>(`/drama/projects/${id}/import-novel`, { novel_id: novelId });
      setProj(next);
      setStatus("已从小说导入故事");
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setBusy("");
    }
  }

  async function uploadFile(file: File) {
    setBusy("upload");
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const next = await api.upload<DramaProject>(`/drama/projects/${id}/upload`, form);
      setProj(next);
      setStatus("剧本已上传");
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy("");
    }
  }

  async function generateScript() {
    setBusy("script");
    setError("");
    setStatus("正在生成漫剧圣经…");
    setTab("chat");
    let acc = "";
    let reasoning = "";
    try {
      // 先拉已有对话,再挂上本次流式气泡
      try {
        const data = await api.get<{ messages: ChatMsg[] }>(`/drama/projects/${id}/chat`);
        setMessages(data.messages || []);
      } catch {
        /* ignore */
      }
      await streamSSE(`/drama/projects/${id}/script/generate`, { skill_id: skillId }, (ev) => {
        if (ev.type === "status") setStatus(String(ev.detail || ""));
        if (ev.type === "chat_user") {
          const content = String(ev.content || "");
          setMessages((xs) => [...xs, { role: "user", content }, { role: "assistant", content: "" }]);
        }
        if (ev.type === "delta") {
          acc += String(ev.content || "");
          const shown = reasoning.trim()
            ? `（思考）\n${reasoning}\n\n${acc}`
            : acc;
          setMessages((xs) => {
            if (!xs.length || xs[xs.length - 1]?.role !== "assistant") {
              return [...xs, { role: "assistant", content: shown }];
            }
            const copy = [...xs];
            copy[copy.length - 1] = { role: "assistant", content: shown };
            return copy;
          });
        }
        if (ev.type === "reasoning") {
          reasoning += String(ev.content || "");
          const shown = acc.trim()
            ? `（思考）\n${reasoning}\n\n${acc}`
            : `（思考）\n${reasoning}`;
          setMessages((xs) => {
            if (!xs.length || xs[xs.length - 1]?.role !== "assistant") {
              return [...xs, { role: "assistant", content: shown }];
            }
            const copy = [...xs];
            copy[copy.length - 1] = { role: "assistant", content: shown };
            return copy;
          });
        }
        if (ev.type === "error") throw new Error(String(ev.detail || "生成失败"));
        if (ev.type === "done" && ev.project) setProj(ev.project as DramaProject);
      });
      await load();
      setStatus("剧本分镜已生成,可检查后去出首帧");
      // 生成结束后仍可留在对话看记录;用户自己切「剧本分镜」
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成失败");
      await load();
    } finally {
      setBusy("");
    }
  }

  async function generateFrames(opts: {
    regenerateAll?: boolean;
    shotIds?: number[];
    refineNote?: string;
    replaceSelectedOnly?: boolean;
    candidates?: number;
  }) {
    setBusy("frames");
    if (opts.shotIds?.length === 1) setRegenId(opts.shotIds[0]);
    setError("");
    setStatus(opts.replaceSelectedOnly ? "按描述重绘勾选图…" : "正在生成每镜 3 张候选首帧…");
    try {
      await streamSSE(
        `/drama/projects/${id}/frames/generate`,
        {
          regenerate_all: !!opts.regenerateAll,
          shot_ids: opts.shotIds,
          candidates: opts.candidates ?? 3,
          refine_note: opts.refineNote || undefined,
          replace_selected_only: !!opts.replaceSelectedOnly,
        },
        (ev) => {
          if (ev.type === "status") setStatus(String(ev.detail || ""));
          if (ev.type === "frame_start") setStatus(`首帧 ${ev.index}/${ev.total}:${ev.caption || ""}`);
          if (ev.type === "frame_progress") {
            setStatus(`镜 ${ev.shot_id} 候选 ${ev.variant}/${ev.need}`);
          }
          if (ev.type === "error") throw new Error(String(ev.detail || "生图失败"));
        }
      );
      await load();
      setTab("frames");
      setStatus(opts.replaceSelectedOnly ? "勾选图已按描述重绘" : "候选首帧已生成,请勾选并填写改图描述");
    } catch (e) {
      setError(e instanceof Error ? e.message : "生图失败");
      await load();
    } finally {
      setBusy("");
      setRegenId(null);
    }
  }

  async function selectCandidate(shot: DramaShot, index: number) {
    setError("");
    try {
      const note = refineDrafts[shot.id] ?? shot.refine_note ?? "";
      const updated = await api.put<DramaShot>(`/drama/projects/${id}/shots/${shot.id}/select`, {
        selected_index: index,
        refine_note: note,
      });
      setProj((p) =>
        p
          ? {
              ...p,
              shots: (p.shots || []).map((s) => (s.id === updated.id ? { ...s, ...updated } : s)),
            }
          : p
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "勾选失败");
    }
  }

  function candidateUrls(s: DramaShot): string[] {
    const list = (s.candidate_urls || []).filter(Boolean);
    if (list.length) return list;
    return s.image_url ? [s.image_url] : [];
  }

  async function sendChat() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((xs) => [...xs, { role: "user", content: text }]);
    setBusy("chat");
    setError("");
    let acc = "";
    setMessages((xs) => [...xs, { role: "assistant", content: "" }]);
    try {
      await streamSSE(`/drama/projects/${id}/chat`, { message: text, skill_id: skillId }, (ev) => {
        if (ev.type === "delta") {
          acc += String(ev.content || "");
          setMessages((xs) => {
            const copy = [...xs];
            copy[copy.length - 1] = { role: "assistant", content: acc };
            return copy;
          });
        }
        if (ev.type === "error") throw new Error(String(ev.detail || "对话失败"));
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "对话失败");
    } finally {
      setBusy("");
    }
  }

  if (authLoading || loading) return <Spinner />;
  if (!user || !proj) {
    return (
      <div className="py-16 text-center text-sm text-slate-400">
        项目不存在 <Link href="/drama">返回列表</Link>
      </div>
    );
  }

  const bible = proj.bible_json || {};
  const episodes = bible.episodes || [];
  const tabs: { id: Tab; label: string }[] = [
    { id: "source", label: "来源" },
    { id: "chat", label: "对话" },
    { id: "script", label: "剧本分镜" },
    { id: "frames", label: "首帧" },
  ];

  return (
    <div className="mx-auto max-w-5xl py-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link href="/drama" className="text-xs text-slate-400 hover:text-indigo-600">
            ← 项目列表
          </Link>
          <h1 className="mt-1 text-lg font-semibold text-slate-800">{proj.title}</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={skillId ?? ""}
            onChange={(e) => setSkillId(e.target.value ? +e.target.value : null)}
            className="max-w-[14rem] text-xs"
            title="生成剧本时注入的分镜技能"
          >
            <option value="">分镜技能:无</option>
            {visualSkills.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
          <Button size="sm" onClick={generateScript} disabled={!!busy}>
            {busy === "script" ? "编剧中…" : "生成剧本"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => generateFrames({})}
            disabled={!!busy || !proj.shots?.length}
          >
            {busy === "frames" ? "出图中…" : "生成首帧"}
          </Button>
        </div>
      </div>
      <div className="mb-4 flex gap-1 border-b border-slate-200">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm ${
              tab === t.id ? "border-b-2 border-indigo-600 font-medium text-indigo-600" : "text-slate-500"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {status && <p className="mb-2 text-xs text-indigo-600">{status}</p>}
      <ErrorText>{error}</ErrorText>

      {tab === "source" && (
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
          <div>
            <Label>标题</Label>
            <Input value={proj.title} onChange={(e) => setProj({ ...proj, title: e.target.value })} />
          </div>
          <div>
            <Label>画风(全片锁定,首帧跨镜同脸同风)</Label>
            <Input
              value={proj.style_prompt || ""}
              onChange={(e) => setProj({ ...proj, style_prompt: e.target.value })}
              placeholder="例如:中国二维赛璐璐动画,禁止Q版/3D/写实…"
            />
            <p className="mt-1 text-[11px] text-slate-400">
              建议写清画风并固定;关联小说时人物卡「定妆描述/参考图」会用于锁脸。改完请保存后再生成首帧。
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <Label>集数</Label>
              <Select
                value={proj.episode_count}
                onChange={(e) => setProj({ ...proj, episode_count: +e.target.value })}
              >
                {[3, 4, 5, 6, 8].map((n) => (
                  <option key={n} value={n}>
                    {n} 集
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label>从小说导入</Label>
              <Select
                className="mt-1 min-w-[12rem]"
                value={proj.novel_id ?? ""}
                onChange={(e) => {
                  const v = e.target.value ? +e.target.value : 0;
                  if (v) importNovel(v);
                }}
              >
                <option value="">选择书架上的书</option>
                {novels.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.title}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <input
                ref={fileRef}
                type="file"
                accept=".txt,.md,.markdown,.text"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) uploadFile(f);
                  e.target.value = "";
                }}
              />
              <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={!!busy}>
                {busy === "upload" ? "上传中…" : "上传剧本 txt/md"}
              </Button>
            </div>
          </div>
          <div>
            <Label>故事 / 剧本正文(可编辑)</Label>
            <Textarea
              rows={12}
              value={proj.source_text || ""}
              onChange={(e) => setProj({ ...proj, source_text: e.target.value })}
            />
          </div>
          <Button onClick={() => saveSource({})} disabled={!!busy}>
            {busy === "save" ? "保存中…" : "保存来源"}
          </Button>
        </div>
      )}

      {tab === "chat" && (
        <div className="flex h-[32rem] flex-col rounded-xl border border-slate-200 bg-white">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
            <Label>技能</Label>
            <Select
              value={skillId ?? ""}
              onChange={(e) => setSkillId(e.target.value ? +e.target.value : null)}
            >
              <option value="">不使用技能</option>
              {visualSkills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
            <div className="ml-auto flex gap-2">
              <Link href="/drama/chat" className="text-xs text-indigo-600 hover:underline">
                打开聊天台
              </Link>
              <Button
                size="sm"
                variant="outline"
                disabled={!!busy || !messages.some((m) => m.role === "assistant" && m.content.trim())}
                onClick={async () => {
                  setBusy("apply");
                  setError("");
                  try {
                    const p = await api.post<DramaProject>(`/drama/projects/${id}/chat/apply-script`, {});
                    setProj(p);
                    setTab("script");
                    setStatus("已从对话应用分镜剧本");
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "应用失败");
                  } finally {
                    setBusy("");
                  }
                }}
              >
                {busy === "apply" ? "应用中…" : "应用分镜剧本"}
              </Button>
            </div>
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
            {messages.length === 0 && (
              <p className="py-10 text-center text-xs text-slate-400">
                聊拆集、改对白,或让助手输出完整视频分镜 JSON,再点「应用分镜剧本」。也可去「聊天台」粘贴剧本。
              </p>
            )}
            {messages.map((m, i) => (
              <div
                key={i}
                className={`max-w-[90%] rounded-lg px-3 py-2 text-sm leading-6 ${
                  m.role === "user" ? "ml-auto bg-indigo-50 text-slate-800" : "bg-slate-50 text-slate-700"
                }`}
              >
                <pre className="whitespace-pre-wrap break-words font-sans">
                  {m.content || (busy === "script" && i === messages.length - 1 && m.role === "assistant" ? "…" : "")}
                </pre>
              </div>
            ))}
            {busy === "script" && (
              <p className="text-center text-[11px] text-indigo-500">编剧输出中,可滚动查看实时内容…</p>
            )}
            <div ref={chatEnd} />
          </div>
          <form
            className="flex gap-2 border-t border-slate-100 p-3"
            onSubmit={(e) => {
              e.preventDefault();
              sendChat();
            }}
          >
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="例如:第三集结尾再加一个反转"
              disabled={!!busy}
            />
            <Button type="submit" disabled={!!busy || !input.trim()}>
              {busy === "chat" ? "…" : "发送"}
            </Button>
          </form>
        </div>
      )}

      {tab === "script" && (
        <div className="space-y-4">
          {!episodes.length ? (
            <p className="py-12 text-center text-sm text-slate-400">还没有圣经,先在「来源」备好故事再点「生成剧本」</p>
          ) : (
            <>
              {(bible.assets || []).length > 0 && (
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <h3 className="mb-2 text-sm font-semibold">锁定资产</h3>
                  <ul className="space-y-1 text-xs text-slate-600">
                    {(bible.assets || []).map((a, i) => (
                      <li key={i}>
                        <span className="font-medium">{a.name}</span>
                        {a.kind ? ` · ${a.kind}` : ""} {a.prompt ? ` — ${a.prompt}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {episodes.map((ep) => (
                <div key={ep.index} className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <Badge color="indigo">第{ep.index}集</Badge>
                    <span className="font-medium text-slate-800">{ep.title}</span>
                    {typeof ep.durationSec === "number" && (
                      <span className="text-[11px] text-slate-400">本集约 {ep.durationSec} 秒</span>
                    )}
                  </div>
                  {ep.synopsis && <p className="mb-3 text-xs text-slate-500">{ep.synopsis}</p>}
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[640px] border-collapse text-left text-xs">
                      <thead>
                        <tr className="border-b border-slate-200 text-[11px] uppercase tracking-wide text-slate-400">
                          <th className="py-2 pr-3 font-medium">镜号</th>
                          <th className="py-2 pr-3 font-medium">时间</th>
                          <th className="py-2 pr-3 font-medium">镜头</th>
                          <th className="py-2 pr-3 font-medium">画面</th>
                          <th className="py-2 font-medium">对白</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(ep.shots || []).map((sh, j) => (
                          <tr key={j} className="border-b border-slate-100 align-top last:border-0">
                            <td className="py-2.5 pr-3 font-medium text-slate-700">镜{j + 1}</td>
                            <td className="py-2.5 pr-3 whitespace-nowrap font-mono text-indigo-600">
                              {timeRange(sh.startSec, sh.endSec)}
                            </td>
                            <td className="py-2.5 pr-3 whitespace-nowrap text-slate-600">
                              {lensLabel(sh.shotSize, sh.cameraMove, sh.angle)}
                            </td>
                            <td className="py-2.5 pr-3 leading-5 text-slate-700">{sh.visual || "—"}</td>
                            <td className="py-2.5 leading-5 text-slate-600">
                              {sh.line ? (
                                <>
                                  <span className="font-medium text-slate-800">{sh.speaker || "旁白"}：</span>
                                  {sh.line}
                                </>
                              ) : (
                                <span className="text-slate-400">（无对白）</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {tab === "frames" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-slate-400">
              每镜约 3 张候选(同画风同定妆,只改构图) · 勾选一张 · 可填描述重绘。人物飘了请在「来源」写死画风并重生成首帧。
            </p>
            <Button
              size="sm"
              variant="outline"
              disabled={!!busy || !proj.shots?.length}
              onClick={() => generateFrames({ regenerateAll: true, candidates: 3 })}
            >
              全部换三张
            </Button>
          </div>
          {!proj.shots?.length ? (
            <p className="py-12 text-center text-sm text-slate-400">请先生成剧本分镜</p>
          ) : (
            proj.shots.map((s: DramaShot) => {
              const urls = candidateUrls(s);
              const selected = Math.min(s.selected_index || 0, Math.max(urls.length - 1, 0));
              const draft = refineDrafts[s.id] ?? s.refine_note ?? "";
              return (
                <div key={s.id} className="rounded-xl border border-slate-100 bg-slate-50 p-3">
                  <div className="mb-1 flex items-center justify-between gap-2 text-xs text-slate-500">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono font-semibold text-indigo-600">
                        {timeRange(s.start_sec, s.end_sec)}
                      </span>
                      <span className="text-slate-700">
                        E{s.episode_index} 镜{s.shot_index} · {lensLabel(s.shot_size, s.camera_move, s.angle)}
                      </span>
                    </div>
                    <span>{s.status === "ready" ? "已出图" : s.status === "failed" ? "失败" : "待生成"}</span>
                  </div>
                  <p className="mb-1 text-xs leading-5 text-slate-600">{s.visual}</p>
                  {s.line ? (
                    <p className="mb-2 text-xs text-slate-500">
                      <span className="font-medium text-slate-700">{s.speaker || "旁白"}：</span>
                      {s.line}
                    </p>
                  ) : (
                    <p className="mb-2 text-xs text-slate-400">（无对白）</p>
                  )}
                  {s.error && <p className="mb-2 text-xs text-red-500">{s.error}</p>}

                  {urls.length ? (
                    <div className="mb-3 grid gap-2 sm:grid-cols-3">
                      {urls.map((url, idx) => (
                        <button
                          key={`${s.id}-${idx}-${url}`}
                          type="button"
                          onClick={() => selectCandidate(s, idx)}
                          className={`relative overflow-hidden rounded-lg border-2 bg-white text-left transition ${
                            selected === idx
                              ? "border-indigo-500 ring-2 ring-indigo-200"
                              : "border-slate-200 hover:border-indigo-300"
                          }`}
                        >
                          <div className="absolute left-2 top-2 z-10 rounded bg-white/90 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                            {selected === idx ? "✓ 已勾选" : `候选 ${idx + 1}`}
                          </div>
                          <ChatMediaContent content={`![候选${idx + 1}](${url})`} />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="mb-3 flex h-28 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-white text-xs text-slate-400">
                      尚未生成
                    </div>
                  )}

                  <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
                    <Label>对勾选图的改图描述</Label>
                    <Textarea
                      rows={2}
                      value={draft}
                      placeholder="例如:人物再少一点,卫星更清晰,星空更深,不要站在地上的人群…"
                      onChange={(e) => setRefineDrafts((d) => ({ ...d, [s.id]: e.target.value }))}
                      onBlur={() => {
                        if (!urls.length) return;
                        const note = refineDrafts[s.id] ?? s.refine_note ?? "";
                        if ((s.refine_note || "") === note) return;
                        api
                          .put(`/drama/projects/${id}/shots/${s.id}/select`, {
                            selected_index: selected,
                            refine_note: note,
                          })
                          .catch(() => {});
                      }}
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        disabled={!!busy || !urls.length || !draft.trim()}
                        onClick={() =>
                          generateFrames({
                            shotIds: [s.id],
                            refineNote: draft.trim(),
                            replaceSelectedOnly: true,
                            candidates: 1,
                          })
                        }
                      >
                        {busy === "frames" && regenId === s.id ? "重绘中…" : "按描述重绘勾选图"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!!busy}
                        onClick={() =>
                          generateFrames({
                            shotIds: [s.id],
                            refineNote: draft.trim() || undefined,
                            candidates: 3,
                          })
                        }
                      >
                        {busy === "frames" && regenId === s.id ? "生成中…" : "换三张候选"}
                      </Button>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
