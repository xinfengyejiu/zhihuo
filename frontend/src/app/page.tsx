"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, streamSSE } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { filterSkillsForChat } from "@/lib/product";
import { useTasks } from "@/lib/tasks";
import type { SkillInfo } from "@/lib/types";
import { Spinner } from "@/components/ui";

interface ChatSessionItem {
  id: number;
  title: string;
  updated_at: string;
  skill_id?: number | null;
}

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  model_name?: string | null;
}

const SUGGESTIONS = [
  "帮我构思三个修仙文的开局选题",
  "分析番茄免费平台的热门题材",
  "给女主起一组有记忆点的名字",
  "爽文的黄金三章怎么安排?",
];

export default function ChatHomePage() {
  const { user, loading: authLoading } = useAuth();
  const { tasks, getTask, startTask, clearTask } = useTasks();
  const router = useRouter();
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameText, setRenameText] = useState("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillId, setSkillId] = useState<number | null>(null);
  const chatSkills = filterSkillsForChat(skills, "novel");
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<{
    novel_id: number;
    title: string;
    characters_created: number;
    outline_created: boolean;
    volumes_created?: number;
    chapters_created?: number;
    catalog_message?: string | null;
    world_created: number;
    rules_created: number;
    relationships_created: number;
    foreshadows_created: number;
  } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadSessions = useCallback(async () => {
    setSessions(await api.get<ChatSessionItem[]>("/chat/sessions"));
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (user) {
      loadSessions().finally(() => setReady(true));
      api
        .get<SkillInfo[]>("/skills")
        .then(setSkills)
        .catch(() => setSkills([]));
    }
  }, [user, authLoading, router, loadSessions]);

  useEffect(() => {
    if (skillId && !chatSkills.some((s) => s.id === skillId)) setSkillId(null);
  }, [chatSkills, skillId]);

  const chatTask = activeId !== null ? getTask(`chat-${activeId}`) : undefined;
  const streaming = chatTask?.status === "running";

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages, chatTask?.output, chatTask?.reasoning]);

  useEffect(() => {
    if (activeId !== null) return;
    const entry = Object.entries(tasks).find(([k]) => k.startsWith("chat-"));
    if (!entry) return;
    const [, t] = entry;
    if (t.status === "running" || t.status === "done") {
      const sid = Number(entry[0].replace("chat-", ""));
      if (!Number.isNaN(sid)) openSession(sid);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, tasks]);

  useEffect(() => {
    if (!chatTask || chatTask.status !== "done" || activeId === null) return;
    if (chatTask.key !== `chat-${activeId}`) return;
    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: chatTask.output, reasoning: chatTask.reasoning || undefined },
    ]);
    clearTask(chatTask.key);
  }, [chatTask, activeId, clearTask]);

  async function openSession(id: number) {
    if (getTask(`chat-${id}`)?.status === "running") {
      setActiveId(id);
      setApplyResult(null);
      const sess = await api.get<ChatSessionItem[]>("/chat/sessions");
      setSessions(sess);
      setSkillId(sess.find((s) => s.id === id)?.skill_id ?? null);
      setMessages(await api.get<ChatMsg[]>(`/chat/sessions/${id}/messages`));
      return;
    }
    setError("");
    setActiveId(id);
    setApplyResult(null);
    const sess = await api.get<ChatSessionItem[]>("/chat/sessions");
    setSessions(sess);
    setSkillId(sess.find((s) => s.id === id)?.skill_id ?? null);
    setMessages(await api.get<ChatMsg[]>(`/chat/sessions/${id}/messages`));
  }

  function backToLanding() {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    setError("");
    setApplyResult(null);
    setSkillId(null);
  }

  async function applyToShelf() {
    if (!activeId || applying) return;
    setApplying(true);
    setError("");
    try {
      setApplyResult(
        await api.post<{
          novel_id: number;
          title: string;
          characters_created: number;
          outline_created: boolean;
          volumes_created?: number;
          chapters_created?: number;
          catalog_message?: string | null;
          world_created: number;
          rules_created: number;
          relationships_created: number;
          foreshadows_created: number;
        }>(`/chat/sessions/${activeId}/apply-to-novel`)
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "应用失败");
    } finally {
      setApplying(false);
    }
  }

  function send(textOverride?: string) {
    const content = (textOverride ?? input).trim();
    if (!content) return;
    if (activeId !== null && getTask(`chat-${activeId}`)?.status === "running") return;
    setError("");
    const doSend = async () => {
      let sessionId = activeId;
      if (!sessionId) {
        const s = await api.post<ChatSessionItem>("/chat/sessions", {});
        sessionId = s.id;
        setActiveId(sessionId);
      }
      const sid = sessionId;
      setMessages((prev) => [...prev, { role: "user", content }]);
      setInput("");
      startTask({
        key: `chat-${sid}`,
        label: `AI 回复:${content.slice(0, 16)}`,
        href: "/",
        run: async (r) => {
          await streamSSE(`/chat/sessions/${sid}/chat`, { message: content, skill_id: skillId || null }, (ev) => {
            if (ev.type === "delta") r.pushOutput(ev.content as string);
            else if (ev.type === "reasoning") r.pushReasoning(ev.content as string);
            else if (ev.type === "error") throw new Error((ev.detail as string) || "AI 调用失败");
          });
          return true;
        },
      });
      loadSessions();
    };
    doSend().catch((e) => setError(e instanceof Error ? e.message : "发送失败"));
  }

  async function removeSession(id: number) {
    if (!confirm("确定删除该对话?")) return;
    await api.delete(`/chat/sessions/${id}`);
    if (activeId === id) backToLanding();
    loadSessions();
  }

  async function saveRename(id: number) {
    const title = renameText.trim();
    setRenamingId(null);
    if (!title) return;
    try {
      await api.put(`/chat/sessions/${id}`, { title });
      loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "重命名失败");
    }
  }

  if (authLoading || (!user && !ready)) return <Spinner />;
  if (!user) return null;

  const inConversation = activeId !== null;
  const liveReasoning = streaming ? chatTask?.reasoning || "" : "";
  const liveOutput = streaming ? chatTask?.output || "" : "";
  const taskError = !streaming && chatTask?.status === "error" ? chatTask.error : null;

  return (
    <div className="flex h-full">
      {inConversation && (
        <aside className="w-60 shrink-0 overflow-y-auto border-r border-slate-200 bg-white">
          <div className="p-3">
            <button
              onClick={backToLanding}
              className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-700"
            >
              + 新的对话
            </button>
          </div>
          <ul className="px-2 pb-4">
            {sessions.map((s) => {
              const sTask = getTask(`chat-${s.id}`);
              return (
                <li key={s.id}>
                  {renamingId === s.id ? (
                    <input
                      autoFocus
                      value={renameText}
                      onChange={(e) => setRenameText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveRename(s.id);
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      onBlur={() => saveRename(s.id)}
                      className="w-full rounded-lg border border-indigo-300 px-3 py-2 text-sm outline-none"
                      placeholder="会话名称"
                    />
                  ) : (
                    <div
                      className={`group flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm ${
                        activeId === s.id ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-50"
                      }`}
                      onClick={() => openSession(s.id)}
                    >
                      <span className="truncate">
                        {s.title}
                        {sTask?.status === "running" && (
                          <span className="ml-1 inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-indigo-400 align-middle" />
                        )}
                      </span>
                      <div className="ml-1 hidden shrink-0 gap-1 text-xs text-slate-300 group-hover:flex">
                        <button
                          type="button"
                          title="重命名"
                          onClick={(e) => {
                            e.stopPropagation();
                            setRenamingId(s.id);
                            setRenameText(s.title);
                          }}
                        >
                          ✎
                        </button>
                        <button
                          type="button"
                          title="删除"
                          className="hover:text-red-500"
                          onClick={(e) => {
                            e.stopPropagation();
                            removeSession(s.id);
                          }}
                        >
                          ×
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </aside>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {inConversation ? (
          <>
            <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
              <span className="truncate text-sm font-medium text-slate-600">
                {sessions.find((s) => s.id === activeId)?.title || "对话"}
              </span>
              <button
                type="button"
                onClick={applyToShelf}
                disabled={applying || streaming}
                title="AI 从对话中提取选题、大纲、卷章目录、故事线、人物,自动创建到书架"
                className="shrink-0 rounded-full border border-indigo-200 px-3 py-1 text-xs font-medium text-indigo-600 transition hover:bg-indigo-50 disabled:opacity-40"
              >
                {applying ? "AI 整理中,约 10-30 秒…" : "📚 应用到书架"}
              </button>
            </div>
            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
              <div className="mx-auto max-w-3xl space-y-4">
                {messages.map((m, i) => (
                  <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div
                      className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-7 ${
                        m.role === "user" ? "bg-indigo-600 text-white" : "bg-white text-slate-700 shadow-sm"
                      }`}
                    >
                      {m.role === "assistant" && m.reasoning && (
                        <details open={false} className="mb-1.5">
                          <summary className="cursor-pointer select-none text-xs text-slate-400">思考过程</summary>
                          <div className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-slate-200 pl-2 text-xs leading-5 text-slate-400">
                            {m.reasoning}
                          </div>
                        </details>
                      )}
                      {m.content}
                    </div>
                  </div>
                ))}
                {chatTask && (
                  <div className="flex justify-start">
                    <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-white px-4 py-2.5 text-sm leading-7 text-slate-700 shadow-sm">
                      {chatTask.reasoning && (
                        <details open={!chatTask.output} className="mb-1.5">
                          <summary className="cursor-pointer select-none text-xs text-slate-400">
                            {chatTask.output ? "思考过程" : "思考中…"}
                          </summary>
                          <div className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-slate-200 pl-2 text-xs leading-5 text-slate-400">
                            {chatTask.reasoning}
                          </div>
                        </details>
                      )}
                      {liveOutput}
                      {streaming && !liveOutput && !liveReasoning && (
                        <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-indigo-400 align-middle" />
                      )}
                      {streaming && liveOutput && (
                        <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-indigo-400 align-middle" />
                      )}
                    </div>
                  </div>
                )}
                {taskError && (
                  <div className="rounded-lg border border-red-100 bg-red-50 p-2.5 text-sm text-red-600">
                    <span className="font-medium">出错了:</span>
                    {taskError}
                  </div>
                )}
                {!chatTask && error && (
                  <div className="rounded-lg border border-red-100 bg-red-50 p-2.5 text-sm text-red-600">
                    <span className="font-medium">出错了:</span>
                    {error}
                  </div>
                )}
              </div>
            </div>
            <div className="border-t border-slate-200 bg-white px-4 py-3">
              <div className="mx-auto max-w-3xl">
                {applyResult && (
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-700">
                    <span>
                      ✓ 已创建《{applyResult.title}》到书架:卷 {applyResult.volumes_created ?? 0} · 章{" "}
                      {applyResult.chapters_created ?? 0} · 大纲文档 {applyResult.outline_created ? "1 份" : "0 份"} ·
                      人物 {applyResult.characters_created} · 世界观 {applyResult.world_created} · 规则{" "}
                      {applyResult.rules_created} · 关系 {applyResult.relationships_created} · 伏笔{" "}
                      {applyResult.foreshadows_created}
                      {applyResult.catalog_message ? `（${applyResult.catalog_message}）` : ""}
                    </span>
                    <div className="flex gap-2">
                      <Link
                        href={`/novels/${applyResult.novel_id}/outline`}
                        className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700"
                      >
                        查看卷章
                      </Link>
                      <Link
                        href={`/novels/${applyResult.novel_id}`}
                        className="rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        工作台
                      </Link>
                      <Link
                        href="/shelf"
                        className="rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        去书架
                      </Link>
                    </div>
                  </div>
                )}
                <div className="flex items-end gap-2">
                  <div className="flex flex-1 flex-col gap-1.5">
                    {chatSkills.length > 0 && (
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <span>技能</span>
                        <select
                          value={skillId ?? ""}
                          onChange={(e) => setSkillId(e.target.value ? +e.target.value : null)}
                          className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 outline-none focus:border-indigo-400"
                        >
                          <option value="">不使用技能</option>
                          {chatSkills.map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name}
                            </option>
                          ))}
                        </select>
                        {skillId && (
                          <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-500">
                            当前:{chatSkills.find((s) => s.id === skillId)?.name}
                          </span>
                        )}
                      </div>
                    )}
                    <textarea
                      rows={1}
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          send();
                        }
                      }}
                      placeholder={
                        streaming
                          ? "AI 正在回复,可先去处理其他事情…"
                          : "继续聊点什么…(Enter 发送,Shift+Enter 换行)"
                      }
                      className="max-h-40 resize-none rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:border-indigo-500"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => send()}
                    disabled={streaming || !input.trim()}
                    className="h-10 w-10 shrink-0 rounded-full bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-200"
                  >
                    {streaming ? "…" : "↑"}
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center px-4 py-8">
            <h1 className="text-4xl font-bold tracking-[0.3em] text-slate-700">织 火</h1>
            <p className="mt-3 text-sm text-slate-400">AI 长篇小说创作平台 · 有什么想法,直接开聊</p>
            <div className="mt-8 w-full max-w-2xl rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
              <textarea
                rows={3}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder="问点什么,一起构思你的下一本书…"
                className="w-full resize-none rounded-xl px-3 py-2 text-sm outline-none"
              />
              <div className="flex items-center justify-between px-1">
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.slice(0, 2).map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setInput(s)}
                      className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-500 hover:border-indigo-300 hover:text-indigo-600"
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => send()}
                  disabled={streaming || !input.trim()}
                  className="h-10 w-10 shrink-0 rounded-full bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-200"
                >
                  ↑
                </button>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.slice(2).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setInput(s)}
                  className="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-500 hover:border-indigo-300 hover:text-indigo-600"
                >
                  {s}
                </button>
              ))}
            </div>
            {error && (
              <div className="mt-4 rounded-lg border border-red-100 bg-red-50 p-2.5 text-sm text-red-600">
                <span className="font-medium">出错了:</span>
                {error}
              </div>
            )}
            {sessions.length > 0 && (
              <div className="mt-8 w-full max-w-2xl">
                <h3 className="mb-2 text-xs font-medium tracking-wide text-slate-400">最近对话</h3>
                <div className="max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-sm">
                  {sessions.slice(0, 30).map((s) => {
                    const sTask = getTask(`chat-${s.id}`);
                    return (
                      <div
                        key={s.id}
                        className="group flex cursor-pointer items-center justify-between border-b border-slate-50 px-4 py-2.5 text-sm last:border-b-0 hover:bg-slate-50"
                        onClick={() => openSession(s.id)}
                      >
                        <span className="truncate text-slate-700">
                          {s.title}
                          {sTask?.status === "running" && (
                            <span className="ml-2 inline-flex items-center gap-1 text-xs text-indigo-500">
                              <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-500" />
                              回复中
                            </span>
                          )}
                        </span>
                        <div className="flex shrink-0 items-center gap-2 text-xs text-slate-400">
                          <span>
                            {new Date(s.updated_at).toLocaleString("zh-CN", {
                              month: "numeric",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                          <button
                            type="button"
                            className="hidden text-red-300 hover:text-red-500 group-hover:block"
                            onClick={(e) => {
                              e.stopPropagation();
                              removeSession(s.id);
                            }}
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}



