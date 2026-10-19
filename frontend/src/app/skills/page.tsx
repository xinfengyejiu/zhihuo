"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { filterSkillsForWorkshop, useProduct } from "@/lib/product";
import type { SkillInfo } from "@/lib/types";
import { Badge, Button, Empty, ErrorText, Input, Label, Modal, Select, Spinner, Textarea } from "@/components/ui";

function peekFrontmatterName(markdown: string): string | null {
  const text = markdown.trim();
  if (!text.startsWith("---")) return null;
  const end = text.indexOf("---", 3);
  if (end === -1) return null;
  for (const line of text.slice(3, end).split(/\r?\n/)) {
    const m = line.trim().match(/^name:\s*(.+)$/i);
    if (m) return m[1].trim().replace(/^["']|["']$/g, "");
  }
  return null;
}

const STAGE_LABELS: Record<string, string> = {
  outline: "大纲生成",
  chapter: "章节正文",
  polish: "润色",
  analysis: "复盘分析",
  review: "审核审查",
  script: "漫剧分镜",
};

const SCOPE_OPTIONS = [
  { key: "shared", label: "共享" },
  { key: "novel", label: "小说" },
  { key: "media", label: "自媒体" },
  { key: "drama", label: "漫剧" },
];

const emptyForm = {
  name: "",
  version: "1.0.0",
  stage: "chapter",
  system_prompt: "",
  params: {} as Record<string, unknown>,
  scope_tags: ["shared"] as string[],
};

export default function SkillsPage() {
  const { current } = useProduct();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<SkillInfo | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [temperature, setTemperature] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [importName, setImportName] = useState("");
  const [importStage, setImportStage] = useState("chapter");
  const [importText, setImportText] = useState("");
  const [importFileName, setImportFileName] = useState("");
  const [importGithubUrl, setImportGithubUrl] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [viewSkill, setViewSkill] = useState<SkillInfo | null>(null);
  const [viewContext, setViewContext] = useState("");
  const importFileRef = useRef<HTMLInputElement>(null);
  const importFolderRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setSkills(await api.get<SkillInfo[]>("/skills"));
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const el = importFolderRef.current;
    if (!el) return;
    el.setAttribute("webkitdirectory", "");
    el.setAttribute("directory", "");
  }, [importOpen]);

  const visibleSkills = useMemo(() => filterSkillsForWorkshop(skills, current), [skills, current]);

  useEffect(() => {
    // 新建默认挂上当前产品标签
    if (!edit && current && current !== "shared") {
      setForm((f) => {
        const tags = new Set(f.scope_tags);
        tags.add("shared");
        tags.add(current);
        return { ...f, scope_tags: Array.from(tags) };
      });
    }
  }, [current, edit]);

  async function seedPresets() {
    setError("");
    try {
      const res = await api.post<{ created: number }>("/skills/seed-presets");
      setMsg(res.created > 0 ? `已导入 ${res.created} 个预置技能` : "预置技能已全部存在");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    }
  }

  async function save() {
    setError("");
    const params = temperature ? { temperature: parseFloat(temperature) || undefined } : {};
    try {
      if (edit) {
        await api.put(`/skills/${edit.id}`, { ...form, params });
      } else {
        await api.post<SkillInfo>("/skills", { ...form, params });
      }
      setOpen(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function importMd() {
    setError("");
    setImportBusy(true);
    try {
      await api.post("/skills/import-markdown", { name: importName || null, stage: importStage, markdown: importText });
      setImportOpen(false);
      setImportName("");
      setImportText("");
      setImportFileName("");
      setImportGithubUrl("");
      if (importFileRef.current) importFileRef.current.value = "";
      setMsg("导入成功");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImportBusy(false);
    }
  }

  async function importGithub() {
    const url = importGithubUrl.trim();
    if (!url) {
      setError("请填写 GitHub 地址");
      return;
    }
    setError("");
    setImportBusy(true);
    try {
      const scope_tags = ["shared", ...(current && current !== "shared" ? [current] : [])];
      const res = await api.post<{ created: number; skipped: string[]; items: { name: string }[] }>("/skills/import-github", {
        url,
        stage: importStage,
        name: importName || null,
        scope_tags,
      });
      setImportOpen(false);
      setImportGithubUrl("");
      setImportName("");
      setImportText("");
      setImportFileName("");
      const skipHint = res.skipped?.length ? `，跳过 ${res.skipped.length} 个已存在` : "";
      setMsg(`已从 GitHub 安装 ${res.created} 个技能${skipHint}`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "GitHub 安装失败");
    } finally {
      setImportBusy(false);
    }
  }

  async function finishPackImport(res: { created: number; skipped: string[] }, label: string) {
    setImportOpen(false);
    setImportFileName("");
    setImportText("");
    setImportGithubUrl("");
    if (importFileRef.current) importFileRef.current.value = "";
    if (importFolderRef.current) importFolderRef.current.value = "";
    const skipHint = res.skipped?.length ? `，跳过 ${res.skipped.length} 个已存在` : "";
    setMsg(`已从${label}安装 ${res.created} 个技能包${skipHint}`);
    load();
  }

  async function onPickSkillFolder(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError("");
    setImportBusy(true);
    const list = Array.from(files);
    const root = list[0].webkitRelativePath?.split("/")[0] || "folder";
    setImportFileName(`${root}/ (${list.length} 个文件)`);
    try {
      const form = new FormData();
      const paths: string[] = [];
      for (const f of list) {
        const rel = f.webkitRelativePath || f.name;
        paths.push(rel);
        form.append("files", f);
      }
      form.append("paths", JSON.stringify(paths));
      form.append("stage", importStage);
      form.append("scope_tags", JSON.stringify(["shared", ...(current && current !== "shared" ? [current] : [])]));
      if (importName.trim()) form.append("name", importName.trim());
      else form.append("name", root);
      const res = await api.upload<{ created: number; skipped: string[] }>("/skills/import-folder", form);
      await finishPackImport(res, "文件夹");
    } catch (e) {
      setError(e instanceof Error ? e.message : "文件夹导入失败");
    } finally {
      setImportBusy(false);
    }
  }

  async function onPickSkillFile(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setError("");
    const lower = file.name.toLowerCase();
    if (lower.endsWith(".zip") || file.type === "application/zip" || file.type === "application/x-zip-compressed") {
      setImportBusy(true);
      setImportFileName(file.name);
      try {
        const form = new FormData();
        form.append("file", file);
        form.append("stage", importStage);
        form.append("scope_tags", JSON.stringify(["shared", ...(current && current !== "shared" ? [current] : [])]));
        if (importName.trim()) form.append("name", importName.trim());
        const res = await api.upload<{ created: number; skipped: string[] }>("/skills/import-zip", form);
        await finishPackImport(res, "zip");
      } catch (e) {
        setError(e instanceof Error ? e.message : "zip 导入失败");
      } finally {
        setImportBusy(false);
      }
      return;
    }
    if (!/\.(md|markdown|txt|skill)$/.test(lower) && file.type && !file.type.startsWith("text/")) {
      setError("请选择文件夹 / .zip / .md 技能文件");
      return;
    }
    try {
      const text = await file.text();
      if (!text.trim()) {
        setError("文件内容为空");
        return;
      }
      setImportText(text);
      setImportFileName(file.name);
      const fmName = peekFrontmatterName(text);
      if (fmName && !importName.trim()) setImportName(fmName);
      else if (!importName.trim() && !fmName) {
        const base = file.name.replace(/\.(md|markdown|txt|skill)$/i, "");
        if (base && base.toLowerCase() !== "skill") setImportName(base);
      }
    } catch {
      setError("读取文件失败");
    }
  }

  async function openViewSkill(s: SkillInfo) {
    setViewSkill(s);
    setViewContext("");
    try {
      const detail = await api.get<SkillInfo>(`/skills/${s.id}`);
      setViewSkill(detail);
      const ctx = await api.get<{ context: string }>(`/skills/${s.id}/context`);
      setViewContext(ctx.context || "");
    } catch {
      setViewContext(s.system_prompt);
    }
  }

  async function remove(id: number) {
    if (!confirm("确定删除该技能?已挂载的书会自动取消挂载")) return;
    await api.delete(`/skills/${id}`);
    load();
  }

  if (loading) return <Spinner />;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">技能工坊</h1>
          <p className="mt-0.5 text-xs text-slate-400">
            按顶栏产品筛选(含 shared)。当前：
            {current === "media" ? "自媒体" : current === "novel" ? "小说" : current || "全部"}
            。文件夹技能包对话时注入目录内全部文本。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={seedPresets}>
            导入预置技能
          </Button>
          <Button
            variant="outline"
            onClick={async () => {
              try {
                const data = await api.get<{ items: unknown[] }>("/skills/export");
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = "xiaohuo-skills.json";
                a.click();
                URL.revokeObjectURL(url);
                setMsg(`已导出 ${data.items.length} 个技能`);
              } catch (e) {
                setError(e instanceof Error ? e.message : "导出失败");
              }
            }}
          >
            导出清单
          </Button>
          <Button variant="outline" onClick={() => setImportOpen(true)}>
            导入技能包
          </Button>
          <Button
            onClick={() => {
              setEdit(null);
              setForm(emptyForm);
              setTemperature("");
              setOpen(true);
            }}
          >
            + 新建技能
          </Button>
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      {msg && <p className="mb-3 text-xs text-emerald-600">{msg}</p>}

      {visibleSkills.length === 0 ? (
        <Empty text={skills.length ? "当前产品下没有技能(可勾选 shared 或对应产品标签)" : "还没有技能,先导入预置技能包"} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {visibleSkills.map((s) => (
            <div key={s.id} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between">
                <h4 className="font-medium text-slate-700">{s.name}</h4>
                <div className="flex flex-wrap gap-1">
                  <Badge color="indigo">{STAGE_LABELS[s.stage] || s.stage}</Badge>
                  {s.builtin && <Badge>预置</Badge>}
                  {(s.file_count || 0) > 0 && <Badge color="green">包 {s.file_count} 文件</Badge>}
                  {(s.scope_tags || []).map((t) => (
                    <Badge key={t} color="slate">
                      {t}
                    </Badge>
                  ))}
                </div>
              </div>
              <p className="mt-2 line-clamp-3 text-xs text-slate-500">{s.system_prompt}</p>
              <div className="mt-3 flex gap-2 text-xs">
                <button className="text-indigo-600 hover:underline" onClick={() => openViewSkill(s)}>
                  查看
                </button>
                <button
                  className="text-indigo-600 hover:underline"
                  onClick={() => {
                    setEdit(s);
                    setForm({
                      name: s.name,
                      version: s.version,
                      stage: s.stage,
                      system_prompt: s.system_prompt,
                      params: s.params,
                      scope_tags: s.scope_tags?.length ? [...s.scope_tags] : ["shared"],
                    });
                    setTemperature(s.params?.temperature !== undefined ? String(s.params.temperature) : "");
                    setOpen(true);
                  }}
                >
                  编辑
                </button>
                <button className="text-red-500 hover:underline" onClick={() => remove(s.id)}>
                  删除
                </button>
                <span className="ml-auto text-slate-400">v{s.version}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal open={open} title={edit ? "编辑技能" : "新建技能"} onClose={() => setOpen(false)} wide>
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <Label>名称 *</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <Label>阶段</Label>
              <Select value={form.stage} onChange={(e) => setForm({ ...form, stage: e.target.value })} className="w-full">
                {Object.entries(STAGE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div>
            <Label>系统提示词(生成时注入)</Label>
            <Textarea rows={10} value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} />
          </div>
          <div className="max-w-32">
            <Label>温度覆盖(可选,0-2)</Label>
            <Input value={temperature} onChange={(e) => setTemperature(e.target.value)} placeholder="如 0.7" />
          </div>
          <div>
            <Label>产品标签</Label>
            <div className="mt-1 flex flex-wrap gap-3 text-sm">
              {SCOPE_OPTIONS.map((opt) => (
                <label key={opt.key} className="flex items-center gap-1.5 text-slate-600">
                  <input
                    type="checkbox"
                    checked={form.scope_tags.includes(opt.key)}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...form.scope_tags, opt.key]
                        : form.scope_tags.filter((t) => t !== opt.key);
                      setForm({ ...form, scope_tags: next.length ? next : ["shared"] });
                    }}
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={save} disabled={!form.name.trim() || !form.system_prompt.trim()} className="w-full">
            保存
          </Button>
        </div>
      </Modal>

      <Modal
        open={importOpen}
        title="导入技能包"
        onClose={() => {
          setImportOpen(false);
          setImportFileName("");
          if (importFileRef.current) importFileRef.current.value = "";
          if (importFolderRef.current) importFolderRef.current.value = "";
        }}
      >
        <div className="space-y-3">
          <p className="rounded-lg bg-slate-50 p-2 text-xs text-slate-500">
            推荐上传整个技能文件夹(如「公众号skills」):目录内全部文件会保存到服务器固定目录,对话选用时整体注入。也支持 zip / GitHub / 单文件 md。
          </p>
          <div>
            <Label>GitHub 地址</Label>
            <Input
              value={importGithubUrl}
              onChange={(e) => setImportGithubUrl(e.target.value)}
              placeholder="https://github.com/owner/repo 或 …/tree/main/skills/xxx"
            />
            <p className="mt-1 text-[11px] text-slate-400">
              会下载 SKILL.md 所在目录的全部文件。私有库可在后端 .env 配置 GITHUB_TOKEN。
            </p>
            <Button
              type="button"
              className="mt-2 w-full"
              variant="outline"
              disabled={importBusy || !importGithubUrl.trim()}
              onClick={importGithub}
            >
              {importBusy && importGithubUrl.trim() ? "正在从 GitHub 安装…" : "从 GitHub 安装"}
            </Button>
          </div>
          <div className="border-t border-slate-100 pt-3">
            <Label>本地文件夹 / 文件</Label>
            <input
              ref={importFolderRef}
              type="file"
              className="hidden"
              multiple
              onChange={(e) => {
                onPickSkillFolder(e.target.files);
                e.target.value = "";
              }}
            />
            <input
              ref={importFileRef}
              type="file"
              accept=".md,.markdown,.txt,.skill,.zip,text/markdown,text/plain,application/zip"
              className="hidden"
              onChange={(e) => {
                onPickSkillFile(e.target.files);
                e.target.value = "";
              }}
            />
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Button type="button" variant="outline" size="sm" disabled={importBusy} onClick={() => importFolderRef.current?.click()}>
                选择文件夹…
              </Button>
              <Button type="button" variant="outline" size="sm" disabled={importBusy} onClick={() => importFileRef.current?.click()}>
                选择文件/zip…
              </Button>
              {importFileName ? (
                <span className="truncate text-xs text-slate-500">{importFileName}</span>
              ) : (
                <span className="text-xs text-slate-400">未选择</span>
              )}
            </div>
          </div>
          <div>
            <Label>技能名称(留空则用 frontmatter 的 name)</Label>
            <Input value={importName} onChange={(e) => setImportName(e.target.value)} />
          </div>
          <div>
            <Label>默认阶段</Label>
            <Select value={importStage} onChange={(e) => setImportStage(e.target.value)}>
              {Object.entries(STAGE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Markdown 内容(本地/粘贴导入)</Label>
            <Textarea rows={6} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder="选择文件后会自动填入,也可手动粘贴" />
          </div>
          <ErrorText>{error}</ErrorText>
          <Button onClick={importMd} disabled={importBusy || !importText.trim()} className="w-full">
            {importBusy && importText.trim() ? "导入中…" : "从文本/文件导入"}
          </Button>
        </div>
      </Modal>

      <Modal
        open={!!viewSkill}
        title={viewSkill?.name || ""}
        onClose={() => {
          setViewSkill(null);
          setViewContext("");
        }}
        wide
      >
        <div className="space-y-3">
          {(viewSkill?.file_count || 0) > 0 && (
            <div className="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs text-slate-600">
              <div className="mb-1 font-medium text-slate-700">技能包文件 ({viewSkill?.file_count})</div>
              <ul className="max-h-28 space-y-0.5 overflow-auto font-mono">
                {(viewSkill?.files || []).map((f) => (
                  <li key={f.path}>
                    {f.path} <span className="text-slate-400">({f.size}B)</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <div className="mb-1 text-xs font-medium text-slate-500">对话注入预览</div>
            <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
              {viewContext || viewSkill?.system_prompt}
            </pre>
          </div>
        </div>
      </Modal>
    </div>
  );
}
