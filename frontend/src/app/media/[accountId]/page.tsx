"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ChatMediaContent, modalityLabel } from "@/components/ChatMediaContent";
import { api, streamSSE } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { loadMediaAccountChatPrefs, saveMediaAccountChatPrefs } from "@/lib/chatPrefs";
import { filterSkillsForChat, useProduct } from "@/lib/product";
import { useTasks } from "@/lib/tasks";
import type { Provider, SkillInfo } from "@/lib/types";
import { Badge, Button, Empty, ErrorText, Input, Label, Select, Spinner, Textarea } from "@/components/ui";

interface Account {
  id: number;
  name: string;
  platform: string;
  persona: string | null;
  token_status?: string;
}

const PLATFORM_LABEL: Record<string, string> = {
  xhs: "小红书",
  wechat_mp: "公众号",
  douyin: "抖音",
  toutiao: "头条",
  other: "其他",
};

const PUBLISH_STATUS: Record<string, string> = {
  success: "发布成功",
  failed: "发布失败",
  need_manual: "需补凭证/人工",
  publishing: "发布中",
  queued: "排队中",
};

interface Topic {
  id: number;
  title: string;
  summary: string | null;
  source: string;
  status: string;
}

interface Article {
  id: number;
  title: string;
  content: string;
  status: string;
  word_count: number;
  updated_at: string;
}

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  model_name?: string | null;
}

interface MountRow {
  id: number;
  resource_type: string;
  resource_id: number;
  stage: string;
  resource_name: string | null;
}

interface ProviderModel {
  id: number;
  model_id: string;
  display_name: string | null;
  enabled: boolean;
  modality?: string;
}

export default function MediaWorkspacePage() {
  const params = useParams();
  const accountId = Number(params.accountId);
  const { user, loading: authLoading } = useAuth();
  const { setCurrent } = useProduct();
  const { startTask, getTask, clearTask } = useTasks();
  const router = useRouter();

  const [tab, setTab] = useState<"chat" | "topics" | "articles" | "settings" | "publish">("chat");
  const [account, setAccount] = useState<Account | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [articles, setArticles] = useState<Article[]>([]);
  const [publishTasks, setPublishTasks] = useState<
    {
      id: number;
      article_id: number;
      platform: string;
      status: string;
      error: string | null;
      result_url: string | null;
      created_at: string;
    }[]
  >([]);
  const [publishingId, setPublishingId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [topicId, setTopicId] = useState<number | null>(null);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillId, setSkillId] = useState<number | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [modelId, setModelId] = useState<string | null>(null);
  const [models, setModels] = useState<ProviderModel[]>([]);
  const [mounts, setMounts] = useState<MountRow[]>([]);
  const [mountSkillId, setMountSkillId] = useState("");
  const [mountKbId, setMountKbId] = useState("");
  const [kbs, setKbs] = useState<{ id: number; name: string }[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [ready, setReady] = useState(false);
  const [newTopic, setNewTopic] = useState({ title: "", summary: "" });
  const scrollRef = useRef<HTMLDivElement>(null);
  const prefsHydrated = useRef(false);

  const taskKey = `media-chat-${accountId}`;
  const chatTask = getTask(taskKey);
  const streaming = chatTask?.status === "running";
  const selectedModality =
    (modelId && models.find((m) => m.model_id === modelId)?.modality) || "text";
  const mediaStatus = String(chatTask?.meta?.mediaStatus || "");

  const loadCore = useCallback(async () => {
    prefsHydrated.current = false;
    const [accounts, chat, topicList, arts, sk, prov, mnt, kbList, tasks] = await Promise.all([
      api.get<Account[]>("/media/accounts"),
      api.get<{ messages: ChatMsg[] }>(`/media/accounts/${accountId}/chat`),
      api.get<Topic[]>(`/media/topics?account_id=${accountId}&status=active`),
      api.get<Article[]>(`/media/articles?account_id=${accountId}`),
      api.get<SkillInfo[]>("/skills"),
      api.get<Provider[]>("/providers"),
      api.get<MountRow[]>(`/mounts?product_key=media&workspace_type=media_account&workspace_id=${accountId}`),
      api.get<{ id: number; name: string }[]>("/kb"),
      api.get<
        {
          id: number;
          article_id: number;
          platform: string;
          status: string;
          error: string | null;
          result_url: string | null;
          created_at: string;
        }[]
      >(`/media/publish/tasks?account_id=${accountId}&limit=30`),
    ]);
    const acc = accounts.find((a) => a.id === accountId) || null;
    if (!acc) throw new Error("账号不存在");
    const prefs = loadMediaAccountChatPrefs(accountId);
    const mediaSkills = filterSkillsForChat(sk, "media");
    const enabledProviders = prov.filter((p) => p.enabled);
    setAccount(acc);
    setMessages(chat.messages || []);
    setTopics(topicList);
    setArticles(arts);
    setPublishTasks(tasks);
    setSkills(mediaSkills);
    setProviders(enabledProviders);
    setMounts(mnt);
    setKbs(kbList);
    setSkillId(prefs.skillId != null && mediaSkills.some((s) => s.id === prefs.skillId) ? prefs.skillId : null);
    if (prefs.providerId != null && enabledProviders.some((p) => p.id === prefs.providerId)) {
      setProviderId(prefs.providerId);
      setModelId(prefs.modelId);
    } else {
      setProviderId(null);
      setModelId(null);
    }
    prefsHydrated.current = true;
  }, [accountId]);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (user && accountId) {
      setCurrent("media");
      loadCore()
        .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
        .finally(() => setReady(true));
    }
  }, [user, authLoading, accountId, router, loadCore, setCurrent]);

  useEffect(() => {
    if (!prefsHydrated.current || !accountId) return;
    saveMediaAccountChatPrefs(accountId, { skillId, providerId, modelId });
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
          const prefs = loadMediaAccountChatPrefs(accountId);
          const preferred = prefs.modelId || current;
          if (preferred && list.some((m) => m.enabled && m.model_id === preferred)) {
            return preferred;
          }
          return null;
        });
      })
      .catch(() => setModels([]));
  }, [providerId, accountId]);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages, chatTask?.output]);

  function send() {
    const text = input.trim();
    if (!text || streaming) return;
    setError("");
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    const usedTopic = topicId;
    startTask({
      key: taskKey,
      label: `${account?.name || "自媒体"}对话`,
      href: `/media/${accountId}`,
      run: async (reporter) => {
        let modelName: string | null = null;
        await streamSSE(
          `/media/accounts/${accountId}/chat`,
          {
            content: text,
            topic_id: usedTopic,
            skill_id: skillId,
            provider_id: providerId,
            model_id: modelId,
            auto_images: true,
          },
          (ev) => {
            if (ev.type === "delta") reporter.pushOutput(String(ev.content || ""));
            if (ev.type === "content_replace" && ev.content != null) reporter.setOutput(String(ev.content));
            if (ev.type === "media_status") reporter.setMeta({ mediaStatus: String(ev.detail || "") });
            if (ev.type === "notice") reporter.setMeta({ notice: String(ev.detail || "") });
            if (ev.type === "model") modelName = String(ev.model_name || ev.model || "") || null;
            if (ev.type === "error") throw new Error(String(ev.detail || "生成失败"));
          }
        );
        return { modelName };
      },
    });
  }

  useEffect(() => {
    if (chatTask?.status === "done" && chatTask.output) {
      const modelName = (chatTask.result as { modelName?: string | null } | undefined)?.modelName;
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.content === chatTask.output) return prev;
        return [...prev, { role: "assistant", content: chatTask.output, model_name: modelName }];
      });
      if (topicId) {
        setTopics((prev) => prev.filter((t) => t.id !== topicId));
        setTopicId(null);
      }
      clearTask(taskKey);
    }
    if (chatTask?.status === "error") {
      setError(chatTask.error || "发送失败");
    }
  }, [chatTask?.status, chatTask?.output, chatTask?.result, chatTask?.error, clearTask, taskKey, topicId]);

  async function saveArticle() {
    setError("");
    try {
      const art = await api.post<Article>(`/media/accounts/${accountId}/chat/save-article`, {
        topic_id: topicId,
        status: "draft",
      });
      setMsg(`已保存成稿「${art.title}」`);
      setArticles(await api.get<Article[]>(`/media/articles?account_id=${accountId}`));
      setTab("articles");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function publishOne(articleId: number) {
    setError("");
    setMsg("");
    setPublishingId(articleId);
    try {
      const task = await api.post<{
        id: number;
        status: string;
        error: string | null;
        result_url: string | null;
        platform: string;
      }>("/media/publish", { article_id: articleId, account_id: accountId });
      if (task.status === "success") {
        setMsg(`一键发布成功(${PLATFORM_LABEL[task.platform] || task.platform})`);
      } else if (task.status === "need_manual") {
        setError(task.error || "需先配置发布凭证");
      } else {
        setError(task.error || "发布失败");
      }
      setPublishTasks(await api.get(`/media/publish/tasks?account_id=${accountId}&limit=30`));
      setArticles(await api.get<Article[]>(`/media/articles?account_id=${accountId}`));
      setTab("publish");
    } catch (e) {
      setError(e instanceof Error ? e.message : "发布失败");
    } finally {
      setPublishingId(null);
    }
  }

  async function createTopic() {
    if (!newTopic.title.trim()) return;
    await api.post("/media/topics", {
      title: newTopic.title.trim(),
      summary: newTopic.summary || null,
      account_id: accountId,
      source: "manual",
    });
    setNewTopic({ title: "", summary: "" });
    setTopics(await api.get<Topic[]>(`/media/topics?account_id=${accountId}&status=active`));
  }

  async function expandTopic(id: number) {
    setError("");
    try {
      const res = await api.post<{ created: number }>(`/media/topics/${id}/expand`, { account_id: accountId, count: 5 });
      setMsg(`已扩写 ${res.created} 个角度`);
      setTopics(await api.get<Topic[]>(`/media/topics?account_id=${accountId}&status=active`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "扩写失败");
    }
  }

  async function addMount(resource_type: "skill" | "kb", resource_id: number) {
    await api.post("/mounts", {
      product_key: "media",
      workspace_type: "media_account",
      workspace_id: accountId,
      resource_type,
      resource_id,
      stage: resource_type === "skill" ? "chat" : "",
    });
    setMounts(
      await api.get<MountRow[]>(`/mounts?product_key=media&workspace_type=media_account&workspace_id=${accountId}`)
    );
  }

  async function removeMount(id: number) {
    await api.delete(`/mounts/${id}`);
    setMounts(
      await api.get<MountRow[]>(`/mounts?product_key=media&workspace_type=media_account&workspace_id=${accountId}`)
    );
  }

  if (authLoading || !ready) return <Spinner />;
  if (!account) return <Empty text="账号不存在" />;

  const liveAssistant = streaming ? chatTask?.output || "" : "";

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link href="/media" className="text-xs text-slate-400 hover:text-indigo-600">
            ← 账号列表
          </Link>
          <h1 className="text-xl font-bold">{account.name}</h1>
          <p className="text-xs text-slate-400">
            聊天创作 · 选题 · 成稿 · 一键发布
            {account.token_status === "ready" ? " · 凭证已就绪" : " · 请先在账号页配置 Token"}
          </p>
        </div>
        <div className="-mx-1 flex max-w-full gap-1 overflow-x-auto rounded-lg bg-slate-100 p-1 text-sm scrollbar-none">
          {(
            [
              ["chat", "创作"],
              ["topics", "选题"],
              ["articles", "成稿"],
              ["publish", "发布"],
              ["settings", "设置"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`shrink-0 rounded-md px-3 py-1.5 font-medium ${tab === k ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      {msg && <p className="mb-2 text-xs text-emerald-600">{msg}</p>}

      {tab === "chat" && (
        <div className="flex flex-col rounded-xl border border-slate-200 bg-white" style={{ minHeight: "28rem" }}>
          <div className="flex flex-col gap-2 border-b border-slate-100 p-3 text-xs sm:flex-row sm:flex-wrap">
            <Select
              value={topicId ?? ""}
              onChange={(e) => setTopicId(e.target.value ? +e.target.value : null)}
              className="w-full sm:min-w-[10rem] sm:w-auto"
            >
              <option value="">不指定选题</option>
              {topics.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </Select>
            <Select
              value={skillId ?? ""}
              onChange={(e) => setSkillId(e.target.value ? +e.target.value : null)}
              className="w-full sm:min-w-[8rem] sm:w-auto"
            >
              <option value="">默认 Skill</option>
              {skills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
            <Select
              value={providerId ?? ""}
              onChange={(e) => {
                setProviderId(e.target.value ? +e.target.value : null);
                setModelId(null);
              }}
              className="w-full sm:min-w-[7rem] sm:w-auto"
            >
              <option value="">默认模型</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
            {providerId && (
              <Select
                value={modelId ?? ""}
                onChange={(e) => setModelId(e.target.value || null)}
                className="w-full sm:min-w-[9rem] sm:w-auto"
              >
                <option value="">选择模型</option>
                {models.filter((m) => m.enabled).map((m) => {
                  const tag = modalityLabel(m.modality);
                  return (
                    <option key={m.id} value={m.model_id}>
                      {tag ? `[${tag}] ` : ""}
                      {m.display_name || m.model_id}
                    </option>
                  );
                })}
              </Select>
            )}
            {selectedModality === "image" && (
              <span className="self-center rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-700">图片生成</span>
            )}
            {selectedModality === "video" && (
              <span className="self-center rounded bg-rose-50 px-2 py-1 text-[11px] text-rose-700">视频生成</span>
            )}
            <Button size="sm" variant="outline" onClick={saveArticle} disabled={streaming || selectedModality !== "text"}>
              保存成稿
            </Button>
          </div>
          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4" style={{ maxHeight: "22rem" }}>
            {messages.length === 0 && !liveAssistant && !mediaStatus && (
              <p className="text-center text-sm text-slate-400">选一个选题,描述要写的内容,开始聊天创作</p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`rounded-lg px-3 py-2 text-sm ${m.role === "user" ? "bg-indigo-50 text-slate-800" : "bg-slate-50 text-slate-700"}`}>
                <div className="mb-1 text-[10px] uppercase text-slate-400">{m.role === "user" ? "你" : m.model_name || "助手"}</div>
                {m.role === "assistant" ? (
                  <ChatMediaContent
                    content={m.content}
                    onGenerateImage={(prompt) => {
                      setInput(`生成配图:${prompt},竖版小红书风格,干净排版`);
                    }}
                  />
                ) : (
                  <div className="whitespace-pre-wrap leading-6">{m.content}</div>
                )}
              </div>
            ))}
            {(liveAssistant || (streaming && mediaStatus)) && (
              <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
                <div className="mb-1 text-[10px] text-slate-400">助手 · 生成中</div>
                {mediaStatus && !liveAssistant && <p className="text-xs text-slate-500">{mediaStatus}</p>}
                {liveAssistant && <ChatMediaContent content={liveAssistant} />}
              </div>
            )}
          </div>
          <div className="border-t border-slate-100 p-3">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={3}
              placeholder={
                selectedModality === "image"
                  ? "描述要生成的配图…"
                  : selectedModality === "video"
                    ? "描述要生成的短视频…"
                    : "例如:按人设写一篇种草笔记,突出痛点与对比…"
              }
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send();
              }}
            />
            <div className="mt-2 flex justify-end">
              <Button onClick={send} disabled={!input.trim() || streaming}>
                {streaming ? "生成中…" : "发送 Ctrl+Enter"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {tab === "topics" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-2 text-sm font-semibold">手动新建选题</h3>
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
              <Input value={newTopic.title} onChange={(e) => setNewTopic({ ...newTopic, title: e.target.value })} placeholder="标题" />
              <Input value={newTopic.summary} onChange={(e) => setNewTopic({ ...newTopic, summary: e.target.value })} placeholder="摘要(可选)" />
              <Button onClick={createTopic} disabled={!newTopic.title.trim()}>
                添加
              </Button>
            </div>
            <p className="mt-2 text-xs text-slate-400">
              更多导入 / RSS 见 <Link href="/media/topics" className="text-indigo-600">选题库</Link>
            </p>
          </div>
          {topics.length === 0 ? (
            <Empty text="暂无活跃选题" />
          ) : (
            <div className="space-y-2">
              {topics.map((t) => (
                <div key={t.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white p-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800">{t.title}</span>
                      <Badge color="slate">{t.source}</Badge>
                    </div>
                    {t.summary && <p className="mt-1 text-xs text-slate-500">{t.summary}</p>}
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => { setTopicId(t.id); setTab("chat"); }}>
                      用来写作
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => expandTopic(t.id)}>
                      AI 扩写
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "articles" && (
        <div className="space-y-2">
          {articles.length === 0 ? (
            <Empty text="还没有成稿,在创作页一键保存助手回复即可" />
          ) : (
            articles.map((a) => (
              <details key={a.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <summary className="cursor-pointer list-none">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{a.title}</span>
                    <span className="text-xs text-slate-400">
                      {a.word_count} 字 · {a.status} · {new Date(a.updated_at).toLocaleString()}
                    </span>
                  </div>
                </summary>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    disabled={publishingId === a.id}
                    onClick={() => publishOne(a.id)}
                  >
                    {publishingId === a.id
                      ? "发布中…"
                      : `一键发${PLATFORM_LABEL[account.platform] || "平台"}`}
                  </Button>
                  <Link href="/media" className="text-xs text-slate-400 hover:text-indigo-600">
                    去配置发布凭证 →
                  </Link>
                </div>
                <div className="mt-3 max-h-80 overflow-auto rounded-lg bg-slate-50 p-3 text-sm leading-6 text-slate-700">
                  <ChatMediaContent content={a.content} />
                </div>
              </details>
            ))
          )}
        </div>
      )}

      {tab === "publish" && (
        <div className="space-y-3">
          <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-3 py-2 text-xs leading-5 text-indigo-700">
            使用账号「{account.name}」绑定的 {PLATFORM_LABEL[account.platform] || account.platform} 凭证发布。
            公众号写入草稿箱;小红书/抖音/头条需在账号页配置 Token + 发布 API 地址。
          </div>
          {articles.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {articles.slice(0, 5).map((a) => (
                <Button
                  key={a.id}
                  size="sm"
                  variant="outline"
                  disabled={publishingId === a.id}
                  onClick={() => publishOne(a.id)}
                >
                  {publishingId === a.id ? "…" : `发「${a.title.slice(0, 12)}」`}
                </Button>
              ))}
            </div>
          )}
          {publishTasks.length === 0 ? (
            <Empty text="还没有发布记录,在成稿里点「一键发布」" />
          ) : (
            <ul className="space-y-2">
              {publishTasks.map((t) => {
                const art = articles.find((a) => a.id === t.article_id);
                return (
                  <li key={t.id} className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">{art?.title || `成稿 #${t.article_id}`}</span>
                      <Badge
                        color={
                          t.status === "success" ? "green" : t.status === "failed" ? "red" : t.status === "need_manual" ? "amber" : "slate"
                        }
                      >
                        {PUBLISH_STATUS[t.status] || t.status}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-slate-400">
                      {PLATFORM_LABEL[t.platform] || t.platform} · {new Date(t.created_at).toLocaleString()}
                      {t.result_url ? (
                        <>
                          {" · "}
                          <a href={t.result_url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">
                            打开平台
                          </a>
                        </>
                      ) : null}
                    </p>
                    {t.error && <p className="mt-1 text-xs text-red-600">{t.error}</p>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {tab === "settings" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-2 text-sm font-semibold">挂载 Skills / 知识库</h3>
            <p className="mb-3 text-xs text-slate-400">挂载后会在聊天系统提示中自动注入</p>
            <div className="mb-3 flex flex-wrap gap-2">
              <Select value={mountSkillId} onChange={(e) => setMountSkillId(e.target.value)} className="min-w-[10rem]">
                <option value="">选择 Skill</option>
                {skills.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
              <Button
                size="sm"
                disabled={!mountSkillId}
                onClick={() => {
                  addMount("skill", +mountSkillId);
                  setMountSkillId("");
                }}
              >
                挂载 Skill
              </Button>
              <Select value={mountKbId} onChange={(e) => setMountKbId(e.target.value)} className="min-w-[10rem]">
                <option value="">选择知识库</option>
                {kbs.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.name}
                  </option>
                ))}
              </Select>
              <Button
                size="sm"
                disabled={!mountKbId}
                onClick={() => {
                  addMount("kb", +mountKbId);
                  setMountKbId("");
                }}
              >
                挂载 KB
              </Button>
            </div>
            {mounts.length === 0 ? (
              <p className="text-xs text-slate-400">尚未挂载资源</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {mounts.map((m) => (
                  <li key={m.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2">
                    <span>
                      <Badge color="slate">{m.resource_type}</Badge> {m.resource_name || `#${m.resource_id}`}
                    </span>
                    <button className="text-xs text-slate-400 hover:text-red-500" onClick={() => removeMount(m.id)}>
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {account.persona && (
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600">
              <Label>当前人设</Label>
              <p className="mt-1 whitespace-pre-wrap leading-6">{account.persona}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
