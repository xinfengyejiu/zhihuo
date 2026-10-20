"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  Novel,
  PlatformAccountInfo,
  PublishChapterInfo,
  PublishTaskInfo,
} from "@/lib/types";
import { PLATFORMS, PLATFORM_LABEL } from "@/lib/types";
import { Badge, Button, ChapterStatusBadge, Empty, ErrorText, Input, Label, Select, Spinner } from "@/components/ui";

const TASK_STATUS: Record<string, { label: string; color: string }> = {
  queued: { label: "待发布", color: "blue" },
  scheduled: { label: "已排期", color: "amber" },
  need_manual: { label: "到期待发布", color: "amber" },
  success: { label: "发布成功", color: "green" },
  failed: { label: "失败", color: "red" },
};

const ACCOUNT_STATUS: Record<string, { label: string; color: string }> = {
  not_configured: { label: "未登录", color: "slate" },
  logged_in: { label: "已登录", color: "green" },
  expired: { label: "已过期", color: "red" },
};

export default function PublishPage() {
  const [tab, setTab] = useState<"tasks" | "accounts">("tasks");
  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">发布中心</h1>
      <div className="mb-4 flex gap-1 rounded-lg bg-slate-100 p-1 text-sm">
        {([
          ["tasks", "发布任务"],
          ["accounts", "平台账号"],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex-1 rounded-md py-1.5 font-medium transition ${tab === key ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "tasks" ? <TasksTab /> : <AccountsTab />}
    </div>
  );
}

/* ================= 发布任务 ================= */
function TasksTab() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelId, setNovelId] = useState<number | null>(null);
  const [chapters, setChapters] = useState<PublishChapterInfo[]>([]);
  const [tasks, setTasks] = useState<PublishTaskInfo[]>([]);
  const [selectedChapters, setSelectedChapters] = useState<number[]>([]);
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [scheduleAt, setScheduleAt] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const loadNovels = useCallback(async () => {
    const ns = await api.get<Novel[]>("/novels");
    setNovels(ns);
    if (ns.length > 0) setNovelId((prev) => prev ?? ns[0].id);
    setLoading(false);
  }, []);

  const loadNovelData = useCallback(async () => {
    if (!novelId) return;
    const novel = novels.find((n) => n.id === novelId);
    if (novel) setSelectedPlatforms(novel.platforms.map((p) => p.platform));
    setChapters(await api.get<PublishChapterInfo[]>(`/publish/chapters?novel_id=${novelId}`));
    setTasks(await api.get<PublishTaskInfo[]>(`/publish/tasks?novel_id=${novelId}`));
  }, [novelId, novels]);

  useEffect(() => {
    loadNovels();
  }, [loadNovels]);
  useEffect(() => {
    loadNovelData();
  }, [loadNovelData]);

  function toggle(list: number[], id: number) {
    return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
  }

  async function createTasks(scheduled: boolean) {
    setError("");
    setMsg("");
    if (selectedChapters.length === 0) return setError("请勾选要发布的章节");
    if (selectedPlatforms.length === 0) return setError("请勾选发布平台");
    setBusy(true);
    try {
      const created = await api.post<PublishTaskInfo[]>("/publish/tasks", {
        chapter_ids: selectedChapters,
        platforms: selectedPlatforms,
        scheduled_at: scheduled && scheduleAt ? new Date(scheduleAt).toISOString() : null,
      });
      setMsg(`已创建 ${created.length} 个发布任务:${created.map((t) => `${t.chapter_title}→${PLATFORM_LABEL[t.platform]}`).join("、")}`);
      setSelectedChapters([]);
      await loadNovelData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function copyChapter(chapterId: number, title: string) {
    try {
      const data = await api.get<{ content: string }>(`/novels/${novelId}/chapters/${chapterId}/content`);
      await navigator.clipboard.writeText(`${title}\n\n${data.content}`);
      setMsg(`《${title}》已复制到剪贴板,去平台后台粘贴发布`);
    } catch {
      setError("复制失败");
    }
  }

  async function taskAction(id: number, action: "success" | "retry" | "delete" | "open") {
    setError("");
    setMsg("");
    try {
      if (action === "delete") {
        if (!confirm("确定删除该发布任务?")) return;
        await api.delete(`/publish/tasks/${id}`);
      } else if (action === "success") {
        await api.post(`/publish/tasks/${id}/success`, {});
      } else if (action === "retry") {
        await api.post(`/publish/tasks/${id}/retry`);
      } else if (action === "open") {
        const res = await api.post<{ ok: boolean; message: string }>(`/publish/tasks/${id}/open-platform`);
        (res.ok ? setMsg : setError)(res.message);
        return;
      }
      await loadNovelData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    }
  }

  if (loading) return <Spinner />;
  if (novels.length === 0) return <Empty text="先在书架创建小说" />;

  const publishable = chapters;

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-700">
        半自动模式:勾选「审核通过」的章节和平台 → 创建发布任务 → 点「复制文本」或「打开平台后台」去平台完成发布 → 回来点「标记已发布」。
        设置排期时间的任务到期后会自动变成「到期待发布」。全平台完成后章节自动标记「已发布」,书架显示各平台进度。
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div>
            <Label>选择书籍</Label>
            <Select value={novelId ?? ""} onChange={(e) => setNovelId(+e.target.value)}>
              {novels.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.title}
                </option>
              ))}
            </Select>
          </div>
          <div className="w-full min-w-0 sm:min-w-56">
            <Label>发布平台</Label>
            <div className="flex flex-wrap gap-1.5">
              {PLATFORMS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => setSelectedPlatforms((prev) => (prev.includes(p.key) ? prev.filter((x) => x !== p.key) : [...prev, p.key]))}
                  className={`rounded-full px-2.5 py-1 text-xs ${selectedPlatforms.includes(p.key) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <Label>定时发布(可选)</Label>
            <Input type="datetime-local" value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
          </div>
          <div className="flex gap-2 pb-0.5">
            <Button onClick={() => createTasks(false)} disabled={busy}>
              立即发布
            </Button>
            <Button variant="outline" onClick={() => createTasks(true)} disabled={busy || !scheduleAt}>
              定时发布
            </Button>
          </div>
        </div>

        {publishable.length === 0 ? (
          <Empty text="该书还没有审核通过的章节" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-slate-400">
                <tr>
                  <th className="w-8 py-2"></th>
                  <th>章节</th>
                  <th>状态</th>
                  <th>各平台任务</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {publishable.map((c) => (
                  <tr key={c.id}>
                    <td className="py-2">
                      <input
                        type="checkbox"
                        checked={selectedChapters.includes(c.id)}
                        onChange={() => setSelectedChapters((prev) => toggle(prev, c.id))}
                        className="accent-indigo-600"
                      />
                    </td>
                    <td className="py-2 font-medium text-slate-700">{c.title}</td>
                    <td>
                      <ChapterStatusBadge status={c.status} />
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {c.tasks.map((t, i) => (
                          <Badge key={i} color={TASK_STATUS[t.status]?.color}>
                            {PLATFORM_LABEL[t.platform] || t.platform}:{TASK_STATUS[t.status]?.label || t.status}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className="py-2 text-right">
                      <button className="text-xs text-indigo-600 hover:underline" onClick={() => copyChapter(c.id, c.title)}>
                        复制文本
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="mb-3 text-sm font-semibold">任务记录</h3>
        {tasks.length === 0 ? (
          <Empty text="还没有发布任务" />
        ) : (
          <div className="space-y-2">
            {tasks.map((t) => (
              <div key={t.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <span className="font-medium">{t.chapter_title}</span>
                  <span className="mx-2 text-xs text-slate-400">→ {PLATFORM_LABEL[t.platform] || t.platform}</span>
                  <Badge color={TASK_STATUS[t.status]?.color}>{TASK_STATUS[t.status]?.label || t.status}</Badge>
                  {t.scheduled_at && t.status === "scheduled" && (
                    <span className="ml-2 text-xs text-slate-400">排期:{new Date(t.scheduled_at).toLocaleString("zh-CN")}</span>
                  )}
                  {t.error && <span className="ml-2 text-xs text-red-500">{t.error}</span>}
                </div>
                <div className="flex shrink-0 gap-2 text-xs">
                  {t.status !== "success" && (
                    <button className="text-emerald-600 hover:underline" onClick={() => taskAction(t.id, "success")}>
                      标记已发布
                    </button>
                  )}
                  {t.status !== "success" && (
                    <button className="text-indigo-600 hover:underline" onClick={() => taskAction(t.id, "open")}>
                      打开平台后台
                    </button>
                  )}
                  {t.status === "failed" && (
                    <button className="text-amber-600 hover:underline" onClick={() => taskAction(t.id, "retry")}>
                      重试
                    </button>
                  )}
                  <button className="text-red-500 hover:underline" onClick={() => taskAction(t.id, "delete")}>
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <ErrorText>{error}</ErrorText>
      {msg && <p className="text-xs text-emerald-600">{msg}</p>}
    </div>
  );
}

/* ================= 平台账号 ================= */
function AccountsTab() {
  const [accounts, setAccounts] = useState<PlatformAccountInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setAccounts(await api.get<PlatformAccountInfo[]>("/publish/accounts"));
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function setStatus(platform: string, status: string) {
    await api.put(`/publish/accounts/${platform}`, { status });
    setMsg(`${PLATFORM_LABEL[platform]} 状态已更新`);
    load();
  }

  if (loading) return <Spinner />;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-amber-100 bg-amber-50 p-3 text-xs text-amber-700">
        自动发布(M3 适配器)需要后端安装 Playwright,并在平台后台完成一次登录(登录态持久化在本机)。当前可手动标记各账号登录状态;「发布任务」页的「打开平台后台」会用带登录态的浏览器打开发布页。
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {accounts.map((a) => (
          <div key={a.platform} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <h4 className="font-medium">{PLATFORM_LABEL[a.platform] || a.platform}</h4>
              <Badge color={ACCOUNT_STATUS[a.status]?.color}>{ACCOUNT_STATUS[a.status]?.label || a.status}</Badge>
            </div>
            <p className="mt-1 text-xs text-slate-400">
              更新于 {new Date(a.updated_at).toLocaleString("zh-CN")}
              {a.note ? ` · ${a.note}` : ""}
            </p>
            <div className="mt-3 flex gap-2 text-xs">
              {a.status !== "logged_in" && (
                <button className="text-emerald-600 hover:underline" onClick={() => setStatus(a.platform, "logged_in")}>
                  标记已登录
                </button>
              )}
              {a.status !== "expired" && (
                <button className="text-amber-600 hover:underline" onClick={() => setStatus(a.platform, "expired")}>
                  标记过期
                </button>
              )}
              {a.status !== "not_configured" && (
                <button className="text-slate-500 hover:underline" onClick={() => setStatus(a.platform, "not_configured")}>
                  重置
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {msg && <p className="text-xs text-emerald-600">{msg}</p>}
    </div>
  );
}
