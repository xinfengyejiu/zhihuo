"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { api, streamSSE } from "@/lib/api";
import { useTasks } from "@/lib/tasks";
import { Button, Spinner, Textarea } from "@/components/ui";

interface NovelSession {
  id: number;
  title: string;
  novel_id: number | null;
  compressed: boolean;
  created?: boolean;
}

interface ChatMsg {
  id?: number;
  role: "user" | "assistant" | "system";
  content: string;
  chapter_id?: number | null;
}

export default function NovelChatPage() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const chapterId = search.get("chapter_id");
  const { getTask, startTask } = useTasks();
  const [session, setSession] = useState<NovelSession | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const taskKey = session ? `novel-chat-${session.id}` : "";
  const chatTask = taskKey ? getTask(taskKey) : undefined;
  const busy = chatTask?.status === "running";

  const load = useCallback(async () => {
    setError("");
    try {
      const s = await api.get<NovelSession>(`/chat/novels/${id}/session`);
      setSession(s);
      const msgs = await api.get<ChatMsg[]>(`/chat/sessions/${s.id}/messages`);
      setMessages(msgs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载对话失败");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, chatTask?.output]);

  // 任务完成后刷新消息(含压缩提示)
  const prevStatus = useRef<string | undefined>();
  useEffect(() => {
    if (prevStatus.current === "running" && chatTask?.status === "done") {
      load();
      if ((chatTask.result as { compressed?: boolean } | undefined)?.compressed) {
        setNotice("上下文已自动压缩,早期讨论收进记忆摘要");
        setTimeout(() => setNotice(""), 4000);
      }
    }
    if (chatTask?.status === "error" && chatTask.error) {
      setError(chatTask.error);
    }
    prevStatus.current = chatTask?.status;
  }, [chatTask?.status, chatTask?.error, chatTask?.result, load]);

  function send() {
    if (!session || busy || !input.trim()) return;
    const content = input.trim();
    setInput("");
    setError("");
    setMessages((prev) => [...prev, { role: "user", content }]);
    startTask({
      key: `novel-chat-${session.id}`,
      label: `书内对话:#${session.id}`,
      href: `/novels/${id}/chat`,
      run: async (r) => {
        let doneMeta: { compressed?: boolean } = {};
        await streamSSE(
          `/chat/sessions/${session.id}/chat`,
          {
            message: content,
            chapter_id: chapterId ? Number(chapterId) : null,
          },
          (ev) => {
            if (ev.type === "delta") {
              r.pushOutput(String(ev.content ?? ""));
            } else if (ev.type === "reasoning") {
              r.pushReasoning(String(ev.content ?? ""));
            } else if (ev.type === "error") {
              throw new Error(typeof ev.detail === "string" ? ev.detail : "对话失败");
            } else if (ev.type === "done") {
              doneMeta = { compressed: Boolean(ev.compressed) };
              r.setResult(doneMeta);
            }
          }
        );
        return doneMeta;
      },
    });
  }

  async function compress() {
    if (!session || busy) return;
    setError("");
    try {
      const res = await api.post<{ compressed: boolean; message_count: number }>(
        `/chat/sessions/${session.id}/compress`
      );
      setNotice(res.compressed ? "已压缩早期上下文" : "当前长度无需压缩");
      setTimeout(() => setNotice(""), 3000);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "压缩失败");
    }
  }

  if (loading) return <Spinner />;

  const liveAssistant =
    busy && chatTask?.output
      ? { role: "assistant" as const, content: chatTask.output }
      : null;

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col rounded-xl border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">书内对话</h2>
          <p className="mt-0.5 text-xs text-slate-400">
            对话 ID:{session?.id ?? "-"} · 全书章节共用 · 超长自动压缩
            {session?.compressed ? " · 已有压缩记忆" : ""}
            {chapterId ? ` · 聚焦章节 #${chapterId}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={compress} disabled={busy || !session}>
            压缩上下文
          </Button>
          <Button size="sm" variant="ghost" onClick={load} disabled={busy}>
            刷新
          </Button>
        </div>
      </div>

      {(error || notice) && (
        <div className="border-b border-slate-100 px-4 py-2 text-xs">
          {error && <p className="text-red-600">{error}</p>}
          {notice && <p className="text-emerald-600">{notice}</p>}
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
        {messages.length === 0 && !liveAssistant && (
          <p className="pt-10 text-center text-xs text-slate-400">
            这部书的创作讨论都落在同一个对话里。
            <br />
            换章节写作时不必新开会话;上下文过长会自动压缩成记忆摘要。
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={m.id ?? `m-${i}`}
            className={`rounded-lg px-3 py-2 text-sm leading-6 ${
              m.role === "user"
                ? "ml-8 bg-indigo-50 text-slate-800"
                : m.role === "system"
                  ? "border border-amber-100 bg-amber-50 text-xs text-amber-800"
                  : "mr-8 bg-slate-50 text-slate-700"
            }`}
          >
            <pre className="whitespace-pre-wrap font-sans">{m.content}</pre>
          </div>
        ))}
        {liveAssistant && (
          <div className="mr-8 rounded-lg bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700">
            <pre className="whitespace-pre-wrap font-sans">{liveAssistant.content}</pre>
            {busy && <span className="mt-1 inline-block text-xs text-slate-400">生成中…</span>}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-slate-100 p-3">
        <Textarea
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="讨论本章剧情、人设、伏笔、文风… Enter 发送"
          disabled={busy}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="mt-2 flex justify-end">
          <Button onClick={send} disabled={busy || !input.trim()}>
            {busy ? "回复中…" : "发送"}
          </Button>
        </div>
      </div>
    </div>
  );
}
