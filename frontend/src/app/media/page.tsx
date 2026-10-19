"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProduct } from "@/lib/product";
import { Badge, Button, Empty, ErrorText, Input, Label, Modal, Select, Spinner, Textarea } from "@/components/ui";

const PLATFORM_LABEL: Record<string, string> = {
  xhs: "小红书",
  wechat_mp: "公众号",
  douyin: "抖音",
  toutiao: "头条",
  other: "其他",
};

const TOKEN_STATUS: Record<string, { label: string; color: string }> = {
  not_configured: { label: "未配置凭证", color: "slate" },
  ready: { label: "可发布", color: "green" },
  expired: { label: "已过期", color: "red" },
};

interface Account {
  id: number;
  name: string;
  platform: string;
  persona: string | null;
  audience: string | null;
  forbidden: string | null;
  app_id?: string | null;
  publish_api_base?: string | null;
  token_note?: string | null;
  token_status?: string;
  access_token_masked?: string | null;
  has_app_secret?: boolean;
  has_access_token?: boolean;
  has_refresh_token?: boolean;
}

const emptyForm = { name: "", platform: "xhs", persona: "", audience: "", forbidden: "" };
const emptyToken = {
  app_id: "",
  app_secret: "",
  access_token: "",
  refresh_token: "",
  publish_api_base: "",
  token_note: "",
};

export default function MediaAccountsPage() {
  const { user, loading: authLoading } = useAuth();
  const { setCurrent } = useProduct();
  const router = useRouter();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [tokenOpen, setTokenOpen] = useState(false);
  const [edit, setEdit] = useState<Account | null>(null);
  const [tokenAcc, setTokenAcc] = useState<Account | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [tokenForm, setTokenForm] = useState(emptyToken);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setAccounts(await api.get<Account[]>("/media/accounts"));
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (user) {
      setCurrent("media");
      load();
    }
  }, [user, authLoading, router, load, setCurrent]);

  async function save() {
    setError("");
    try {
      const body = {
        name: form.name.trim(),
        platform: form.platform,
        persona: form.persona || null,
        audience: form.audience || null,
        forbidden: form.forbidden || null,
      };
      if (edit) await api.put(`/media/accounts/${edit.id}`, body);
      else await api.post("/media/accounts", body);
      setOpen(false);
      setEdit(null);
      setForm(emptyForm);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function openCreator() {
    if (!tokenAcc) return;
    setError("");
    setMsg("");
    try {
      const r = await api.post<{ ok: boolean; message?: string; url?: string }>(
        `/media/accounts/${tokenAcc.id}/open-creator`,
        {},
      );
      setMsg(r.message || (r.ok ? "已打开创作者后台" : "打开失败"));
      if (!r.ok && r.url) {
        window.open(r.url, "_blank");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "打开创作者后台失败");
    }
  }

  async function saveToken() {
    if (!tokenAcc) return;
    setError("");
    setMsg("");
    try {
      await api.put(`/media/accounts/${tokenAcc.id}/token`, {
        app_id: tokenForm.app_id || null,
        app_secret: tokenForm.app_secret || null,
        access_token: tokenForm.access_token || null,
        refresh_token: tokenForm.refresh_token || null,
        publish_api_base: tokenForm.publish_api_base || null,
        token_note: tokenForm.token_note || null,
      });
      setTokenOpen(false);
      setTokenAcc(null);
      setTokenForm(emptyToken);
      setMsg("发布凭证已保存");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "凭证保存失败");
    }
  }

  async function remove(id: number) {
    if (!confirm("确定删除该账号?相关成稿也会受影响")) return;
    await api.delete(`/media/accounts/${id}`);
    load();
  }

  if (authLoading || loading) return <Spinner />;
  if (!user) return null;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">自媒体账号</h1>
          <p className="mt-0.5 text-xs text-slate-400">
            为人设建档,并配置各平台 Token 后可对成稿一键发布(公众号 / 小红书 / 抖音 / 头条)
          </p>
        </div>
        <Button
          onClick={() => {
            setEdit(null);
            setForm(emptyForm);
            setOpen(true);
          }}
        >
          + 新建账号
        </Button>
      </div>
      <ErrorText>{error}</ErrorText>
      {msg && <p className="mb-2 text-xs text-emerald-600">{msg}</p>}
      {accounts.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-200 bg-white p-8 text-center">
          <p className="text-sm text-slate-600">还没有自媒体账号</p>
          <p className="mt-2 text-xs text-slate-400">
            账号 = 平台人设档案 + 发布凭证。可以先去「聊天」用 AI 定选题,再回来建账号写稿发布。
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            <Button
              onClick={() => {
                setEdit(null);
                setForm(emptyForm);
                setOpen(true);
              }}
            >
              + 新建账号
            </Button>
            <Link href="/media/chat">
              <Button variant="outline">先去聊天定选题</Button>
            </Link>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {accounts.map((a) => {
            const ts = TOKEN_STATUS[a.token_status || "not_configured"] || TOKEN_STATUS.not_configured;
            return (
              <div key={a.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link href={`/media/${a.id}`} className="font-medium text-indigo-600 hover:underline">
                      {a.name}
                    </Link>
                    <Badge color="slate">{PLATFORM_LABEL[a.platform] || a.platform}</Badge>
                    <Badge color={ts.color}>{ts.label}</Badge>
                  </div>
                  {a.audience && <p className="mt-1 truncate text-xs text-slate-400">受众:{a.audience}</p>}
                  {a.has_access_token && (
                    <p className="mt-0.5 font-mono text-[11px] text-slate-400">Token:{a.access_token_masked}</p>
                  )}
                  {a.publish_api_base && (
                    <p className="mt-0.5 truncate text-[11px] text-slate-400">API:{a.publish_api_base}</p>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <Link href={`/media/${a.id}`}>
                    <Button size="sm">工作台</Button>
                  </Link>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setTokenAcc(a);
                      setTokenForm({
                        app_id: a.app_id || "",
                        app_secret: "",
                        access_token: "",
                        refresh_token: "",
                        publish_api_base: a.publish_api_base || "",
                        token_note: a.token_note || "",
                      });
                      setTokenOpen(true);
                      setError("");
                    }}
                  >
                    发布凭证
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEdit(a);
                      setForm({
                        name: a.name,
                        platform: a.platform,
                        persona: a.persona || "",
                        audience: a.audience || "",
                        forbidden: a.forbidden || "",
                      });
                      setOpen(true);
                    }}
                  >
                    编辑
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => remove(a.id)}>
                    删除
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Modal open={open} title={edit ? "编辑账号" : "新建账号"} onClose={() => setOpen(false)}>
        <div className="space-y-3">
          <div>
            <Label>名称 *</Label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如:职场干货号" />
          </div>
          <div>
            <Label>平台</Label>
            <Select value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })} className="w-full">
              {Object.entries(PLATFORM_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>受众</Label>
            <Input value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })} placeholder="如:25-35 职场女性" />
          </div>
          <div>
            <Label>人设 / 口吻</Label>
            <Textarea
              value={form.persona}
              onChange={(e) => setForm({ ...form, persona: e.target.value })}
              rows={4}
              placeholder="语气、人设、常用表达…"
            />
          </div>
          <div>
            <Label>禁忌</Label>
            <Textarea value={form.forbidden} onChange={(e) => setForm({ ...form, forbidden: e.target.value })} rows={2} placeholder="禁用词、敏感话题…" />
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={save} disabled={!form.name.trim()} className="w-full">
            保存
          </Button>
        </div>
      </Modal>

      <Modal
        open={tokenOpen}
        title={tokenAcc ? `发布凭证 · ${tokenAcc.name}` : "发布凭证"}
        onClose={() => {
          setTokenOpen(false);
          setTokenAcc(null);
        }}
      >
        <div className="space-y-3">
          <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-[11px] leading-5 text-indigo-700">
            <p>
              <strong>公众号</strong>:填 AppID + AppSecret(或 access_token),一键写入微信草稿箱。
            </p>
            <p className="mt-1">
              <strong>小红书 / 抖音 / 头条</strong>:织火只存你的桥接地址与密钥,不托管平台私有登录。
              推荐流程:
            </p>
            <ol className="mt-1 list-decimal space-y-0.5 pl-4">
              <li>
                本机启动示例桥接:
                <code className="mx-0.5 break-all">python backend/scripts/media_bridge_example.py</code>
              </li>
              <li>
                下方填「发布 API 地址」
                <code className="mx-0.5">http://127.0.0.1:8765</code>
                ,Access Token 填桥接密钥(默认
                <code className="mx-0.5">change-me-bridge-secret</code>)
              </li>
              <li>点「打开创作者后台」在弹出浏览器里登录平台(登录态本机持久化)</li>
              <li>成稿页「一键发布」→ 织火 POST 到你的桥接 /publish</li>
            </ol>
            <p className="mt-1">密钥加密存储;留空表示不修改已有值。</p>
          </div>
          {tokenAcc && tokenAcc.platform !== "wechat_mp" && (
            <Button type="button" variant="outline" className="w-full" onClick={openCreator}>
              打开创作者后台登录
            </Button>
          )}
          {msg && <p className="text-xs text-emerald-600">{msg}</p>}
          {tokenAcc?.platform === "wechat_mp" && (
            <div>
              <Label>AppID</Label>
              <Input
                value={tokenForm.app_id}
                onChange={(e) => setTokenForm({ ...tokenForm, app_id: e.target.value })}
                placeholder="wx…"
                className="font-mono text-xs"
              />
            </div>
          )}
          {tokenAcc?.platform === "wechat_mp" && (
            <div>
              <Label>AppSecret{tokenAcc.has_app_secret ? "(已配置,留空不改)" : ""}</Label>
              <Input
                type="password"
                value={tokenForm.app_secret}
                onChange={(e) => setTokenForm({ ...tokenForm, app_secret: e.target.value })}
                placeholder="sk…"
                className="font-mono text-xs"
              />
            </div>
          )}
          <div>
            <Label>Access Token{tokenAcc?.has_access_token ? `(已有 ${tokenAcc.access_token_masked})` : ""}</Label>
            <Input
              type="password"
              value={tokenForm.access_token}
              onChange={(e) => setTokenForm({ ...tokenForm, access_token: e.target.value })}
              placeholder="平台 access_token / Bearer"
              className="font-mono text-xs"
            />
          </div>
          <div>
            <Label>Refresh Token(可选)</Label>
            <Input
              type="password"
              value={tokenForm.refresh_token}
              onChange={(e) => setTokenForm({ ...tokenForm, refresh_token: e.target.value })}
              className="font-mono text-xs"
            />
          </div>
          <div>
            <Label>发布 API 地址(小红书/抖音/头条/自建桥接)</Label>
            <Input
              value={tokenForm.publish_api_base}
              onChange={(e) => setTokenForm({ ...tokenForm, publish_api_base: e.target.value })}
              placeholder="http://127.0.0.1:8765"
              className="font-mono text-xs"
            />
          </div>
          <div>
            <Label>备注</Label>
            <Input
              value={tokenForm.token_note}
              onChange={(e) => setTokenForm({ ...tokenForm, token_note: e.target.value })}
              placeholder="如:企业主体开放平台应用"
            />
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={saveToken} className="w-full">
            保存凭证
          </Button>
        </div>
      </Modal>
    </div>
  );
}
