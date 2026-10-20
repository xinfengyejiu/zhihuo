"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChatMediaContent, listImagePlaceholders, modalityLabel } from "@/components/ChatMediaContent";
import { ChatSessionDrawer } from "@/components/ChatSessionDrawer";
import { api, streamSSE } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { loadMediaChatPrefs, saveMediaChatPrefs } from "@/lib/chatPrefs";
import { filterSkillsForChat, useProduct } from "@/lib/product";
import { useTasks } from "@/lib/tasks";
import type { Provider, SkillInfo } from "@/lib/types";

interface ChatSessionItem {
  id: number;
  title: string;
  updated_at: string;
  skill_id?: number | null;
  account_id?: number | null;
  workspace_id?: number | null;
}

interface ChatMsg {
  id?: number;
  role: "user" | "assistant";
  content: string;
  model_name?: string | null;
}

interface Account {
  id: number;
  name: string;
  platform: string;
}

interface ProviderModel {
  id: number;
  model_id: string;
  display_name: string | null;
  enabled: boolean;
  modality?: string;
}

const SUGGESTIONS = [
  "帮我围绕职场成长想 8 个小红书选题",
  "公众号适不适合做「AI 工具测评」?给 5 个切入角度",
  "对比这几个方向哪个更适合冷启动账号",
  "面向宝妈的轻食账号,帮我想一周选题",
];

type PendingImage = { url: string; preview: string };

export default function MediaChatPage() {
  const { user, loading: authLoading } = useAuth();
  const { setCurrent } = useProduct();
  const { tasks, getTask, startTask, clearTask } = useTasks();
  const router = useRouter();

  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [ready, setReady] = useState(false);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [sessionDrawerOpen, setSessionDrawerOpen] = useState(false);
  const [renameText, setRenameText] = useState("");
  const [saving, setSaving] = useState(false);
  const [fillingMsgId, setFillingMsgId] = useState<number | "last" | null>(null);
  const autoFilledRef = useRef<Set<string>>(new Set());
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillId, setSkillId] = useState<number | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [modelId, setModelId] = useState<string | null>(null);
  const [models, setModels] = useState<ProviderModel[]>([]);
  const prefsHydrated = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const chatSkills = filterSkillsForChat(skills, "media");
  const selectedModality =
    (modelId && models.find((m) => m.model_id === modelId)?.modality) || "text";

  const loadSessions = useCallback(async () => {
    setSessions(await api.get<ChatSessionItem[]>("/media/sessions"));
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (!user) return;
    setCurrent("media");
    const prefs = loadMediaChatPrefs();
    Promise.all([
      loadSessions(),
      api.get<Account[]>("/media/accounts"),
      api.get<SkillInfo[]>("/skills"),
      api.get<Provider[]>("/providers"),
    ])
      .then(([_, accs, sk, prov]) => {
        const enabledProviders = prov.filter((p) => p.enabled);
        setAccounts(accs);
        setSkills(sk);
        setProviders(enabledProviders);
        if (prefs.accountId != null && accs.some((a) => a.id === prefs.accountId)) {
          setAccountId(prefs.accountId);
        }
        if (prefs.skillId != null && filterSkillsForChat(sk, "media").some((s) => s.id === prefs.skillId)) {
          setSkillId(prefs.skillId);
        }
        if (prefs.providerId != null && enabledProviders.some((p) => p.id === prefs.providerId)) {
          setProviderId(prefs.providerId);
          setModelId(prefs.modelId);
        }
        prefsHydrated.current = true;
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setReady(true));
  }, [user, authLoading, router, loadSessions, setCurrent]);

  useEffect(() => {
    if (!prefsHydrated.current) return;
    saveMediaChatPrefs({ accountId, skillId, providerId, modelId });
  }, [accountId, skillId, providerId, modelId]);

  useEffect(() => {
    if (!providerId) {
      setModels([]);
      return;
    }
    api
      .get<ProviderModel[]>(`/providers/${providerId}/models`)
      .then((list) => {
        setModels(list);
        setModelId((current) => {
          const prefs = loadMediaChatPrefs();
          const preferred = prefs.modelId || current;
          if (preferred && list.some((m) => m.enabled && m.model_id === preferred)) {
            return preferred;
          }
          return null;
        });
      })
      .catch(() => setModels([]));
  }, [providerId]);

  useEffect(() => {
    if (skillId && !chatSkills.some((s) => s.id === skillId)) setSkillId(null);
  }, [chatSkills, skillId]);

  const chatTask = activeId !== null ? getTask(`media-chat-${activeId}`) : undefined;
  const streaming = chatTask?.status === "running";
  const mediaStatus = String(chatTask?.meta?.mediaStatus || "");
  const mediaNotice = String(chatTask?.meta?.notice || "");
  const liveModelName = String(chatTask?.meta?.modelName || "");

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages, chatTask?.output, mediaStatus]);

  useEffect(() => {
    if (activeId !== null) return;
    const entry = Object.entries(tasks).find(([k]) => k.startsWith("media-chat-"));
    if (!entry) return;
    const [, t] = entry;
    if (t.status === "running" || t.status === "done") {
      const sid = Number(entry[0].replace("media-chat-", ""));
      if (!Number.isNaN(sid)) openSession(sid);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, tasks]);

  useEffect(() => {
    if (!chatTask || activeId === null) return;
    if (chatTask.key !== `media-chat-${activeId}`) return;
    if (chatTask.status === "error") {
      setError(chatTask.error || "生成失败");
      return;
    }
    if (chatTask.status !== "done") return;
    const modelName =
      (chatTask.result as { modelName?: string | null } | undefined)?.modelName ||
      (typeof chatTask.meta?.modelName === "string" ? chatTask.meta.modelName : null);
    const output = (chatTask.output || "").trim();
    if (!output) {
      setError("模型未返回内容,请查看用量看板的失败日志或换模型重试");
      clearTask(chatTask.key);
      return;
    }
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && last.content === output) return prev;
      return [...prev, { role: "assistant", content: output, model_name: modelName }];
    });
    clearTask(chatTask.key);
    loadSessions();
    // 若流式阶段仍残留占位,自动补生成(无需点击)
    if (listImagePlaceholders(output).length > 0) {
      const key = `sess-${activeId}-pending`;
      if (!autoFilledRef.current.has(key)) {
        autoFilledRef.current.add(key);
        setTimeout(() => fillImagesForMessage(), 80);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatTask, activeId, clearTask, loadSessions]);

  // 打开会话时:对仍含占位的助手消息自动补齐配图(每条只尝试一次)
  useEffect(() => {
    if (!activeId || streaming || fillingMsgId != null) return;
    const pending = [...messages]
      .reverse()
      .find((m) => m.role === "assistant" && listImagePlaceholders(m.content).length > 0);
    if (!pending) return;
    const key = `msg-${pending.id ?? pending.content.slice(0, 40)}`;
    if (autoFilledRef.current.has(key)) return;
    autoFilledRef.current.add(key);
    fillImagesForMessage(pending.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, messages]);

  async function openSession(id: number) {
    setError("");
    setMsg("");
    setActiveId(id);
    const sess = await api.get<ChatSessionItem[]>("/media/sessions");
    setSessions(sess);
    const cur = sess.find((s) => s.id === id);
    if (cur?.skill_id != null) setSkillId(cur.skill_id);
    if (cur?.account_id != null) setAccountId(cur.account_id);
    setMessages(await api.get<ChatMsg[]>(`/media/sessions/${id}/messages`));
  }

  function backToLanding() {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    setError("");
    setMsg("");
  }

  async function attachFiles(fileList: FileList | File[] | null) {
    if (!fileList) return;
    const files = Array.from(fileList).filter((f) => f.type.startsWith("image/")).slice(0, 8);
    if (!files.length) return;
    setUploading(true);
    setError("");
    try {
      const added: PendingImage[] = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const res = await api.upload<{ url: string }>("/media/upload-image", form);
        added.push({ url: res.url, preview: URL.createObjectURL(file) });
      }
      setPendingImages((prev) => [...prev, ...added].slice(0, 8));
    } catch (e) {
      setError(e instanceof Error ? e.message : "图片上传失败");
    } finally {
      setUploading(false);
    }
  }

  function removePending(idx: number) {
    setPendingImages((prev) => {
      const next = [...prev];
      const [gone] = next.splice(idx, 1);
      if (gone?.preview.startsWith("blob:")) URL.revokeObjectURL(gone.preview);
      return next;
    });
  }

  function onPasteImage(e: React.ClipboardEvent) {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      attachFiles(files);
    }
  }

  function send(textOverride?: string) {
    const content = (textOverride ?? input).trim();
    const images = pendingImages.map((p) => p.url);
    if (!content && !images.length) return;
    if (activeId !== null && getTask(`media-chat-${activeId}`)?.status === "running") return;
    setError("");
    setMsg("");
    const mod = selectedModality;
    const labelPrefix = images.length
      ? "识图"
      : mod === "image"
        ? "生图"
        : mod === "video"
          ? "生视频"
          : "选题";
    const display =
      content +
      (images.length
        ? "\n\n" + images.map((u, i) => `![上传图片${i + 1}](${u})`).join("\n\n")
        : "");

    const doSend = async () => {
      let sessionId = activeId;
      if (!sessionId) {
        const s = await api.post<ChatSessionItem>("/media/sessions", {
          account_id: accountId,
        });
        sessionId = s.id;
        setActiveId(sessionId);
      }
      const sid = sessionId;
      setMessages((prev) => [...prev, { role: "user", content: display }]);
      setInput("");
      setPendingImages([]);
      startTask({
        key: `media-chat-${sid}`,
        label: `自媒体${labelPrefix}:${(content || "图片").slice(0, 16)}`,
        href: "/media/chat",
        run: async (reporter) => {
          let modelName: string | null = null;
          await streamSSE(
            `/media/sessions/${sid}/chat`,
            {
              content,
              images,
              mode: accountId ? "write" : "brainstorm",
              account_id: accountId,
              skill_id: skillId,
              provider_id: providerId,
              model_id: modelId,
              auto_images: true,
            },
            (ev) => {
              if (ev.type === "delta") reporter.pushOutput(String(ev.content || ""));
              if (ev.type === "content_replace" && ev.content != null) {
                reporter.setOutput(String(ev.content));
              }
              if (ev.type === "image" && ev.url) {
                reporter.pushOutput(`\n\n![图片](${String(ev.url)})\n`);
              }
              if (ev.type === "video" && ev.url) {
                reporter.pushOutput(`\n\n🎥 [视频](${String(ev.url)})\n`);
              }
              if (ev.type === "media_status") reporter.setMeta({ mediaStatus: String(ev.detail || "") });
              if (ev.type === "notice") reporter.setMeta({ notice: String(ev.detail || "") });
              if (ev.type === "model") {
                modelName = String(ev.model_name || ev.model || "") || null;
                if (modelName) reporter.setMeta({ modelName });
              }
              if (ev.type === "error") throw new Error(String(ev.detail || "生成失败"));
            }
          );
          return { modelName };
        },
      });
      loadSessions();
    };
    doSend().catch((e) => setError(e instanceof Error ? e.message : "发送失败"));
  }

  async function removeSession(id: number) {
    if (!confirm("确定删除该对话?")) return;
    await api.delete(`/media/sessions/${id}`);
    if (activeId === id) backToLanding();
    loadSessions();
  }

  async function saveRename(id: number) {
    const title = renameText.trim();
    setRenamingId(null);
    if (!title) return;
    try {
      await api.put(`/media/sessions/${id}`, { title });
      loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "重命名失败");
    }
  }

  async function saveTopics() {
    if (!activeId || saving) return;
    setSaving(true);
    setError("");
    setMsg("");
    try {
      const res = await api.post<{ created: number; titles: string[] }>("/media/chat/save-topics", {
        account_id: accountId,
        session_id: activeId,
      });
      setMsg(`已存入选题库 ${res.created} 条`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function fillImagesForMessage(messageId?: number) {
    if (!activeId || fillingMsgId != null || streaming) return;
    setFillingMsgId(messageId ?? "last");
    setError("");
    setMsg("");
    try {
      let finalContent = "";
      let targetId = messageId;
      await streamSSE(
        `/media/sessions/${activeId}/fill-images`,
        {
          message_id: messageId ?? null,
          provider_id: providerId,
          model_id: modelId,
        },
        (ev) => {
          if (ev.type === "content_replace" && ev.content != null) {
            finalContent = String(ev.content);
            setMessages((prev) => {
              const next = [...prev];
              // 更新对应助手消息内容,立刻预览
              for (let i = next.length - 1; i >= 0; i--) {
                if (next[i].role !== "assistant") continue;
                if (messageId != null && next[i].id !== messageId) continue;
                next[i] = { ...next[i], content: finalContent };
                break;
              }
              return next;
            });
          }
          if (ev.type === "media_status") {
            setMsg(String(ev.detail || ""));
          }
          if (ev.type === "done") {
            finalContent = String(ev.content || finalContent);
            if (ev.message_id != null) targetId = Number(ev.message_id);
            setMessages((prev) => {
              const next = [...prev];
              for (let i = next.length - 1; i >= 0; i--) {
                if (next[i].role !== "assistant") continue;
                if (targetId != null && next[i].id != null && next[i].id !== targetId) continue;
                next[i] = { ...next[i], content: finalContent, id: targetId ?? next[i].id };
                break;
              }
              return next;
            });
          }
          if (ev.type === "error") throw new Error(String(ev.detail || "配图生成失败"));
        }
      );
      setMsg("配图已生成,可在对话中直接预览");
    } catch (e) {
      setError(e instanceof Error ? e.message : "配图生成失败");
    } finally {
      setFillingMsgId(null);
    }
  }

  if (authLoading || (!user && !ready)) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-400">加载中…</div>
    );
  }
  if (!user) return null;

  const inConversation = activeId !== null;
  const liveOutput = streaming ? chatTask?.output || "" : "";
  const taskError = !streaming && chatTask?.status === "error" ? chatTask.error : null;

  return (
    <div className="flex h-full">
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        multiple
        className="hidden"
        onChange={(e) => {
          attachFiles(e.target.files);
          e.target.value = "";
        }}
      />
      {inConversation && (
        <ChatSessionDrawer open={sessionDrawerOpen} onClose={() => setSessionDrawerOpen(false)} className="md:w-60">
          <div className="p-3">
            <button
              onClick={() => {
                setSessionDrawerOpen(false);
                backToLanding();
              }}
              className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-700"
            >
              + 新的对话
            </button>
          </div>
          <ul className="px-2 pb-4">
            {sessions.map((s) => {
              const sTask = getTask(`media-chat-${s.id}`);
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
                      onClick={() => {
                        setSessionDrawerOpen(false);
                        openSession(s.id);
                      }}
                    >
                      <span className="truncate">
                        {s.title}
                        {sTask?.status === "running" && (
                          <span className="ml-1 inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-indigo-400 align-middle" />
                        )}
                      </span>
                      <div className="ml-1 hidden shrink-0 gap-1 text-xs text-slate-300 group-hover:flex">
                        <button
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
                          title="删除"
                          className="hover:text-red-500"
                          onClick={(e) => {
                            e.stopPropagation();
                            removeSession(s.id);
                          }}
                        >
                          ✕
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </ChatSessionDrawer>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {inConversation ? (
          <>
            <div className="flex items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-2 sm:px-4">
              <div className="flex min-w-0 items-center gap-2">
                <button
                  type="button"
                  className="inline-flex h-8 shrink-0 items-center rounded-lg border border-slate-200 px-2 text-xs text-slate-600 md:hidden"
                  onClick={() => setSessionDrawerOpen(true)}
                >
                  会话
                </button>
                <span className="truncate text-sm font-medium text-slate-600">
                  {sessions.find((s) => s.id === activeId)?.title || "对话"}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Link href="/media" className="hidden text-xs text-slate-400 hover:text-indigo-600 sm:inline">
                  管理账号
                </Link>
                <Link href="/media/topics" className="hidden text-xs text-slate-400 hover:text-indigo-600 sm:inline">
                  选题库
                </Link>
                <button
                  onClick={saveTopics}
                  disabled={saving || streaming || selectedModality !== "text"}
                  className="rounded-full border border-indigo-200 px-3 py-1 text-xs font-medium text-indigo-600 transition hover:bg-indigo-50 disabled:opacity-40"
                >
                  {saving ? "提取中…" : "存入选题库"}
                </button>
              </div>
            </div>

            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
              <div className="mx-auto max-w-3xl space-y-4">
                {messages.map((m, i) => {
                  const hasPlaceholders = m.role === "assistant" && listImagePlaceholders(m.content).length > 0;
                  const isFilling =
                    fillingMsgId != null &&
                    (fillingMsgId === "last"
                      ? i === messages.length - 1 && m.role === "assistant"
                      : m.id === fillingMsgId);
                  return (
                    <div key={m.id ?? i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div
                        className={`rounded-2xl px-4 py-2.5 text-sm leading-7 ${
                          m.role === "user"
                            ? "max-w-[85%] bg-indigo-600 text-white"
                            : "w-full max-w-3xl bg-white text-slate-700 shadow-sm"
                        }`}
                      >
                        {m.role === "assistant" && m.model_name && (
                          <div className="mb-1 text-[10px] uppercase text-slate-400">{m.model_name}</div>
                        )}
                        {m.role === "assistant" ? (
                          <ChatMediaContent
                            content={m.content}
                            generatingAll={isFilling}
                            onGenerateImage={(prompt) => {
                              send(`生成配图:${prompt},竖版小红书风格,干净排版`);
                            }}
                            onGenerateAllImages={
                              hasPlaceholders ? () => fillImagesForMessage(m.id) : undefined
                            }
                          />
                        ) : (
                          <ChatMediaContent content={m.content} className="text-inherit [&_figcaption]:text-indigo-100" />
                        )}
                      </div>
                    </div>
                  );
                })}
                {chatTask && (streaming || liveOutput || mediaStatus || taskError) && (
                  <div className="flex justify-start">
                    <div className="w-full max-w-3xl rounded-2xl bg-white px-4 py-2.5 text-sm leading-7 text-slate-700 shadow-sm">
                      {liveModelName && (
                        <div className="mb-1 text-[10px] uppercase text-slate-400">{liveModelName}</div>
                      )}
                      {mediaNotice && (
                        <p className="mb-1.5 rounded bg-indigo-50 px-2 py-1 text-[11px] text-indigo-600">{mediaNotice}</p>
                      )}
                      {mediaStatus && (
                        <p className="mb-1.5 text-xs text-slate-500">{mediaStatus}</p>
                      )}
                      {liveOutput ? (
                        <ChatMediaContent
                          content={liveOutput}
                          onGenerateImage={(prompt) => send(`生成配图:${prompt},竖版小红书风格,干净排版`)}
                          onGenerateAllImages={
                            listImagePlaceholders(liveOutput).length > 0 && !streaming
                              ? () => fillImagesForMessage()
                              : undefined
                          }
                        />
                      ) : null}
                      {streaming && !liveOutput && (
                        <p className="text-xs text-slate-400">
                          {selectedModality === "video"
                            ? "视频生成通常需要 1–3 分钟,请稍候…"
                            : selectedModality === "image"
                              ? "正在生成图片…"
                              : "正在思考…"}
                          <span className="ml-1 inline-block h-3 w-1.5 animate-pulse bg-indigo-400 align-middle" />
                        </p>
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
                {msg && <p className="text-center text-xs text-emerald-600">{msg}</p>}
              </div>
            </div>

            <div className="border-t border-slate-200 bg-white px-3 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-4">
              <div className="mx-auto max-w-3xl">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <select
                    value={accountId ?? ""}
                    onChange={(e) => setAccountId(e.target.value ? +e.target.value : null)}
                    className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 outline-none focus:border-indigo-400"
                  >
                    <option value="">不绑账号(选题模式)</option>
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
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
                  <select
                    value={providerId ?? ""}
                    onChange={(e) => {
                      setProviderId(e.target.value ? +e.target.value : null);
                      setModelId(null);
                    }}
                    className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 outline-none focus:border-indigo-400"
                  >
                    <option value="">默认模型</option>
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  {providerId && (
                    <select
                      value={modelId ?? ""}
                      onChange={(e) => setModelId(e.target.value || null)}
                      className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 outline-none focus:border-indigo-400"
                    >
                      <option value="">选择模型</option>
                      {models
                        .filter((m) => m.enabled)
                        .map((m) => {
                          const tag = modalityLabel(m.modality);
                          return (
                            <option key={m.id} value={m.model_id}>
                              {tag ? `[${tag}] ` : ""}
                              {m.display_name || m.model_id}
                            </option>
                          );
                        })}
                    </select>
                  )}
                  {selectedModality === "image" && (
                    <span className="rounded bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">图片生成</span>
                  )}
                  {selectedModality === "video" && (
                    <span className="rounded bg-rose-50 px-2 py-0.5 text-[11px] text-rose-700">视频生成</span>
                  )}
                </div>
                {pendingImages.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {pendingImages.map((img, idx) => (
                      <div key={img.url} className="relative h-16 w-16 overflow-hidden rounded-lg border border-slate-200">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={img.preview} alt="" className="h-full w-full object-cover" />
                        <button
                          type="button"
                          onClick={() => removePending(idx)}
                          className="absolute right-0.5 top-0.5 rounded bg-black/60 px-1 text-[10px] text-white"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex items-end gap-2">
                  <button
                    type="button"
                    title="上传图片(支持 OKR 截图识别)"
                    disabled={streaming || uploading}
                    onClick={() => fileInputRef.current?.click()}
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-slate-200 text-slate-500 hover:border-indigo-300 hover:text-indigo-600 disabled:opacity-40"
                  >
                    {uploading ? "…" : "🖼"}
                  </button>
                  <textarea
                    rows={1}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onPaste={onPasteImage}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        send();
                      }
                    }}
                    placeholder={
                      streaming
                        ? "AI 正在回复,可先去处理其他事情…"
                        : pendingImages.length
                          ? "可补充说明,如:识别图中的 OKR 并结构化整理…"
                          : selectedModality === "image"
                            ? "描述要生成的图片…"
                            : selectedModality === "video"
                              ? "描述要生成的视频…"
                              : "继续聊选题或文案…可粘贴/上传图片识别 OKR"
                    }
                    className="max-h-40 flex-1 resize-none rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:border-indigo-500"
                  />
                  <button
                    onClick={() => send()}
                    disabled={streaming || uploading || (!input.trim() && !pendingImages.length)}
                    className="h-10 w-10 shrink-0 rounded-full bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-200"
                  >
                    {streaming ? "…" : "↑"}
                  </button>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-2 text-[11px]">
                  <button
                    type="button"
                    className="rounded-full border border-slate-200 px-2 py-0.5 text-slate-500 hover:border-indigo-300 hover:text-indigo-600"
                    onClick={() => {
                      setInput("请识别图中的 OKR,结构化整理 Objective 与 Key Results,并给出改写建议");
                      fileInputRef.current?.click();
                    }}
                  >
                    上传 OKR 截图识别
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center px-4 py-8">
            <h1 className="text-4xl font-bold tracking-[0.3em] text-slate-700">织 火</h1>
            <p className="mt-3 text-sm text-slate-400">AI 自媒体创作 · 出文案、配图、识图 OKR</p>
            <p className="mt-1 max-w-md text-center text-[11px] text-slate-400">
              可上传/粘贴图片做 OKR 识别;写稿时的配图占位会自动生成预览
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-2 text-xs">
              <select
                value={accountId ?? ""}
                onChange={(e) => setAccountId(e.target.value ? +e.target.value : null)}
                className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-600 outline-none"
              >
                <option value="">不绑账号(选题模式)</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <select
                value={skillId ?? ""}
                onChange={(e) => setSkillId(e.target.value ? +e.target.value : null)}
                className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-600 outline-none"
              >
                <option value="">不使用技能</option>
                {chatSkills.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <select
                value={providerId ?? ""}
                onChange={(e) => {
                  setProviderId(e.target.value ? +e.target.value : null);
                  setModelId(null);
                }}
                className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-600 outline-none"
              >
                <option value="">默认模型</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {providerId && (
                <select
                  value={modelId ?? ""}
                  onChange={(e) => setModelId(e.target.value || null)}
                  className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-600 outline-none"
                >
                  <option value="">选择模型</option>
                  {models
                    .filter((m) => m.enabled)
                    .map((m) => {
                      const tag = modalityLabel(m.modality);
                      return (
                        <option key={m.id} value={m.model_id}>
                          {tag ? `[${tag}] ` : ""}
                          {m.display_name || m.model_id}
                        </option>
                      );
                    })}
                </select>
              )}
            </div>
            <div className="mt-6 w-full max-w-2xl rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
              {pendingImages.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-2 px-1">
                  {pendingImages.map((img, idx) => (
                    <div key={img.url} className="relative h-14 w-14 overflow-hidden rounded-lg border border-slate-200">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={img.preview} alt="" className="h-full w-full object-cover" />
                      <button
                        type="button"
                        onClick={() => removePending(idx)}
                        className="absolute right-0.5 top-0.5 rounded bg-black/60 px-1 text-[10px] text-white"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <textarea
                rows={3}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={onPasteImage}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder="问点什么,或上传/粘贴 OKR 截图…"
                className="w-full resize-none rounded-xl px-3 py-2 text-sm outline-none"
              />
              <div className="flex items-center justify-between px-1">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                    className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-500 hover:border-indigo-300 hover:text-indigo-600"
                  >
                    {uploading ? "上传中…" : "上传图片"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setInput("请识别图中的 OKR,结构化整理 Objective 与 Key Results,并给出改写建议");
                      fileInputRef.current?.click();
                    }}
                    className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-500 hover:border-indigo-300 hover:text-indigo-600"
                  >
                    OKR 识别
                  </button>
                  {SUGGESTIONS.slice(0, 1).map((s) => (
                    <button
                      key={s}
                      onClick={() => setInput(s)}
                      className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-500 hover:border-indigo-300 hover:text-indigo-600"
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => send()}
                  disabled={streaming || uploading || (!input.trim() && !pendingImages.length)}
                  className="h-10 w-10 shrink-0 rounded-full bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-200"
                >
                  ↑
                </button>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.slice(1).map((s) => (
                <button
                  key={s}
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
                    const sTask = getTask(`media-chat-${s.id}`);
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
                            className="hidden text-red-300 hover:text-red-500 group-hover:block"
                            onClick={(e) => {
                              e.stopPropagation();
                              removeSession(s.id);
                            }}
                          >
                            ✕
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
