"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, streamSSE } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { filterSkillsForChat, useProduct } from "@/lib/product";
import type { DramaProject, SkillInfo } from "@/lib/types";
import { Button, ErrorText, Label, Select, Spinner, Textarea } from "@/components/ui";

interface ChatSessionItem {
  id: number;
  title: string;
  updated_at: string;
  skill_id?: number | null;
  project_id?: number | null;
  workspace_id?: number | null;
}

interface ChatMsg {
  id?: number;
  role: "user" | "assistant";
  content: string;
}

const SUGGESTIONS = [
  "把下面这段故事改成 5 集视频分镜剧本(每镜写清秒数、景别、运镜、对白),直接输出 JSON",
  "按短视频节奏拆 3 集,每集 3 镜,时间轴连续",
  "加强女主冲突,重写第 2 集分镜对白",
  "我粘贴了完整剧本,请整理成可拍摄的分镜表 JSON",
];

export default function DramaChatPage() {
  const { user, loading: authLoading } = useAuth();
  const { setCurrent } = useProduct();
  const router = useRouter();

  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [projects, setProjects] = useState<DramaProject[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [ready, setReady] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [saving, setSaving] = useState("");
  const [projectId, setProjectId] = useState<number | null>(null);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillId, setSkillId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const chatSkills = filterSkillsForChat(skills, "drama");

  const loadSessions = useCallback(async () => {
    setSessions(await api.get<ChatSessionItem[]>("/drama/sessions"));
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (!user) return;
    setCurrent("drama");
    Promise.all([loadSessions(), api.get<DramaProject[]>("/drama/projects"), api.get<SkillInfo[]>("/skills")])
      .then(([_, projs, sk]) => {
        setProjects(projs);
        setSkills(sk);
        const dramaSk = filterSkillsForChat(sk, "drama");
        const preferred =
          dramaSk.find((s) => s.name.includes("Seedance") || s.name.includes("导演级分镜")) ||
          dramaSk.find((s) => s.stage === "script") ||
          dramaSk[0];
        if (preferred) setSkillId(preferred.id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setReady(true));
  }, [user, authLoading, router, loadSessions, setCurrent]);

  useEffect(() => {
    if (skillId && !chatSkills.some((s) => s.id === skillId)) setSkillId(null);
  }, [chatSkills, skillId]);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages, streaming]);

  async function openSession(id: number) {
    setError("");
    setMsg("");
    setActiveId(id);
    const list = await api.get<ChatMsg[]>(`/drama/sessions/${id}/messages`);
    setMessages(list);
    const sess = sessions.find((s) => s.id === id);
    if (sess?.project_id) setProjectId(sess.project_id);
  }

  async function newSession() {
    setError("");
    const s = await api.post<ChatSessionItem>("/drama/sessions", {
      project_id: projectId,
      title: "新对话",
    });
    await loadSessions();
    setActiveId(s.id);
    setMessages([]);
    setMsg("");
  }

  async function deleteSession(id: number) {
    await api.delete(`/drama/sessions/${id}`);
    if (activeId === id) {
      setActiveId(null);
      setMessages([]);
    }
    await loadSessions();
  }

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || streaming) return;
    let sid = activeId;
    if (sid == null) {
      const s = await api.post<ChatSessionItem>("/drama/sessions", {
        project_id: projectId,
        title: content.slice(0, 20),
      });
      sid = s.id;
      setActiveId(sid);
      await loadSessions();
    }
    setInput("");
    setMessages((xs) => [...xs, { role: "user", content }, { role: "assistant", content: "" }]);
    setStreaming(true);
    setError("");
    let acc = "";
    try {
      await streamSSE(`/drama/sessions/${sid}/chat`, { message: content, skill_id: skillId }, (ev) => {
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
      await loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "对话失败");
    } finally {
      setStreaming(false);
    }
  }

  async function applySource() {
    if (activeId == null) return;
    setSaving("source");
    setError("");
    setMsg("");
    try {
      const proj = await api.post<DramaProject>("/drama/chat/apply-source", {
        session_id: activeId,
        project_id: projectId,
        content: input.trim() || undefined,
      });
      setProjectId(proj.id);
      setMsg(`已存入项目《${proj.title}》来源,可继续生成分镜或聊天改剧`);
      await loadSessions();
      setProjects(await api.get<DramaProject[]>("/drama/projects"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存来源失败");
    } finally {
      setSaving("");
    }
  }

  async function applyScript() {
    if (activeId == null) return;
    setSaving("script");
    setError("");
    setMsg("");
    try {
      const proj = await api.post<DramaProject>("/drama/chat/apply-script", {
        session_id: activeId,
        project_id: projectId,
      });
      setProjectId(proj.id);
      setMsg(`已应用分镜到《${proj.title}》(${proj.shot_count || 0} 镜)`);
      await loadSessions();
      setProjects(await api.get<DramaProject[]>("/drama/projects"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "应用剧本失败");
    } finally {
      setSaving("");
    }
  }

  if (authLoading || !ready) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (!user) return null;

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white sm:w-60">
        <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-3 py-2">
          <span className="text-xs font-medium text-slate-500">会话</span>
          <Button size="sm" variant="outline" onClick={newSession}>
            新建
          </Button>
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
          {sessions.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-slate-400">还没有对话</p>
          ) : (
            sessions.map((s) => (
              <div
                key={s.id}
                className={`group flex items-center gap-1 rounded-lg px-2 py-2 text-left text-sm ${
                  activeId === s.id ? "bg-indigo-50 text-indigo-700" : "hover:bg-slate-50"
                }`}
              >
                <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => openSession(s.id)}>
                  {s.title || "新对话"}
                </button>
                <button
                  type="button"
                  className="hidden text-[10px] text-red-400 group-hover:inline"
                  onClick={() => deleteSession(s.id)}
                >
                  删
                </button>
              </div>
            ))
          )}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="shrink-0 border-b border-slate-200 bg-white px-3 py-2 sm:px-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="text-sm font-semibold text-slate-800">漫剧聊天</h1>
            <p className="text-[11px] text-slate-400">
              粘贴剧本或聊分镜 · 谈完可存来源 / 应用剧本
              <Link href="/drama" className="ml-1 text-indigo-600 hover:underline">
                项目
              </Link>
            </p>
          </div>
          {(error || msg) && (
            <div className="mt-1">
              <ErrorText>{error}</ErrorText>
              {msg && <p className="text-xs text-emerald-600">{msg}</p>}
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Label>应用到项目</Label>
            <Select
              className="min-w-[9rem]"
              value={projectId ?? ""}
              onChange={(e) => setProjectId(e.target.value ? +e.target.value : null)}
            >
              <option value="">聊天后新建项目</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </Select>
            <Label>技能</Label>
            <Select value={skillId ?? ""} onChange={(e) => setSkillId(e.target.value ? +e.target.value : null)}>
              <option value="">不使用技能</option>
              {chatSkills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
            <div className="ml-auto flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={!activeId || !!saving || streaming} onClick={applySource}>
                {saving === "source" ? "保存中…" : "存为来源"}
              </Button>
              <Button size="sm" disabled={!activeId || !!saving || streaming} onClick={applyScript}>
                {saving === "script" ? "应用中…" : "应用分镜剧本"}
              </Button>
              {projectId ? (
                <Button size="sm" variant="outline" onClick={() => router.push(`/drama/${projectId}`)}>
                  打开项目
                </Button>
              ) : null}
            </div>
          </div>
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3 sm:px-4">
          {messages.length === 0 ? (
            <div className="mx-auto max-w-xl space-y-3 py-8 text-center">
              <p className="text-sm text-slate-500">在下方粘贴剧本,或点建议开始拆分镜</p>
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
                    onClick={() => send(s)}
                  >
                    {s.slice(0, 28)}…
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => (
              <div key={m.id ?? i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-h-[min(52vh,28rem)] max-w-[min(90%,48rem)] overflow-y-auto whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-6 ${
                    m.role === "user" ? "bg-indigo-600 text-white" : "bg-white text-slate-800 shadow-sm ring-1 ring-slate-100"
                  }`}
                >
                  {m.content || (streaming && i === messages.length - 1 ? "…" : "")}
                </div>
              </div>
            ))
          )}
        </div>

        <form
          className="shrink-0 border-t border-slate-200 bg-white p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <div className="mx-auto flex max-w-3xl gap-2">
            <Textarea
              rows={2}
              className="min-h-[2.75rem] max-h-28 resize-none"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="粘贴剧本正文,或描述想要的分镜…(Ctrl+Enter 发送)"
              disabled={streaming}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <Button type="submit" className="shrink-0 self-end" disabled={streaming || !input.trim()}>
              {streaming ? "…" : "发送"}
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}
