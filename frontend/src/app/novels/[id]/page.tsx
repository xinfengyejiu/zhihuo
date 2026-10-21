"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { api, getApiBase, readerNovelUrl } from "@/lib/api";
import { resolveAssetUrl } from "@/lib/assetCache";
import { filterSkillsForChat } from "@/lib/product";
import type { Chapter, CollabInfo, KbInfo, Novel, SkillInfo } from "@/lib/types";
import { PLATFORMS, PLATFORM_LABEL } from "@/lib/types";
import { Badge, Button, CHAPTER_STATUS_LABELS, Empty, ErrorText, Input, Label, Modal, Select, Spinner, Textarea } from "@/components/ui";

interface Stats {
  novel_id: number;
  title: string;
  target_words: number | null;
  published_words: number;
  total_chapters: number;
  by_status: Record<string, { count: number; words: number }>;
}

const STAGE_KEYS = ["outline", "chapter", "polish", "analysis"] as const;
const STAGE_LABELS: Record<string, string> = { outline: "大纲", chapter: "正文", polish: "润色", analysis: "复盘", review: "审核" };

export default function NovelOverviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [novel, setNovel] = useState<Novel | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [n, s, c] = await Promise.all([
        api.get<Novel>(`/novels/${id}`),
        api.get<Stats>(`/novels/${id}/stats`),
        api.get<Chapter[]>(`/novels/${id}/chapters`),
      ]);
      setNovel(n);
      setStats(s);
      setChapters(c);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <Spinner />;
  if (!novel) return <Empty text="小说不存在" />;

  const latest = [...chapters].sort((a, b) => b.sort - a.sort)[0];
  const pct = stats?.target_words ? Math.min(100, Math.round((stats.published_words / stats.target_words) * 100)) : 0;
  const isOwner = novel.my_role === "owner";

  return (
    <div>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold leading-snug">{novel.title}</h1>
            {novel.my_role && novel.my_role !== "owner" && <Badge color="indigo">{novel.my_role === "reviewer" ? "审核协作" : "编辑协作"}</Badge>}
          </div>
          <p className="mt-1 text-sm leading-6 text-slate-500">{novel.summary}</p>
        </div>
        <div className="flex shrink-0 gap-2">
          <a href={readerNovelUrl(novel.id)} target="_blank" rel="noreferrer" className="flex-1 sm:flex-none">
            <Button variant="outline" className="w-full sm:w-auto">📖 阅读器</Button>
          </a>
          <Link href={`/novels/${novel.id}/outline`} className="flex-1 sm:flex-none">
            <Button className="w-full sm:w-auto">写作</Button>
          </Link>
        </div>
      </div>

      <CoverCard novel={novel} onChanged={load} />

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Object.entries(CHAPTER_STATUS_LABELS).map(([status, meta]) => (
          <div key={status} className="rounded-xl border border-slate-200 bg-white p-3">
            <div className="text-xs text-slate-400">{meta.label}</div>
            <div className="mt-1 text-xl font-semibold">{stats?.by_status?.[status]?.count ?? 0}</div>
          </div>
        ))}
      </div>

      <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-2 flex items-center justify-between text-sm">
          <span className="font-medium">发布字数进度</span>
          <span className="text-slate-500">
            {stats?.published_words.toLocaleString() || 0}
            {stats?.target_words ? ` / ${stats.target_words.toLocaleString()} 字 (${pct}%)` : " 字"}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full rounded-full bg-indigo-500" style={{ width: `${pct}%` }} />
        </div>
        {novel.platforms.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
            {novel.platforms.map((p) => (
              <span key={p.platform} className="rounded-full bg-slate-50 px-2.5 py-1">
                {PLATFORM_LABEL[p.platform] || p.platform} · 已发 {p.published_chapter_no} 章
              </span>
            ))}
          </div>
        )}
      </div>

      {latest ? (
        <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-medium">最新章节</h2>
            <Badge color={CHAPTER_STATUS_LABELS[latest.status]?.color}>{CHAPTER_STATUS_LABELS[latest.status]?.label}</Badge>
          </div>
          <button
            className="text-left font-medium text-indigo-600 hover:underline"
            onClick={() => router.push(`/novels/${novel.id}/chapters/${latest.id}`)}
          >
            {latest.title}
          </button>
          <p className="mt-1 text-xs text-slate-400">
            {latest.word_count} 字 · 更新于 {new Date(latest.updated_at).toLocaleString("zh-CN")}
          </p>
        </div>
      ) : (
        <Empty text="还没有章节,去「大纲 · 章节」创建" />
      )}

      <AiConfigCard novelId={novel.id} />
      <TeamCard novelId={novel.id} isOwner={isOwner} />
      <ExportCard novelId={novel.id} />
      <EditNovelModal novel={novel} onSaved={load} />
    </div>
  );
}

/* ================= 封面:上传 / AI 按关键词生成 ================= */
function AuthedCoverImg({ url, alt, className }: { url: string; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  const src = url && !failed ? resolveAssetUrl(url) : "";
  if (!src) {
    return <div className={`animate-pulse bg-slate-100 ${className || ""}`} />;
  }
  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

function CoverCard({ novel, onChanged }: { novel: Novel; onChanged: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const tagKeywords = novel.tags.map((t) => t.name).filter(Boolean).join("、");
  const [keywords, setKeywords] = useState(tagKeywords);
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState<"upload" | "gen" | "clear" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setKeywords((prev) => (prev.trim() ? prev : tagKeywords));
  }, [tagKeywords]);

  async function uploadFile(file: File) {
    setBusy("upload");
    setError("");
    setNotice("");
    try {
      const form = new FormData();
      form.append("file", file);
      await api.upload<Novel>(`/novels/${novel.id}/cover/upload`, form);
      setNotice("封面已上传");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(null);
    }
  }

  async function generate() {
    setBusy("gen");
    setError("");
    setNotice("");
    try {
      await api.post<Novel>(`/novels/${novel.id}/cover/generate`, {
        keywords: keywords.trim() || null,
        extra: extra.trim() || null,
      });
      setNotice("封面已生成");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setBusy(null);
    }
  }

  async function clearCover() {
    if (!novel.cover || !confirm("确定清除当前封面?")) return;
    setBusy("clear");
    setError("");
    try {
      await api.delete<Novel>(`/novels/${novel.id}/cover`);
      setNotice("已清除封面");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "清除失败");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">封面</h2>
        {notice && <span className="text-xs text-emerald-600">{notice}</span>}
      </div>
      <div className="flex flex-col gap-4 sm:flex-row">
        <div className="mx-auto w-36 shrink-0 sm:mx-0">
          <div className="aspect-[3/4] overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
            {novel.cover ? (
              <AuthedCoverImg url={novel.cover} alt={`${novel.title}封面`} className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full items-center justify-center px-3 text-center text-xs leading-5 text-slate-400">
                暂无封面
                <br />
                可上传或 AI 生成
              </div>
            )}
          </div>
        </div>
        <div className="min-w-0 flex-1 space-y-2.5">
          <div>
            <Label>关键词(题材 / 世界观 / 氛围)</Label>
            <Input
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder="如:武侠穿越、系统流、诸天、破碎虚空"
            />
            <p className="mt-1 text-[11px] text-slate-400">
              默认带上本书标签;也可改成你想强调的画面关键词。会结合书名、简介与画风提示生成。
            </p>
          </div>
          <div>
            <Label>画面补充(可选)</Label>
            <Textarea
              rows={2}
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              placeholder="如:主角背对镜头望向破碎天穹,冷色调"
            />
          </div>
          <ErrorText>{error}</ErrorText>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void uploadFile(f);
              }}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!!busy}
              onClick={() => fileRef.current?.click()}
            >
              {busy === "upload" ? "上传中…" : "上传封面"}
            </Button>
            <Button size="sm" disabled={!!busy} onClick={() => void generate()}>
              {busy === "gen" ? "生成中…" : "AI 生成封面"}
            </Button>
            {novel.cover && (
              <Button variant="ghost" size="sm" disabled={!!busy} onClick={() => void clearCover()}>
                {busy === "clear" ? "清除中…" : "清除"}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ================= AI 配置(挂技能/挂知识库) ================= */
function AiConfigCard({ novelId }: { novelId: number }) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [mounted, setMounted] = useState<Record<string, number>>({});
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [mountedKbs, setMountedKbs] = useState<number[]>([]);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const [sk, mt, kbList, kbMounted] = await Promise.all([
      api.get<SkillInfo[]>("/skills"),
      api.get<Record<string, number>>(`/novels/${novelId}/skills`),
      api.get<KbInfo[]>("/kb"),
      api.get<{ kb_ids: number[] }>(`/novels/${novelId}/kbs`),
    ]);
    setSkills(filterSkillsForChat(sk, "novel"));
    setMounted(mt);
    setKbs(kbList);
    setMountedKbs(kbMounted.kb_ids);
  }, [novelId]);

  useEffect(() => {
    load();
  }, [load]);

  async function mountStage(stage: string, skillId: string) {
    await api.put(`/novels/${novelId}/skills`, { stage, skill_id: skillId ? +skillId : null });
    setMsg(`已更新「${STAGE_LABELS[stage]}」技能挂载`);
    load();
  }

  async function toggleKb(kbId: number) {
    const next = mountedKbs.includes(kbId) ? mountedKbs.filter((x) => x !== kbId) : [...mountedKbs, kbId];
    await api.put(`/novels/${novelId}/kbs`, { kb_ids: next });
    setMsg("已更新知识库挂载");
    load();
  }

  return (
    <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium">AI 配置</h2>
        <Link href="/skills" className="text-xs text-indigo-600 hover:underline">
          管理技能 →
        </Link>
      </div>
      {msg && <p className="mb-2 text-xs text-emerald-600">{msg}</p>}
      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {STAGE_KEYS.map((stage) => (
          <div key={stage}>
            <Label>{STAGE_LABELS[stage]}技能</Label>
            <Select value={mounted[stage] ?? ""} onChange={(e) => mountStage(stage, e.target.value)} className="w-full">
              <option value="">不挂载</option>
              {skills
                .filter((s) => s.stage === stage)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
            </Select>
          </div>
        ))}
      </div>
      <div>
        <Label>知识库(生成时检索注入)</Label>
        {kbs.length === 0 ? (
          <p className="text-xs text-slate-400">
            还没有知识库,去
            <Link href="/knowledge" className="text-indigo-600 hover:underline">
              知识库
            </Link>
            创建并上传资料
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {kbs.map((kb) => (
              <button
                key={kb.id}
                onClick={() => toggleKb(kb.id)}
                className={`rounded-full px-2.5 py-1 text-xs ${mountedKbs.includes(kb.id) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
              >
                {kb.name}({kb.chunk_count}片段)
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ================= 团队协作 ================= */
function TeamCard({ novelId, isOwner }: { novelId: number; isOwner: boolean }) {
  const [members, setMembers] = useState<CollabInfo[]>([]);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("editor");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setMembers(await api.get<CollabInfo[]>(`/novels/${novelId}/collaborators`));
  }, [novelId]);

  useEffect(() => {
    load();
  }, [load]);

  async function invite() {
    setError("");
    setMsg("");
    try {
      await api.post(`/novels/${novelId}/collaborators`, { username, role });
      setUsername("");
      setMsg("已邀请");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "邀请失败");
    }
  }

  async function changeRole(id: number, r: string) {
    await api.put(`/novels/${novelId}/collaborators/${id}`, { role: r });
    load();
  }

  async function removeMember(id: number) {
    if (!confirm("确定移除该协作者?")) return;
    await api.delete(`/novels/${novelId}/collaborators/${id}`);
    load();
  }

  return (
    <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4">
      <h2 className="mb-2 text-sm font-medium">团队协作</h2>
      <p className="mb-2 text-xs text-slate-400">邀请其他注册用户协作本书:editor 可写作改稿,reviewer 负责审核</p>
      <ErrorText>{error}</ErrorText>
      {msg && <p className="mb-2 text-xs text-emerald-600">{msg}</p>}
      {members.length > 0 && (
        <div className="mb-3 space-y-1.5">
          {members.map((m) => (
            <div key={m.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-1.5 text-sm">
              <span>
                {m.nickname || m.username}
                <span className="ml-2 text-xs text-slate-400">@{m.username}</span>
              </span>
              <div className="flex items-center gap-2 text-xs">
                {isOwner ? (
                  <>
                    <Select value={m.role} onChange={(e) => changeRole(m.id, e.target.value)} className="!py-1 text-xs">
                      <option value="editor">editor</option>
                      <option value="reviewer">reviewer</option>
                    </Select>
                    <button className="text-red-500 hover:underline" onClick={() => removeMember(m.id)}>
                      移除
                    </button>
                  </>
                ) : (
                  <Badge>{m.role}</Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {isOwner && (
        <div className="flex gap-2">
          <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="对方用户名" className="max-w-44" />
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="editor">editor</option>
            <option value="reviewer">reviewer</option>
          </Select>
          <Button variant="outline" onClick={invite} disabled={!username.trim()}>
            邀请
          </Button>
        </div>
      )}
    </div>
  );
}

/* ================= 导出 ================= */
function ExportCard({ novelId }: { novelId: number }) {
  return (
    <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4">
      <h2 className="mb-2 text-sm font-medium">导出</h2>
      <div className="flex flex-wrap gap-2">
        <a href={`${getApiBase()}/novels/${novelId}/export?format=txt`} target="_blank" rel="noreferrer">
          <Button variant="outline" size="sm">
            导出 TXT(审核通过章节)
          </Button>
        </a>
        <a href={`${getApiBase()}/novels/${novelId}/export?format=epub`} target="_blank" rel="noreferrer">
          <Button variant="outline" size="sm">
            导出 EPUB
          </Button>
        </a>
      </div>
    </div>
  );
}

/* ================= 编辑信息 ================= */
function EditNovelModal({ novel, onSaved }: { novel: Novel; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ title: "", summary: "", status: "draft", target_words: "" });
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setForm({
        title: novel.title,
        summary: novel.summary || "",
        status: novel.status,
        target_words: novel.target_words ? String(novel.target_words) : "",
      });
      setPlatforms(novel.platforms.map((p) => p.platform));
    }
  }, [open, novel]);

  async function save() {
    setError("");
    try {
      await api.put(`/novels/${novel.id}`, {
        ...form,
        target_words: form.target_words ? parseInt(form.target_words) : null,
        platforms,
      });
      setOpen(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  return (
    <>
      <button className="mb-3 text-xs text-slate-400 hover:text-indigo-600" onClick={() => setOpen(true)}>
        ✎ 编辑书籍信息 / 发布平台
      </button>
      <Modal open={open} title="编辑书籍信息" onClose={() => setOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>书名</Label>
            <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>
          <div>
            <Label>简介</Label>
            <Textarea rows={3} value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>状态</Label>
              <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="w-full">
                <option value="draft">构思中</option>
                <option value="serializing">连载中</option>
                <option value="completed">已完本</option>
              </Select>
            </div>
            <div>
              <Label>目标字数</Label>
              <Input value={form.target_words} onChange={(e) => setForm({ ...form, target_words: e.target.value.replace(/\D/g, "") })} />
            </div>
          </div>
          <div>
            <Label>发布平台</Label>
            <div className="flex flex-wrap gap-2">
              {PLATFORMS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => setPlatforms((prev) => (prev.includes(p.key) ? prev.filter((x) => x !== p.key) : [...prev, p.key]))}
                  className={`rounded-full px-2.5 py-1 text-xs ${platforms.includes(p.key) ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={save} disabled={!form.title.trim()} className="w-full">
            保存
          </Button>
        </div>
      </Modal>
    </>
  );
}
