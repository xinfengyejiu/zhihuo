/** 流式生成时把半成品 JSON / 行文本解析成可读的卷章目录。 */

export interface LiveChapter {
  title: string;
  outline?: string | null;
}

export interface LiveVolume {
  title: string;
  chapters: LiveChapter[];
}

export function parseLiveCatalog(text: string): LiveVolume[] {
  if (!text.trim()) return [];
  const fromJson = parsePartialJson(text);
  if (fromJson.length) return fromJson;
  return parseLineCatalog(text);
}

function parsePartialJson(text: string): LiveVolume[] {
  if (!text.includes("volumes") && !text.includes('"chapters"')) return [];
  const blob = extractJsonBlob(text);
  if (!blob) return [];

  for (const candidate of [closeJson(blob), blob]) {
    try {
      const data = JSON.parse(candidate);
      const vols = Array.isArray(data?.volumes) ? data.volumes : Array.isArray(data) ? data : null;
      if (!vols) continue;
      const out: LiveVolume[] = [];
      for (const v of vols) {
        if (!v || typeof v !== "object") continue;
        const title = String(v.title || v.name || "未命名卷").trim() || "未命名卷";
        const chapters: LiveChapter[] = [];
        const list = Array.isArray(v.chapters) ? v.chapters : [];
        for (const ch of list) {
          if (!ch) continue;
          if (typeof ch === "string") {
            const t = stripChapterPrefix(ch);
            if (t) chapters.push({ title: t });
            continue;
          }
          const t = stripChapterPrefix(String(ch.title || ch.name || "").trim());
          if (!t) continue;
          const outline = String(ch.outline || ch.summary || "").trim() || null;
          const characters = String(ch.characters || "").trim();
          chapters.push({
            title: t,
            outline: characters ? (outline ? `${outline}（出场人物:${characters}）` : `出场人物:${characters}`) : outline,
          });
        }
        if (chapters.length) out.push({ title, chapters });
      }
      if (out.length) return out;
    } catch {
      /* try next */
    }
  }

  // JSON 仍不完整时,按已出现的 volume/chapter 对象尽量提取
  return extractByRegex(blob);
}

function extractJsonBlob(text: string): string | null {
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) return fence[1].trim();
  const start = text.indexOf("{");
  if (start < 0) return null;
  return text.slice(start);
}

function closeJson(blob: string): string {
  let src = blob.trim();
  // 去掉未写完的末尾键值碎片
  src = src.replace(/,\s*"[^"]*$/, "");
  src = src.replace(/,\s*"[^"]*"\s*:\s*("[^"]*)?$/, "");
  src = src.replace(/,\s*$/, "");
  const stack: string[] = [];
  let inStr = false;
  let esc = false;
  for (const ch of src) {
    if (inStr) {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') {
      inStr = true;
      continue;
    }
    if (ch === "{" || ch === "[") stack.push(ch);
    else if (ch === "}" && stack[stack.length - 1] === "{") stack.pop();
    else if (ch === "]" && stack[stack.length - 1] === "[") stack.pop();
  }
  if (inStr) {
    const cut = src.lastIndexOf('"');
    if (cut > 0) src = src.slice(0, cut);
    src = src.replace(/,\s*$/, "").replace(/:\s*$/, "").replace(/,\s*"[^"]*"\s*$/, "");
    return closeJson(src);
  }
  let out = src;
  for (let i = stack.length - 1; i >= 0; i--) out += stack[i] === "{" ? "}" : "]";
  return out;
}

function extractByRegex(blob: string): LiveVolume[] {
  const out: LiveVolume[] = [];
  let current: LiveVolume | null = null;
  // 粗粒度:遇到 volume 的 title 后,收集后续 title/outline 对直到下一个 volume
  const pieces = blob.split(/"volumes"\s*:\s*\[|"chapters"\s*:\s*\[/i);
  // 更稳:按对象切分
  const objRe = /\{[^{}]*"title"\s*:\s*"((?:\\.|[^"\\])*)"[^{}]*\}/g;
  const titles: { title: string; outline?: string; isVolumeHint: boolean }[] = [];
  let m: RegExpExecArray | null;
  while ((m = objRe.exec(blob))) {
    const title = unescapeJson(m[1]).trim();
    if (!title) continue;
    const outlineMatch = m[0].match(/"outline"\s*:\s*"((?:\\.|[^"\\])*)"/);
    const outline = outlineMatch ? unescapeJson(outlineMatch[1]).trim() : "";
    const isVolumeHint = /"chapters"\s*:/.test(m[0]) || /第[0-9零一二三四五六七八九十百千两]+[卷部]/.test(title) || title.includes("卷");
    titles.push({ title, outline, isVolumeHint });
  }
  // 上面对象级太碎,改用体积更大的 volume 块
  const volRe = /\{\s*"title"\s*:\s*"((?:\\.|[^"\\])*)"[\s\S]*?"chapters"\s*:\s*\[([\s\S]*?)(?:\]\s*\}|\]\s*$)/g;
  while ((m = volRe.exec(blob))) {
    const vtitle = unescapeJson(m[1]).trim() || "未命名卷";
    const chapters: LiveChapter[] = [];
    const chRe = /\{\s*"title"\s*:\s*"((?:\\.|[^"\\])*)"(?:[\s\S]*?"outline"\s*:\s*"((?:\\.|[^"\\])*)")?/g;
    let cm: RegExpExecArray | null;
    while ((cm = chRe.exec(m[2]))) {
      const t = stripChapterPrefix(unescapeJson(cm[1]).trim());
      if (t) chapters.push({ title: t, outline: cm[2] ? unescapeJson(cm[2]).trim() : null });
    }
    if (chapters.length) out.push({ title: vtitle, chapters });
  }
  if (out.length) return out;

  // 最后兜底:所有 title 当章,塞进一个临时卷
  if (titles.length) {
    current = { title: "生成中…", chapters: [] };
    for (const item of titles) {
      if (item.isVolumeHint && current.chapters.length) {
        out.push(current);
        current = { title: item.title, chapters: [] };
      } else if (item.isVolumeHint) {
        current.title = item.title;
      } else {
        current.chapters.push({ title: stripChapterPrefix(item.title), outline: item.outline || null });
      }
    }
    if (current.chapters.length) out.push(current);
  }
  return out;
}

function unescapeJson(s: string) {
  try {
    return JSON.parse(`"${s}"`);
  } catch {
    return s.replace(/\\n/g, "\n").replace(/\\"/g, '"');
  }
}

function stripChapterPrefix(title: string) {
  return title.replace(/^(第[0-9零一二三四五六七八九十百千两]+[章回节]|Chapter\s*\d+)\s*[:：.、\-—\s]*/i, "").trim() || title.trim();
}

function parseLineCatalog(text: string): LiveVolume[] {
  const groups: LiveVolume[] = [{ title: "生成中…", chapters: [] }];
  let current = groups[0];
  const volRe = /^(?:卷[:：]\s*(.+)|(第[0-9零一二三四五六七八九十百千两]+[卷部])\s*[:：]?\s*(.*))$/;
  const chapterRe = /^(第[0-9零一二三四五六七八九十百千两]+[章回节]|Chapter\s*\d+)/i;
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.trim().replace(/^#+\s*/, "").replace(/^[-*+]\s+/, "");
    if (!line || line.startsWith("```")) continue;
    const vm = line.match(volRe);
    if (vm && !line.includes("|")) {
      const title = (vm[1] || `${vm[2]}${vm[3] ? " " + vm[3] : ""}`).trim();
      if (!title) continue;
      current = { title, chapters: [] };
      groups.push(current);
      continue;
    }
    const isHeading = chapterRe.test(line) || line.includes("|");
    if (!isHeading && line.length > 40) continue;
    const [titlePart, ...rest] = line.split("|");
    const title = stripChapterPrefix(titlePart.trim());
    if (!title) continue;
    if (!isHeading && !chapterRe.test(titlePart) && !rest.length) continue;
    current.chapters.push({ title, outline: rest[0]?.trim() || null });
  }
  return groups.filter((g) => g.chapters.length);
}
