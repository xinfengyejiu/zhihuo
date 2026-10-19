"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Provider, RouteConfig } from "@/lib/types";
import { Badge, Button, Empty, ErrorText, Input, Label, Modal, Select, Spinner } from "@/components/ui";

const PURPOSE_LABELS: Record<string, string> = {
  chat: "自由对话",
  outline: "大纲生成",
  chapter: "章节正文",
  polish: "润色",
  analysis: "复盘分析",
  summary: "前情摘要",
  embedding: "向量记忆",
  topic_expand: "选题扩写",
  visual: "分镜/生图",
  script: "漫剧剧本",
};

function purposeLabel(purpose: string): string {
  if (PURPOSE_LABELS[purpose]) return PURPOSE_LABELS[purpose];
  if (purpose.includes(".")) {
    const [prod, short] = purpose.split(".", 2);
    const shortLabel = PURPOSE_LABELS[short] || short;
    const prodLabel =
      prod === "novel" ? "小说" : prod === "media" ? "自媒体" : prod === "drama" ? "漫剧" : prod === "shared" ? "共享" : prod;
    return `${prodLabel} · ${shortLabel}`;
  }
  return purpose;
}

interface RouteMetaProduct {
  key: string;
  name: string;
  purposes: { key: string; short: string; label: string }[];
}

interface ProviderModel {
  id: number;
  model_id: string;
  display_name: string | null;
  enabled: boolean;
  modality?: string;
}

function modelOptionLabel(m: ProviderModel): string {
  const tag = m.modality === "image" ? "[图片] " : m.modality === "video" ? "[视频] " : "";
  return `${tag}${m.display_name || m.model_id}`;
}

interface UsageSummary {
  total_prompt_tokens: number;
  total_completion_tokens: number;
  by_product?: Record<string, { prompt_tokens: number; completion_tokens: number }>;
  error_failed_count?: number;
  error_degraded_count?: number;
  points: {
    date: string;
    purpose: string;
    model_name: string | null;
    product_key?: string;
    prompt_tokens: number;
    completion_tokens: number;
  }[];
}

interface CallErrorItem {
  id: number;
  created_at: string | null;
  purpose: string;
  product_key: string;
  provider_name: string | null;
  model_name: string | null;
  status: "failed" | "degraded" | string;
  error: string;
}
export default function ProfilePage() {
  const { user, refresh } = useAuth();
  const [tab, setTab] = useState<"profile" | "ai" | "usage">("profile");

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-4 text-xl font-bold">个人中心</h1>
      <div className="mb-4 flex gap-1 rounded-lg bg-slate-100 p-1 text-sm">
        {([
          ["profile", "资料与安全"],
          ["ai", "AI 供应商配置"],
          ["usage", "用量看板"],
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
      {tab === "profile" && <ProfileTab refresh={refresh} user={user} />}
      {tab === "ai" && <AiTab />}
      {tab === "usage" && <UsageTab />}
    </div>
  );
}

/* ================= 资料与安全 ================= */
function ProfileTab({ user, refresh }: { user: ReturnType<typeof useAuth>["user"]; refresh: () => Promise<void> }) {
  const [nickname, setNickname] = useState("");
  const [email, setEmail] = useState("");
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (user) {
      setNickname(user.nickname || "");
      setEmail(user.email || "");
    }
  }, [user]);

  async function saveProfile() {
    setError("");
    setMsg("");
    try {
      await api.put("/profile", { nickname, email: email || null });
      await refresh();
      setMsg("资料已保存");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function savePassword() {
    setError("");
    setMsg("");
    try {
      await api.put("/profile/password", { old_password: oldPwd, new_password: newPwd });
      setOldPwd("");
      setNewPwd("");
      setMsg("密码已修改");
    } catch (e) {
      setError(e instanceof Error ? e.message : "修改失败");
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-sm font-semibold">基本资料</h3>
        <div className="grid max-w-md gap-3">
          <div>
            <Label>用户名</Label>
            <Input value={user?.username || ""} disabled />
          </div>
          <div>
            <Label>昵称</Label>
            <Input value={nickname} onChange={(e) => setNickname(e.target.value)} />
          </div>
          <div>
            <Label>邮箱</Label>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <Button onClick={saveProfile} className="w-fit">保存资料</Button>
        </div>
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-sm font-semibold">修改密码</h3>
        <div className="grid max-w-md gap-3">
          <div>
            <Label>旧密码</Label>
            <Input type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)} />
          </div>
          <div>
            <Label>新密码(至少 6 位)</Label>
            <Input type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} />
          </div>
          <Button onClick={savePassword} disabled={!oldPwd || newPwd.length < 6} className="w-fit">修改密码</Button>
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      {msg && <p className="text-xs text-emerald-600">{msg}</p>}
    </div>
  );
}

/* ================= AI 供应商 ================= */
function AiTab() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [routes, setRoutes] = useState<RouteConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [edit, setEdit] = useState<Provider | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState({ name: "", base_url: "", api_key: "", remark: "" });
  const [manualModel, setManualModel] = useState("");
  const [testResult, setTestResult] = useState<
    Record<
      number,
      {
        ok: boolean;
        error: string | null;
        models: string[];
        passed?: number;
        failed?: number;
        results?: { model_id: string; modality: string; ok: boolean; error: string | null; detail?: string | null }[];
      }
    >
  >({});
  const [modelList, setModelList] = useState<Record<number, ProviderModel[]>>({});
  const [routeDraft, setRouteDraft] = useState<Record<string, RouteConfig>>({});
  const [routeMeta, setRouteMeta] = useState<RouteMetaProduct[]>([]);
  const [savedRows, setSavedRows] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const loadModels = useCallback(async (providerId: number) => {
    try {
      const models = await api.get<ProviderModel[]>(`/providers/${providerId}/models`);
      setModelList((prev) => ({ ...prev, [providerId]: models }));
    } catch {
      /* 单个供应商模型加载失败不阻塞页面 */
    }
  }, []);

  const load = useCallback(async () => {
    const [p, r, meta] = await Promise.all([
      api.get<Provider[]>("/providers"),
      api.get<RouteConfig[]>("/routes"),
      api.get<{ products: RouteMetaProduct[] }>("/routes/meta").catch(() => ({ products: [] })),
    ]);
    setProviders(p);
    setRoutes(r);
    setRouteMeta(meta.products || []);
    setRouteDraft(Object.fromEntries(r.map((x) => [x.purpose, { ...x }])));
    setLoading(false);
    p.filter((prov) => prov.enabled).forEach((prov) => loadModels(prov.id));
  }, [loadModels]);

  useEffect(() => {
    load();
  }, [load]);

  async function saveProvider() {
    setError("");
    try {
      let providerId = edit?.id;
      if (edit) {
        await api.put(`/providers/${edit.id}`, form.api_key ? form : { ...form, api_key: undefined });
      } else {
        const created = await api.post<Provider>("/providers", form);
        providerId = created.id;
      }
      const mid = manualModel.trim();
      if (providerId && mid) {
        await api.put(`/providers/${providerId}/models`, { model_id: mid, enabled: true });
      }
      setFormOpen(false);
      setEdit(null);
      setForm({ name: "", base_url: "", api_key: "", remark: "" });
      setManualModel("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function addManualModel(providerId: number, modelId: string) {
    const mid = modelId.trim();
    if (!mid) return;
    setError("");
    try {
      await api.put(`/providers/${providerId}/models`, { model_id: mid, enabled: true });
      setMsg(`已添加模型 ${mid}`);
      setManualModel("");
      await loadModels(providerId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "添加模型失败");
    }
  }

  async function removeProvider(id: number) {
    if (!confirm("确定删除该供应商?关联的用途路由会被清空")) return;
    await api.delete(`/providers/${id}`);
    load();
  }

  async function testProvider(id: number) {
    setTestResult((prev) => ({
      ...prev,
      [id]: { ok: false, error: "正在测试已入库模型…", models: [], results: [] },
    }));
    try {
      const res = await api.post<{
        ok: boolean;
        error: string | null;
        models: string[];
        passed?: number;
        failed?: number;
        results?: { model_id: string; modality: string; ok: boolean; error: string | null; detail?: string | null }[];
      }>(`/providers/${id}/test`);
      setTestResult((prev) => ({ ...prev, [id]: res }));
      const models = await api.get<ProviderModel[]>(`/providers/${id}/models`);
      setModelList((prev) => ({ ...prev, [id]: models }));
    } catch (e) {
      setTestResult((prev) => ({
        ...prev,
        [id]: { ok: false, error: e instanceof Error ? e.message : "测试失败", models: [], results: [] },
      }));
    }
  }

  async function syncModels(id: number) {
    setError("");
    try {
      const res = await api.post<{ count: number }>(`/providers/${id}/sync-models`);
      setMsg(`已同步 ${res.count} 个模型`);
      const models = await api.get<ProviderModel[]>(`/providers/${id}/models`);
      setModelList((prev) => ({ ...prev, [id]: models }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "同步失败");
    }
  }

  async function saveRoute(purpose: string) {
    setError("");
    const draft = routeDraft[purpose];
    if (!draft) return;
    const modelId = draft.model_id?.trim() || null;
    const payload = { ...draft, model_id: modelId };
    try {
      // 手输模型名时自动入库,方便下次下拉/建议出现
      if (draft.provider_id && modelId) {
        const known = (modelList[draft.provider_id] || []).some((m) => m.model_id === modelId);
        if (!known) {
          await api.put(`/providers/${draft.provider_id}/models`, { model_id: modelId, enabled: true }).catch(() => null);
        }
      }
      await api.put(`/routes/${purpose}`, payload);
      setRouteDraft((prev) => ({ ...prev, [purpose]: payload }));
      setSavedRows((prev) => [...prev, purpose]);
      setTimeout(() => setSavedRows((prev) => prev.filter((x) => x !== purpose)), 2500);
      setMsg(`「${purposeLabel(purpose)}」路由已保存`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  function updateDraft(purpose: string, patch: Partial<RouteConfig>) {
    setRouteDraft((prev) => ({ ...prev, [purpose]: { ...prev[purpose], ...patch } }));
  }

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-700">
        支持任意 OpenAI 兼容接口(GitCC / DeepSeek / 通义 / Kimi / Ollama 等)。填好 base_url 和 api_key 后点「测试」,成功后「同步模型」,再在下方为每个用途指定模型。
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">供应商</h3>
          <Button
            size="sm"
            onClick={() => {
              setEdit(null);
              setForm({ name: "", base_url: "", api_key: "", remark: "" });
              setManualModel("");
              setFormOpen(true);
            }}
          >
            + 添加供应商
          </Button>
        </div>
        {providers.length === 0 ? (
          <Empty text="还没有配置供应商" />
        ) : (
          <div className="space-y-3">
            {providers.map((p) => (
              <div key={p.id} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <strong className="text-sm">{p.name}</strong>
                      <Badge color={p.enabled ? "green" : "slate"}>{p.enabled ? "启用" : "停用"}</Badge>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-slate-400">{p.base_url} · Key:{p.api_key_masked}</p>
                    {testResult[p.id] && (
                      <div className="mt-1 space-y-1">
                        <p className={`text-xs ${testResult[p.id].ok ? "text-emerald-600" : "text-red-500"}`}>
                          {testResult[p.id].error === "正在测试已入库模型…"
                            ? testResult[p.id].error
                            : testResult[p.id].ok
                              ? `已入库模型测试通过 ${testResult[p.id].passed ?? testResult[p.id].models.length}/${(testResult[p.id].results?.length ?? testResult[p.id].models.length) || "?"}`
                              : `测试未全部通过:${testResult[p.id].error}`}
                        </p>
                        {testResult[p.id].results && testResult[p.id].results!.length > 0 && (
                          <ul className="max-h-28 space-y-0.5 overflow-y-auto text-[11px]">
                            {testResult[p.id].results!.map((r) => (
                              <li key={r.model_id} className={r.ok ? "text-emerald-700" : "text-red-600"}>
                                <span className="font-mono">{r.model_id}</span>
                                <span className="text-slate-400"> · {r.modality}</span>
                                {r.ok
                                  ? ` · ✓${r.detail ? ` ${r.detail}` : ""}`
                                  : ` · ✗ ${r.error || "失败"}`}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                    {modelList[p.id] && modelList[p.id].length > 0 && (
                      <p className="mt-0.5 text-xs text-slate-400">
                        已入库模型:{modelList[p.id].slice(0, 5).map((m) => m.model_id).join(", ")}
                        {modelList[p.id].length > 5 ? ` 等 ${modelList[p.id].length} 个` : ""}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    <Button size="sm" variant="outline" onClick={() => testProvider(p.id)}>测试</Button>
                    <Button size="sm" variant="outline" onClick={() => syncModels(p.id)}>同步模型</Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setEdit(p);
                        setForm({ name: p.name, base_url: p.base_url, api_key: "", remark: p.remark || "" });
                        setManualModel("");
                        loadModels(p.id);
                        setFormOpen(true);
                      }}
                    >
                      编辑
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => removeProvider(p.id)}>删除</Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold">用途路由（按产品）</h3>
            <p className="mt-0.5 text-xs text-slate-400">
              用途键为 product.purpose（如 novel.chapter / media.chat）。未配置时会回落旧短名或其它产品同名用途。
            </p>
          </div>
          {msg && <span className="text-xs text-emerald-600">{msg}</span>}
        </div>
        <ErrorText>{error}</ErrorText>
        {(routeMeta.length
          ? routeMeta
          : [{ key: "all", name: "全部", purposes: routes.map((r) => ({ key: r.purpose, short: r.purpose, label: purposeLabel(r.purpose) })) }]
        ).map((group) => {
          if (!group.purposes.length) return null;
          return (
            <div key={group.key} className="mb-5">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{group.name}</h4>
              <div className="space-y-3">
                {group.purposes.map((meta) => {
                  const r = routes.find((x) => x.purpose === meta.key) || {
                    purpose: meta.key,
                    provider_id: null,
                    model_id: null,
                    temperature: 0.8,
                    max_tokens: 4096,
                    fallback_provider_id: null,
                    fallback_model_id: null,
                  };
                  const draft = routeDraft[meta.key] || r;
                  const providerModels = draft.provider_id ? modelList[draft.provider_id] : undefined;
                  const enabledModels = (providerModels || []).filter((m) => m.enabled);
                  const modelListId = `route-models-${meta.key}`;
                  return (
                    <div key={meta.key} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                      <div className="flex flex-wrap items-end gap-2">
                        <div className="w-28 shrink-0 self-center text-sm font-medium">{meta.label}</div>
                        <div className="w-36">
                          <Label>供应商</Label>
                          <Select
                            value={draft.provider_id ?? ""}
                            onChange={(e) => {
                              const pid = e.target.value ? +e.target.value : null;
                              updateDraft(meta.key, { provider_id: pid, model_id: null });
                              if (pid) loadModels(pid);
                            }}
                            className="w-full"
                          >
                            <option value="">未选择</option>
                            {providers.filter((p) => p.enabled).map((p) => (
                              <option key={p.id} value={p.id}>{p.name}</option>
                            ))}
                          </Select>
                        </div>
                        <div className="min-w-48 flex-1">
                          <Label>模型（可手输）</Label>
                          <Input
                            value={draft.model_id ?? ""}
                            onChange={(e) => updateDraft(meta.key, { model_id: e.target.value || null })}
                            onBlur={(e) => updateDraft(meta.key, { model_id: e.target.value.trim() || null })}
                            placeholder="手输模型名，如 embedding-3 / glm-4"
                            list={enabledModels.length ? modelListId : undefined}
                            className="w-full font-mono text-xs"
                            autoComplete="off"
                          />
                          {enabledModels.length > 0 && (
                            <datalist id={modelListId}>
                              {enabledModels.map((m) => (
                                <option key={m.id} value={m.model_id}>{modelOptionLabel(m)}</option>
                              ))}
                            </datalist>
                          )}
                        </div>
                        <Button size="sm" variant={savedRows.includes(meta.key) ? "outline" : "primary"} onClick={() => saveRoute(meta.key)}>
                          {savedRows.includes(meta.key) ? "✓ 已保存" : "保存"}
                        </Button>
                      </div>
                      <div className="mt-2 flex flex-wrap items-end gap-2 pl-[7.5rem]">
                        <div className="w-20">
                          <Label>温度</Label>
                          <Input value={draft.temperature} onChange={(e) => updateDraft(meta.key, { temperature: parseFloat(e.target.value) || 0 })} />
                        </div>
                        <div className="w-24">
                          <Label>max_tokens</Label>
                          <Input value={draft.max_tokens} onChange={(e) => updateDraft(meta.key, { max_tokens: parseInt(e.target.value) || 4096 })} />
                        </div>
                        <div className="min-w-48 flex-1">
                          <Label>备用模型</Label>
                          <Select
                            value={draft.fallback_provider_id ? `${draft.fallback_provider_id}:${draft.fallback_model_id}` : ""}
                            onChange={(e) => {
                              const v = e.target.value;
                              if (!v) updateDraft(meta.key, { fallback_provider_id: null, fallback_model_id: null });
                              else {
                                const [pid, ...rest] = v.split(":");
                                updateDraft(meta.key, { fallback_provider_id: +pid, fallback_model_id: rest.join(":") });
                              }
                            }}
                            className="w-full"
                          >
                            <option value="">无</option>
                            {providers.filter((p) => p.enabled).flatMap((p) =>
                              (modelList[p.id] || []).filter((m) => m.enabled).map((m) => (
                                <option key={`${p.id}:${m.model_id}`} value={`${p.id}:${m.model_id}`}>
                                  {p.name} / {modelOptionLabel(m)}
                                </option>
                              ))
                            )}
                          </Select>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <ErrorText>{error}</ErrorText>
      {msg && <p className="text-xs text-emerald-600">{msg}</p>}

      <Modal open={formOpen} title={edit ? "编辑供应商" : "添加供应商"} onClose={() => { setFormOpen(false); setManualModel(""); }}>
        <div className="space-y-3">
          <div>
            <Label>名称 *</Label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如:GitCC" />
          </div>
          <div>
            <Label>Base URL *</Label>
            <Input
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              placeholder="按供应商文档原样填写,系统不会自动加 /v1"
            />
            <p className="mt-1 text-[11px] text-slate-400">
              原样使用你填的地址。若填完整接口(如 …/videos/generations)则直接请求该地址;若填根地址(如 …/v4)会再拼对应路径。
            </p>
          </div>
          <div>
            <Label>{edit ? "API Key(留空则不修改)" : "API Key *"}</Label>
            <Input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="sk-…" />
          </div>
          <div>
            <Label>备注</Label>
            <Input value={form.remark} onChange={(e) => setForm({ ...form, remark: e.target.value })} />
          </div>
          <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-3">
            <Label>手动添加模型</Label>
            <p className="mb-2 text-[11px] text-slate-400">同步列表没有的模型（如 embedding-3）可在此手输入库</p>
            <div className="flex gap-2">
              <Input
                value={manualModel}
                onChange={(e) => setManualModel(e.target.value)}
                placeholder="如 embedding-3 / embedding-2"
                className="font-mono text-xs"
                autoComplete="off"
              />
              {edit ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!manualModel.trim()}
                  onClick={() => addManualModel(edit.id, manualModel)}
                >
                  添加
                </Button>
              ) : null}
            </div>
            {edit && modelList[edit.id]?.length ? (
              <p className="mt-2 text-[11px] text-slate-400">
                已入库: {modelList[edit.id].slice(0, 8).map((m) => m.model_id).join(", ")}
                {modelList[edit.id].length > 8 ? ` 等 ${modelList[edit.id].length} 个` : ""}
              </p>
            ) : null}
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={saveProvider} disabled={!form.name.trim() || !form.base_url.trim()} className="w-full">保存</Button>
        </div>
      </Modal>
    </div>
  );
}

/* ================= 用量 ================= */
function UsageTab() {
  const [data, setData] = useState<UsageSummary | null>(null);
  const [errors, setErrors] = useState<CallErrorItem[]>([]);
  const [errorFilter, setErrorFilter] = useState<"all" | "failed" | "degraded">("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get<UsageSummary>("/usage/summary?days=30"),
      api.get<{ items: CallErrorItem[] }>("/usage/errors?days=30&limit=80"),
    ])
      .then(([d, e]) => {
        setData(d);
        setErrors(e.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;
  if (!data) return null;

  const filteredErrors =
    errorFilter === "all" ? errors : errors.filter((x) => x.status === errorFilter);

  function fmtTime(iso: string | null): string {
    if (!iso) return "-";
    try {
      const d = new Date(iso);
      return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    } catch {
      return iso;
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="text-xs text-slate-400">输入 Token(近 30 天)</div>
          <div className="mt-1 text-2xl font-semibold">{data.total_prompt_tokens.toLocaleString()}</div>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="text-xs text-slate-400">输出 Token(近 30 天)</div>
          <div className="mt-1 text-2xl font-semibold">{data.total_completion_tokens.toLocaleString()}</div>
        </div>
        <div className="rounded-xl border border-red-100 bg-red-50 p-4">
          <div className="text-xs text-red-400">调用失败</div>
          <div className="mt-1 text-2xl font-semibold text-red-600">{(data.error_failed_count ?? 0).toLocaleString()}</div>
        </div>
        <div className="rounded-xl border border-amber-100 bg-amber-50 p-4">
          <div className="text-xs text-amber-500">主模型失败(已降级)</div>
          <div className="mt-1 text-2xl font-semibold text-amber-600">{(data.error_degraded_count ?? 0).toLocaleString()}</div>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">调用失败日志</h3>
          <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
            {([
              ["all", "全部"],
              ["failed", "失败"],
              ["degraded", "降级"],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setErrorFilter(key)}
                className={`rounded-md px-2.5 py-1 font-medium transition ${
                  errorFilter === key ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {filteredErrors.length === 0 ? (
          <p className="text-xs text-slate-400">近 30 天暂无失败记录（新失败会自动写入）</p>
        ) : (
          <div className="max-h-80 space-y-2 overflow-y-auto">
            {filteredErrors.map((e) => (
              <div
                key={e.id}
                className={`rounded-lg border px-3 py-2 text-xs ${
                  e.status === "failed" ? "border-red-100 bg-red-50/60" : "border-amber-100 bg-amber-50/60"
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-400">{fmtTime(e.created_at)}</span>
                  <Badge color={e.status === "failed" ? "red" : "amber"}>
                    {e.status === "failed" ? "失败" : "降级"}
                  </Badge>
                  <span className="font-medium text-slate-700">{purposeLabel(e.purpose)}</span>
                  <span className="text-slate-400">
                    {e.product_key === "novel" ? "小说" : e.product_key === "media" ? "自媒体" : e.product_key}
                  </span>
                  {(e.provider_name || e.model_name) && (
                    <span className="font-mono text-slate-500">
                      {[e.provider_name, e.model_name].filter(Boolean).join(" / ")}
                    </span>
                  )}
                </div>
                <p className="mt-1 break-all leading-relaxed text-slate-600">{e.error || "-"}</p>
              </div>
            ))}
          </div>
        )}
      </div>
      {data.by_product && Object.keys(data.by_product).length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold">按产品用量</h3>
          <div className="grid gap-2 sm:grid-cols-3">
            {Object.entries(data.by_product).map(([pk, v]) => (
              <div key={pk} className="rounded-lg bg-slate-50 px-3 py-2 text-xs">
                <div className="font-medium text-slate-700">{pk === "novel" ? "小说" : pk === "media" ? "自媒体" : pk}</div>
                <div className="mt-1 text-slate-500">
                  入 {(v.prompt_tokens || 0).toLocaleString()} · 出 {(v.completion_tokens || 0).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-sm font-semibold">每日用量(近 30 天)</h3>
        {(() => {
          const byDate = new Map<string, number>();
          for (const p of data.points) {
            byDate.set(p.date, (byDate.get(p.date) || 0) + p.prompt_tokens + p.completion_tokens);
          }
          const days = Array.from(byDate.entries()).sort(([a], [b]) => (a < b ? -1 : 1)).slice(-30);
          if (days.length === 0) return <p className="text-xs text-slate-400">暂无数据</p>;
          const max = Math.max(...days.map(([, v]) => v));
          return (
            <div className="flex h-32 items-end gap-1">
              {days.map(([date, value]) => (
                <div key={date} className="group relative flex-1" title={`${date}: ${value.toLocaleString()} tokens`}>
                  <div
                    className="w-full rounded-t bg-indigo-500/80 transition group-hover:bg-indigo-600"
                    style={{ height: `${Math.max(4, (value / max) * 120)}px` }}
                  />
                </div>
              ))}
            </div>
          );
        })()}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-sm font-semibold">明细(按日期 / 用途 / 模型)</h3>
        {data.points.length === 0 ? (
          <Empty text="还没有 AI 调用记录" />
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="text-slate-400">
              <tr>
                <th className="py-1.5">日期</th>
                <th>产品</th>
                <th>用途</th>
                <th>模型</th>
                <th className="text-right">输入</th>
                <th className="text-right">输出</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {data.points.map((p, i) => (
                <tr key={i}>
                  <td className="py-1.5">{p.date}</td>
                  <td>{p.product_key || "-"}</td>
                  <td>{purposeLabel(p.purpose)}</td>
                  <td className="text-slate-500">{p.model_name || "-"}</td>
                  <td className="text-right">{p.prompt_tokens.toLocaleString()}</td>
                  <td className="text-right">{p.completion_tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
