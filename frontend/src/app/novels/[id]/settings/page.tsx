"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ChatMediaContent } from "@/components/ChatMediaContent";
import { api, getApiBase } from "@/lib/api";
import type { Chapter, Character, ForeshadowInfo, Novel, NovelScene, Relationship, Rule, WorldEntry } from "@/lib/types";
import { Badge, Button, Empty, ErrorText, Input, Label, Modal, Select, Spinner, Textarea } from "@/components/ui";

const WORLD_CATEGORIES: [string, string][] = [
  ["geography", "地理"],
  ["faction", "势力"],
  ["power", "力量体系"],
  ["item", "物品"],
  ["event", "事件"],
  ["other", "其他"],
];

const RELATIONSHIP_TYPES: [string, string][] = [
  ["family", "亲人"],
  ["mentor", "师徒"],
  ["rival", "对手"],
  ["lover", "恋人"],
  ["ally", "盟友"],
  ["enemy", "敌对"],
  ["other", "其他"],
];
const RELATIONSHIP_COLORS: Record<string, string> = {
  family: "#f59e0b",
  mentor: "#10b981",
  rival: "#f97316",
  lover: "#ec4899",
  ally: "#3b82f6",
  enemy: "#ef4444",
  other: "#94a3b8",
};

const ROLE_LABELS: Record<string, string> = {
  protagonist: "主角",
  support: "配角",
  villain: "反派",
  minor: "龙套",
};

type Tab = "world" | "characters" | "visual" | "relationships" | "rules" | "foreshadows";

export default function SettingsPage() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<Tab>("world");
  const tabs: [Tab, string][] = [
    ["world", "世界观"],
    ["characters", "人物"],
    ["visual", "人物场景视觉"],
    ["relationships", "关系图谱"],
    ["foreshadows", "伏笔台账"],
    ["rules", "规则页"],
  ];

  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1 text-sm">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`min-w-[5.5rem] flex-1 rounded-md py-1.5 font-medium transition ${tab === key ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "world" && <WorldTab novelId={id} />}
      {tab === "characters" && <CharactersTab novelId={id} />}
      {tab === "visual" && <VisualTab novelId={id} />}
      {tab === "relationships" && <RelationshipsTab novelId={id} />}
      {tab === "foreshadows" && <ForeshadowsTab novelId={id} />}
      {tab === "rules" && <RulesTab novelId={id} />}
    </div>
  );
}

/* ================= 人物场景视觉(全书画风 + 场景库 + 定妆) ================= */
function VisualTab({ novelId }: { novelId: string }) {
  const [novel, setNovel] = useState<Novel | null>(null);
  const [chars, setChars] = useState<Character[]>([]);
  const [scenes, setScenes] = useState<NovelScene[]>([]);
  const [style, setStyle] = useState("");
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [sceneOpen, setSceneOpen] = useState(false);
  const [editScene, setEditScene] = useState<NovelScene | null>(null);
  const [sceneForm, setSceneForm] = useState({
    title: "",
    description: "",
    atmosphere: "",
    reference_image_urls: [] as string[],
  });
  const [uploading, setUploading] = useState(false);
  const [portraitBusyId, setPortraitBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // 分开请求,避免一个失败拖死整页
      const n = await api.get<Novel>(`/novels/${novelId}`);
      setNovel(n);
      setStyle(n.visual_style_prompt || "");
      try {
        setChars(await api.get<Character[]>(`/novels/${novelId}/characters`));
      } catch (e) {
        setChars([]);
        setError(e instanceof Error ? e.message : "人物列表加载失败");
      }
      try {
        setScenes(await api.get<NovelScene[]>(`/novels/${novelId}/scenes`));
      } catch (e) {
        setScenes([]);
        setError((prev) => prev || (e instanceof Error ? e.message : "场景库加载失败"));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [novelId]);

  async function generatePortrait(characterId: number) {
    setPortraitBusyId(characterId);
    setError("");
    setMsg("");
    try {
      const updated = await api.post<Character>(
        `/novels/${novelId}/characters/${characterId}/generate-portrait`,
        {}
      );
      setChars((prev) => prev.map((c) => (c.id === characterId ? updated : c)));
      setMsg(`「${updated.name}」定妆图已生成`);
      setTimeout(() => setMsg(""), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "定妆生成失败");
    } finally {
      setPortraitBusyId(null);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  async function saveStyle() {
    setError("");
    try {
      await api.put(`/novels/${novelId}`, { visual_style_prompt: style || null });
      setMsg("全书画风已保存");
      setTimeout(() => setMsg(""), 2000);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function uploadRef(onUrl: (url: string) => void) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setUploading(true);
      setError("");
      try {
        const form = new FormData();
        form.append("file", file);
        const res = await api.upload<{ url: string }>(`/novels/${novelId}/visual-upload`, form);
        onUrl(res.url);
      } catch (e) {
        setError(e instanceof Error ? e.message : "上传失败");
      } finally {
        setUploading(false);
      }
    };
    input.click();
  }

  async function saveScene() {
    setError("");
    try {
      const body = {
        title: sceneForm.title.trim(),
        description: sceneForm.description || null,
        atmosphere: sceneForm.atmosphere || null,
        reference_image_urls: sceneForm.reference_image_urls,
        sort: editScene?.sort ?? scenes.length,
      };
      if (editScene) await api.put(`/novels/${novelId}/scenes/${editScene.id}`, body);
      else await api.post(`/novels/${novelId}/scenes`, body);
      setSceneOpen(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存场景失败");
    }
  }

  async function removeScene(sid: number) {
    if (!confirm("删除该场景?")) return;
    await api.delete(`/novels/${novelId}/scenes/${sid}`);
    load();
  }

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-violet-100 bg-violet-50/60 px-4 py-3 text-xs leading-5 text-violet-800">
        这里管「整本书长什么样」:全书画风、人物定妆、场景库。生成设定稿时会写出详尽定妆词并锁定物种/耳色瞳色/服装主色等;分镜规划与生图都会注入同一套定妆,减少每次漂脸。
        {novel ? ` 当前书:《${novel.title}》` : ""}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold">全书画风</h3>
        <p className="mb-2 text-xs text-slate-500">
          保存后立即生效:人物设定稿、分镜生图、章节视频均严格按此处文案绘制。改画风后请重新生成设定稿并「全部重绘」分镜。
        </p>
        <Textarea
          rows={3}
          value={style}
          onChange={(e) => setStyle(e.target.value)}
          placeholder="如:中国动画风格,二维赛璐璐,清晰轮廓线,冷暖对比光影,禁止照片写实与文字水印…"
        />
        <div className="mt-2 flex items-center gap-3">
          <Button size="sm" onClick={saveStyle}>
            保存画风
          </Button>
          {msg && <span className="text-xs text-emerald-600">{msg}</span>}
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">人物定妆(摘要)</h3>
          <span className="text-xs text-slate-400">生成详尽四分区设定稿并刷新定妆词;改人设后请重新生成,再去大纲重规划分镜</span>
        </div>
        {chars.length === 0 ? (
          <Empty text="还没有人物" />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {chars.map((c) => {
              const cover = (c.reference_image_urls || [])[0];
              const desc = c.visual_prompt || c.appearance || c.personality || c.notes || "";
              return (
                <div key={c.id} className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 font-medium text-slate-800">{c.name}</div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={portraitBusyId === c.id}
                      onClick={() => generatePortrait(c.id)}
                    >
                      {portraitBusyId === c.id ? "生成中…" : "生成设定稿"}
                    </Button>
                  </div>
                  <p className="mt-1 text-slate-500 line-clamp-2">
                    {desc || "未填视觉设定(将尽量用性格/备注 + 全书画风生成)"}
                  </p>
                  {cover ? (
                    <div className="mt-2">
                      <ChatMediaContent content={`![${c.name}定妆](${cover})`} />
                    </div>
                  ) : (
                    <p className="mt-1 text-[11px] text-slate-400">尚未生成定妆图</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">场景库</h3>
          <Button
            size="sm"
            onClick={() => {
              setEditScene(null);
              setSceneForm({ title: "", description: "", atmosphere: "", reference_image_urls: [] });
              setSceneOpen(true);
            }}
          >
            + 新场景
          </Button>
        </div>
        {scenes.length === 0 ? (
          <Empty text="还没有场景,如:旧书店、城西码头" />
        ) : (
          <div className="space-y-2">
            {scenes.map((s) => (
              <div key={s.id} className="rounded-lg border border-slate-100 px-3 py-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{s.title}</div>
                    {s.atmosphere && <p className="text-xs text-slate-400">氛围:{s.atmosphere}</p>}
                    {s.description && <p className="mt-1 text-xs text-slate-600">{s.description}</p>}
                  </div>
                  <div className="flex shrink-0 gap-2 text-xs">
                    <button
                      className="text-indigo-600 hover:underline"
                      onClick={() => {
                        setEditScene(s);
                        setSceneForm({
                          title: s.title,
                          description: s.description || "",
                          atmosphere: s.atmosphere || "",
                          reference_image_urls: s.reference_image_urls || [],
                        });
                        setSceneOpen(true);
                      }}
                    >
                      编辑
                    </button>
                    <button className="text-red-500 hover:underline" onClick={() => removeScene(s.id)}>
                      删除
                    </button>
                  </div>
                </div>
                {(s.reference_image_urls || []).length > 0 && (
                  <p className="mt-1 text-[11px] text-indigo-500">
                    参考图 {(s.reference_image_urls || []).length} 张
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <ErrorText>{error}</ErrorText>
      {!loading && (
        <button type="button" className="text-xs text-indigo-600 hover:underline" onClick={() => void load()}>
          重新加载
        </button>
      )}
      <Modal open={sceneOpen} title={editScene ? "编辑场景" : "新建场景"} onClose={() => setSceneOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>名称 *</Label>
            <Input
              value={sceneForm.title}
              onChange={(e) => setSceneForm({ ...sceneForm, title: e.target.value })}
              placeholder="如:旧书店"
            />
          </div>
          <div>
            <Label>氛围</Label>
            <Input
              value={sceneForm.atmosphere}
              onChange={(e) => setSceneForm({ ...sceneForm, atmosphere: e.target.value })}
              placeholder="如:潮湿、昏黄、雨夜"
            />
          </div>
          <div>
            <Label>描述</Label>
            <Textarea
              rows={3}
              value={sceneForm.description}
              onChange={(e) => setSceneForm({ ...sceneForm, description: e.target.value })}
            />
          </div>
          <div>
            <Label>参考图</Label>
            <div className="mt-1 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={uploading}
                onClick={() =>
                  uploadRef((url) =>
                    setSceneForm((f) => ({ ...f, reference_image_urls: [...f.reference_image_urls, url] }))
                  )
                }
              >
                {uploading ? "上传中…" : "上传参考图"}
              </Button>
              {sceneForm.reference_image_urls.length > 0 && (
                <button
                  type="button"
                  className="text-xs text-red-500 hover:underline"
                  onClick={() => setSceneForm((f) => ({ ...f, reference_image_urls: [] }))}
                >
                  清空参考图
                </button>
              )}
            </div>
            {sceneForm.reference_image_urls.length > 0 && (
              <p className="mt-1 text-[11px] text-slate-400">
                已选 {sceneForm.reference_image_urls.length} 张(保存后生效)
              </p>
            )}
          </div>
          <Button onClick={saveScene} disabled={!sceneForm.title.trim()} className="w-full">
            保存
          </Button>
        </div>
      </Modal>
    </div>
  );
}

/* ================= 伏笔台账 ================= */
const FORESHADOW_STATUS: Record<string, { label: string; color: string }> = {
  planted: { label: "埋设中", color: "amber" },
  recalled: { label: "已回收", color: "green" },
  overdue: { label: "超期未收", color: "red" },
};

function ForeshadowsTab({ novelId }: { novelId: string }) {
  const [items, setItems] = useState<ForeshadowInfo[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<ForeshadowInfo | null>(null);
  const [form, setForm] = useState({ description: "", plant_chapter_id: "", expect_recall_chapter_id: "", status: "planted" });
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [f, c] = await Promise.all([
      api.get<ForeshadowInfo[]>(`/novels/${novelId}/foreshadows`),
      api.get<Chapter[]>(`/novels/${novelId}/chapters`),
    ]);
    setItems(f);
    setChapters(c);
    setLoading(false);
  }, [novelId]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    setError("");
    try {
      const body = {
        description: form.description,
        plant_chapter_id: form.plant_chapter_id ? +form.plant_chapter_id : null,
        expect_recall_chapter_id: form.expect_recall_chapter_id ? +form.expect_recall_chapter_id : null,
        status: form.status,
      };
      if (edit) await api.put(`/novels/${novelId}/foreshadows/${edit.id}`, body);
      else await api.post(`/novels/${novelId}/foreshadows`, body);
      setOpen(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function remove(id: number) {
    if (!confirm("确定删除该伏笔?")) return;
    await api.delete(`/novels/${novelId}/foreshadows/${id}`);
    load();
  }

  if (loading) return <Spinner />;

  const chapterTitle = (cid: number | null) => chapters.find((c) => c.id === cid)?.title || "—";

  return (
    <div>
      <div className="mb-3 flex justify-end gap-2">
        <AiImportButton novelId={novelId} kind="foreshadows" onDone={load} />
        <Button
          size="sm"
          onClick={() => {
            setEdit(null);
            setForm({ description: "", plant_chapter_id: "", expect_recall_chapter_id: "", status: "planted" });
            setOpen(true);
          }}
        >
          + 登记伏笔
        </Button>
      </div>
      <ErrorText>{error}</ErrorText>
      {items.length === 0 ? (
        <Empty text="还没有登记伏笔" />
      ) : (
        <div className="space-y-2">
          {items.map((f) => (
            <div key={f.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-700">{f.description}</p>
                <p className="mt-0.5 text-xs text-slate-400">
                  埋于:{chapterTitle(f.plant_chapter_id)} · 预计回收:{chapterTitle(f.expect_recall_chapter_id)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge color={FORESHADOW_STATUS[f.status]?.color}>{FORESHADOW_STATUS[f.status]?.label || f.status}</Badge>
                <button
                  className="text-xs text-indigo-600 hover:underline"
                  onClick={() => {
                    setEdit(f);
                    setForm({
                      description: f.description,
                      plant_chapter_id: f.plant_chapter_id ? String(f.plant_chapter_id) : "",
                      expect_recall_chapter_id: f.expect_recall_chapter_id ? String(f.expect_recall_chapter_id) : "",
                      status: f.status,
                    });
                    setOpen(true);
                  }}
                >
                  编辑
                </button>
                <button className="text-xs text-red-500 hover:underline" onClick={() => remove(f.id)}>
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Modal open={open} title={edit ? "编辑伏笔" : "登记伏笔"} onClose={() => setOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>伏笔描述 *</Label>
            <Textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="如:主角捡到的旧剑其实是师祖遗物" />
          </div>
          <div>
            <Label>埋设章节</Label>
            <Select value={form.plant_chapter_id} onChange={(e) => setForm({ ...form, plant_chapter_id: e.target.value })} className="w-full">
              <option value="">未定</option>
              {chapters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>预计回收章节</Label>
            <Select value={form.expect_recall_chapter_id} onChange={(e) => setForm({ ...form, expect_recall_chapter_id: e.target.value })} className="w-full">
              <option value="">未定</option>
              {chapters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>状态</Label>
            <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="w-full">
              {Object.entries(FORESHADOW_STATUS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </Select>
          </div>
          <Button onClick={save} disabled={!form.description.trim()} className="w-full">
            保存
          </Button>
        </div>
      </Modal>
    </div>
  );
}

/* ================= AI 批量导入 ================= */
function AiImportButton({
  novelId,
  kind,
  onDone,
}: {
  novelId: string;
  kind: "world" | "characters" | "relationships" | "foreshadows";
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  const placeholders: Record<string, string> = {
    world: "## 力量体系\n剑道九境:入门、凝气、剑心……\n\n## 势力\n天衡宗:北域第一剑宗……",
    characters:
      "### 林晚舟\n- 定位:主角\n- 性格:坚韧、话少\n- 目标:登顶剑道\n\n### 苏轻眉\n- 定位:同门师姐,机敏",
    relationships:
      "- 林晚舟 ⇄ 苏轻眉:同门师姐弟,亲密 +60\n- 林晚舟 vs 血河老祖:死敌,-90\n- 苏轻眉的师父 = 云鹤真人(师徒)",
    foreshadows:
      "- 第三章出现的旧剑穗,暗示主角身世(未回收)\n- 长老临终那句话,第 20 章已揭晓",
  };

  async function run() {
    setBusy(true);
    setError("");
    setResult("");
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 180_000);
      const token = localStorage.getItem("xiaohuo_token");
      const resp = await fetch(
        `${getApiBase()}/novels/${novelId}/import-${kind}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ markdown: text }),
          signal: controller.signal,
        }
      );
      clearTimeout(timer);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || `导入失败(HTTP ${resp.status})`);
      }
      if ((data.created ?? 0) === 0) {
        setError(
          (data.message || "AI 未能从文本中提取到任何条目") +
            "。建议:文本里写明确字段(如「定位:主角」「关系:师徒」),或换一段更具体的内容重试。"
        );
      } else {
        setResult(data.message || "导入完成");
        onDone();
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setError("AI 解析超时(超过 3 分钟被中止)。请检查该模型服务是否可用,或在个人中心把对应用途换成更快的模型后重试。");
      } else {
        setError(e instanceof Error ? e.message : "导入失败,请稍后重试");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          setOpen(true);
          setResult("");
          setError("");
        }}
      >
        🤖 AI 导入
      </Button>
      <Modal open={open} title="AI 解析 · 批量导入" onClose={() => setOpen(false)} wide>
        <div className="space-y-3">
          <p className="rounded-lg bg-indigo-50 p-2 text-xs text-indigo-700">
            粘贴 markdown 或任意格式的设定文本,AI 自动提取并批量回填到设定库。人物重名自动跳过;关系里出现新人物会自动建档。需要已在个人中心配置 AI 模型(优先使用「复盘分析」用途,未配置时自动尝试其他用途)。
          </p>
          <Textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholders[kind]} />
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={run} disabled={busy || text.trim().length < 10}>
              {busy ? "AI 解析中…" : "开始解析导入"}
            </Button>
            {busy && <span className="text-xs text-slate-400">正在调用 AI 解析并回填,请勿关闭弹框;失败会在这里显示红色错误</span>}
          </div>
          {result && <div className="rounded-lg bg-emerald-50 p-2.5 text-sm text-emerald-700">✓ {result}</div>}
          {error && (
            <div className="rounded-lg border border-red-100 bg-red-50 p-2.5 text-sm text-red-600">
              <span className="font-medium">导入失败:</span>
              {error}
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}

/* ================= 世界观 ================= */
function WorldTab({ novelId }: { novelId: string }) {
  const [entries, setEntries] = useState<WorldEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [cat, setCat] = useState("all");
  const [edit, setEdit] = useState<WorldEntry | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ category: "geography", title: "", content: "" });
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setEntries(await api.get<WorldEntry[]>(`/novels/${novelId}/world`));
    setLoading(false);
  }, [novelId]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    setError("");
    try {
      if (edit) {
        await api.put(`/novels/${novelId}/world/${edit.id}`, form);
      } else {
        await api.post(`/novels/${novelId}/world`, form);
      }
      setOpen(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function remove(id: number) {
    if (!confirm("确定删除该条目?")) return;
    await api.delete(`/novels/${novelId}/world/${id}`);
    load();
  }

  if (loading) return <Spinner />;
  const shown = cat === "all" ? entries : entries.filter((e) => e.category === cat);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setCat("all")}
            className={`rounded-full px-3 py-1 text-xs ${cat === "all" ? "bg-indigo-600 text-white" : "bg-white border border-slate-200 text-slate-600"}`}
          >
            全部
          </button>
          {WORLD_CATEGORIES.map(([k, v]) => (
            <button
              key={k}
              onClick={() => setCat(k)}
              className={`rounded-full px-3 py-1 text-xs ${cat === k ? "bg-indigo-600 text-white" : "bg-white border border-slate-200 text-slate-600"}`}
            >
              {v}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <AiImportButton novelId={novelId} kind="world" onDone={load} />
          <Button
            size="sm"
            onClick={() => {
              setEdit(null);
              setForm({ category: cat === "all" ? "geography" : cat, title: "", content: "" });
              setOpen(true);
            }}
          >
            + 新条目
          </Button>
        </div>
      </div>
      {shown.length === 0 ? (
        <Empty text="暂无世界观条目" />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {shown.map((e) => (
            <div key={e.id} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between">
                <h4 className="font-medium">{e.title}</h4>
                <Badge color="indigo">{WORLD_CATEGORIES.find(([k]) => k === e.category)?.[1] || e.category}</Badge>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{e.content}</p>
              <div className="mt-3 flex gap-2 text-xs">
                <button
                  className="text-indigo-600 hover:underline"
                  onClick={() => {
                    setEdit(e);
                    setForm({ category: e.category, title: e.title, content: e.content || "" });
                    setOpen(true);
                  }}
                >
                  编辑
                </button>
                <button className="text-red-500 hover:underline" onClick={() => remove(e.id)}>
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Modal open={open} title={edit ? "编辑世界观条目" : "新建世界观条目"} onClose={() => setOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>分类</Label>
            <Select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {WORLD_CATEGORIES.map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>标题</Label>
            <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>
          <div>
            <Label>内容</Label>
            <Textarea rows={6} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={save} disabled={!form.title.trim()} className="w-full">
            保存
          </Button>
        </div>
      </Modal>
    </div>
  );
}

/* ================= 人物 ================= */
function CharactersTab({ novelId }: { novelId: string }) {
  const [chars, setChars] = useState<Character[]>([]);
  const [loading, setLoading] = useState(true);
  const [edit, setEdit] = useState<Character | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [portraitBusyId, setPortraitBusyId] = useState<number | null>(null);
  const emptyForm = {
    name: "",
    aliases: "",
    role: "support",
    occupation: "",
    organization: "",
    appearance: "",
    visual_prompt: "",
    reference_image_urls: [] as string[],
    personality: "",
    goal: "",
    notes: "",
  };
  const [form, setForm] = useState(emptyForm);
  const [uploading, setUploading] = useState(false);

  async function generatePortrait(characterId: number) {
    setPortraitBusyId(characterId);
    setError("");
    try {
      const updated = await api.post<Character>(
        `/novels/${novelId}/characters/${characterId}/generate-portrait`,
        {}
      );
      setChars((prev) => prev.map((c) => (c.id === characterId ? updated : c)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "定妆生成失败");
    } finally {
      setPortraitBusyId(null);
    }
  }

  const load = useCallback(async () => {
    setChars(await api.get<Character[]>(`/novels/${novelId}/characters`));
    setLoading(false);
  }, [novelId]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    setError("");
    try {
      if (edit) {
        await api.put(`/novels/${novelId}/characters/${edit.id}`, form);
      } else {
        await api.post(`/novels/${novelId}/characters`, form);
      }
      setOpen(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function remove(id: number) {
    if (!confirm("确定删除该人物及其关系?")) return;
    await api.delete(`/novels/${novelId}/characters/${id}`);
    load();
  }

  if (loading) return <Spinner />;

  return (
    <div>
      <div className="mb-3 flex justify-end gap-2">
        <AiImportButton novelId={novelId} kind="characters" onDone={load} />
        <Button
          size="sm"
          onClick={() => {
            setEdit(null);
            setForm(emptyForm);
            setOpen(true);
          }}
        >
          + 新人物
        </Button>
      </div>
      {chars.length === 0 ? (
        <Empty text="暂无人物,先创建主角吧" />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {chars.map((c) => (
            <div key={c.id} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between">
                <h4 className="font-medium">
                  {c.name}
                  {c.aliases && <span className="ml-2 text-xs text-slate-400">{c.aliases}</span>}
                </h4>
                <Badge color={c.role === "protagonist" ? "indigo" : c.role === "villain" ? "red" : "slate"}>
                  {ROLE_LABELS[c.role] || c.role}
                </Badge>
              </div>
              <dl className="mt-2 space-y-1 text-xs text-slate-600">
                {c.occupation && <div>职业:{c.occupation}{c.organization ? ` · ${c.organization}` : ""}</div>}
                {c.personality && <div>性格:{c.personality}</div>}
                {c.goal && <div>目标:{c.goal}</div>}
                {c.notes && <div>备注:{c.notes}</div>}
              </dl>
              {(c.reference_image_urls || [])[0] && (
                <div className="mt-2">
                  <ChatMediaContent content={`![${c.name}](${(c.reference_image_urls || [])[0]})`} />
                </div>
              )}
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <button
                  className="text-violet-600 hover:underline disabled:opacity-40"
                  disabled={portraitBusyId === c.id}
                  onClick={() => generatePortrait(c.id)}
                >
                  {portraitBusyId === c.id ? "生成设定稿中…" : "生成设定稿"}
                </button>
                <button
                  className="text-indigo-600 hover:underline"
                  onClick={() => {
                    setEdit(c);
                    setForm({
                      name: c.name,
                      aliases: c.aliases || "",
                      role: c.role,
                      occupation: c.occupation || "",
                      organization: c.organization || "",
                      appearance: c.appearance || "",
                      visual_prompt: c.visual_prompt || "",
                      reference_image_urls: c.reference_image_urls || [],
                      personality: c.personality || "",
                      goal: c.goal || "",
                      notes: c.notes || "",
                    });
                    setOpen(true);
                  }}
                >
                  编辑
                </button>
                <button className="text-red-500 hover:underline" onClick={() => remove(c.id)}>
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <ErrorText>{error}</ErrorText>
      <Modal open={open} title={edit ? "编辑人物" : "新建人物"} onClose={() => setOpen(false)} wide>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>姓名 *</Label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <Label>别名</Label>
            <Input value={form.aliases} onChange={(e) => setForm({ ...form, aliases: e.target.value })} />
          </div>
          <div>
            <Label>定位</Label>
            <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {Object.entries(ROLE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>职业</Label>
            <Input value={form.occupation} onChange={(e) => setForm({ ...form, occupation: e.target.value })} />
          </div>
          <div className="col-span-2">
            <Label>所属组织</Label>
            <Input value={form.organization} onChange={(e) => setForm({ ...form, organization: e.target.value })} />
          </div>
          <div className="col-span-2">
            <Label>外貌</Label>
            <Textarea rows={2} value={form.appearance} onChange={(e) => setForm({ ...form, appearance: e.target.value })} />
          </div>
          <div className="col-span-2">
            <Label>视觉定妆提示词(生图必用,保持跨章一致)</Label>
            <Textarea
              rows={2}
              value={form.visual_prompt}
              onChange={(e) => setForm({ ...form, visual_prompt: e.target.value })}
              placeholder="如:二十出头东亚男性,短碎发,深灰风衣,右眉细疤,冷白皮…"
            />
          </div>
          <div className="col-span-2">
            <Label>定妆参考图</Label>
            <div className="mt-1 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                type="button"
                disabled={uploading}
                onClick={() => {
                  const input = document.createElement("input");
                  input.type = "file";
                  input.accept = "image/*";
                  input.onchange = async () => {
                    const file = input.files?.[0];
                    if (!file) return;
                    setUploading(true);
                    try {
                      const fd = new FormData();
                      fd.append("file", file);
                      const res = await api.upload<{ url: string }>(`/novels/${novelId}/visual-upload`, fd);
                      setForm((f) => ({
                        ...f,
                        reference_image_urls: [...f.reference_image_urls, res.url],
                      }));
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "上传失败");
                    } finally {
                      setUploading(false);
                    }
                  };
                  input.click();
                }}
              >
                {uploading ? "上传中…" : "上传定妆图"}
              </Button>
              {form.reference_image_urls.length > 0 && (
                <button
                  type="button"
                  className="text-xs text-red-500 hover:underline"
                  onClick={() => setForm({ ...form, reference_image_urls: [] })}
                >
                  清空参考图
                </button>
              )}
            </div>
            {form.reference_image_urls.length > 0 && (
              <div className="mt-2">
                <ChatMediaContent
                  content={form.reference_image_urls.map((u, i) => `![定妆${i + 1}](${u})`).join("\n")}
                />
              </div>
            )}
          </div>
          <div className="col-span-2">
            <Label>性格</Label>
            <Textarea rows={2} value={form.personality} onChange={(e) => setForm({ ...form, personality: e.target.value })} />
          </div>
          <div className="col-span-2">
            <Label>目标 / 动机</Label>
            <Textarea rows={2} value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} />
          </div>
          <div className="col-span-2">
            <Label>备注</Label>
            <Textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
        </div>
        <ErrorText>{error}</ErrorText>
        <Button onClick={save} disabled={!form.name.trim()} className="mt-3 w-full">
          保存
        </Button>
      </Modal>
    </div>
  );
}

/* ================= 关系图谱 ================= */
function RelationshipsTab({ novelId }: { novelId: string }) {
  const [chars, setChars] = useState<Character[]>([]);
  const [rels, setRels] = useState<Relationship[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ from_character_id: 0, to_character_id: 0, type: "ally", intimacy: 50, note: "" });

  const load = useCallback(async () => {
    const [c, r] = await Promise.all([
      api.get<Character[]>(`/novels/${novelId}/characters`),
      api.get<Relationship[]>(`/novels/${novelId}/relationships`),
    ]);
    setChars(c);
    setRels(r);
    setLoading(false);
  }, [novelId]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    setError("");
    try {
      await api.post(`/novels/${novelId}/relationships`, form);
      setOpen(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function remove(id: number) {
    if (!confirm("确定删除该关系?")) return;
    await api.delete(`/novels/${novelId}/relationships/${id}`);
    load();
  }

  // 图谱布局:人物均匀分布在圆周上
  const layout = useMemo(() => {
    const n = chars.length;
    const W = 640;
    const H = 420;
    const cx = W / 2;
    const cy = H / 2;
    const R = Math.min(W, H) / 2 - 70;
    const pos = new Map<number, { x: number; y: number }>();
    chars.forEach((c, i) => {
      const angle = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
      pos.set(c.id, { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) });
    });
    return { W, H, pos };
  }, [chars]);

  if (loading) return <Spinner />;

  return (
    <div>
      <div className="mb-3 flex justify-end gap-2">
        <AiImportButton novelId={novelId} kind="relationships" onDone={load} />
        <Button
          size="sm"
          onClick={() => {
            setForm({ from_character_id: chars[0]?.id || 0, to_character_id: chars[1]?.id || 0, type: "ally", intimacy: 50, note: "" });
            setOpen(true);
          }}
          disabled={chars.length < 2}
        >
          + 新关系
        </Button>
      </div>

      {chars.length < 2 ? (
        <Empty text="至少需要两个人物才能建立关系图谱" />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-2">
          <svg width={layout.W} height={layout.H} className="mx-auto">
            {rels.map((r) => {
              const a = layout.pos.get(r.from_character_id);
              const b = layout.pos.get(r.to_character_id);
              if (!a || !b) return null;
              const width = 1 + (Math.abs(r.intimacy) / 100) * 3;
              return (
                <g key={r.id}>
                  <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={RELATIONSHIP_COLORS[r.type] || "#94a3b8"} strokeWidth={width} strokeOpacity={0.7} />
                  <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} textAnchor="middle" fontSize={10} fill="#64748b">
                    {RELATIONSHIP_TYPES.find(([k]) => k === r.type)?.[1] || r.type}
                    {r.intimacy !== 0 ? ` (${r.intimacy > 0 ? "+" : ""}${r.intimacy})` : ""}
                  </text>
                </g>
              );
            })}
            {chars.map((c) => {
              const p = layout.pos.get(c.id);
              if (!p) return null;
              const isProtagonist = c.role === "protagonist";
              return (
                <g key={c.id}>
                  <circle cx={p.x} cy={p.y} r={isProtagonist ? 26 : 20} fill={isProtagonist ? "#4f46e5" : "#e0e7ff"} stroke="#4f46e5" strokeWidth={1.5} />
                  <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize={11} fill={isProtagonist ? "#fff" : "#4338ca"} fontWeight={500}>
                    {c.name.slice(0, 4)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      {rels.length > 0 && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
          <h4 className="mb-2 text-sm font-medium">关系列表</h4>
          <div className="space-y-2">
            {rels.map((r) => {
              const from = chars.find((c) => c.id === r.from_character_id);
              const to = chars.find((c) => c.id === r.to_character_id);
              return (
                <div key={r.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <span>
                    <strong>{from?.name}</strong> —{" "}
                    <Badge color="slate">
                      {RELATIONSHIP_TYPES.find(([k]) => k === r.type)?.[1] || r.type}({r.intimacy > 0 ? "+" : ""}
                      {r.intimacy})
                    </Badge>{" "}
                    — <strong>{to?.name}</strong>
                    {r.note && <span className="ml-2 text-xs text-slate-400">{r.note}</span>}
                  </span>
                  <button className="text-xs text-red-500 hover:underline" onClick={() => remove(r.id)}>
                    删除
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <Modal open={open} title="新建关系" onClose={() => setOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>人物 A</Label>
            <Select value={form.from_character_id} onChange={(e) => setForm({ ...form, from_character_id: +e.target.value })} className="w-full">
              {chars.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>关系</Label>
            <Select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className="w-full">
              {RELATIONSHIP_TYPES.map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>人物 B</Label>
            <Select value={form.to_character_id} onChange={(e) => setForm({ ...form, to_character_id: +e.target.value })} className="w-full">
              {chars.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>亲密度({form.intimacy > 0 ? "+" : ""}{form.intimacy},范围 -100 ~ 100)</Label>
            <input
              type="range"
              min={-100}
              max={100}
              value={form.intimacy}
              onChange={(e) => setForm({ ...form, intimacy: +e.target.value })}
              className="w-full accent-indigo-600"
            />
          </div>
          <div>
            <Label>备注</Label>
            <Input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="如:同门师兄弟,曾有恩怨" />
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={save} className="w-full">
            保存
          </Button>
        </div>
      </Modal>
    </div>
  );
}

/* ================= 规则页 ================= */
function RulesTab({ novelId }: { novelId: string }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [content, setContent] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setRules(await api.get<Rule[]>(`/novels/${novelId}/rules`));
    setLoading(false);
  }, [novelId]);

  useEffect(() => {
    load();
  }, [load]);

  async function add() {
    setError("");
    try {
      await api.post(`/novels/${novelId}/rules`, { content, priority: 0, enabled: true });
      setContent("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function toggle(rule: Rule) {
    await api.put(`/novels/${novelId}/rules/${rule.id}`, { content: rule.content, priority: rule.priority, enabled: !rule.enabled });
    load();
  }

  async function remove(id: number) {
    if (!confirm("确定删除该规则?")) return;
    await api.delete(`/novels/${novelId}/rules/${id}`);
    load();
  }

  if (loading) return <Spinner />;

  return (
    <div>
      <div className="mb-4 rounded-xl border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-700">
        规则页是这本小说的「宪法」:文风约束、写作禁忌、字数要求等。每次 AI 生成章节时,这里的启用规则都会强制注入提示词。
      </div>
      <div className="mb-4 flex gap-2">
        <Input value={content} onChange={(e) => setContent(e.target.value)} placeholder="例如:主角前期不得使用禁咒;每章结尾必须留钩子" onKeyDown={(e) => e.key === "Enter" && content.trim() && add()} />
        <Button onClick={add} disabled={!content.trim()}>
          添加规则
        </Button>
      </div>
      <ErrorText>{error}</ErrorText>
      {rules.length === 0 ? (
        <Empty text="还没有规则" />
      ) : (
        <div className="space-y-2">
          {rules.map((r) => (
            <div key={r.id} className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => toggle(r)}
                  className={`h-4 w-8 rounded-full transition ${r.enabled ? "bg-indigo-600" : "bg-slate-300"}`}
                  title={r.enabled ? "点击停用" : "点击启用"}
                >
                  <span className={`block h-3 w-3 translate-y-0.5 rounded-full bg-white transition ${r.enabled ? "translate-x-4" : "translate-x-0.5"}`} />
                </button>
                <span className={`text-sm ${r.enabled ? "text-slate-700" : "text-slate-400 line-through"}`}>{r.content}</span>
              </div>
              <button className="text-xs text-red-500 hover:underline" onClick={() => remove(r.id)}>
                删除
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
