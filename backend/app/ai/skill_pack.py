"""文件夹型 Skill 包:落盘到固定目录,对话时组装全部文本文件。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..config import settings
from ..models import Skill

# 参与注入的文本扩展名
TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".xml",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".sh",
    ".bat",
    ".ps1",
    ".sql",
    ".ini",
    ".cfg",
    ".conf",
    ".env.example",
}
SKIP_DIR_NAMES = {"__macosx", ".git", "node_modules", ".svn", "__pycache__"}
MAX_FILE_BYTES = 400_000
MAX_TOTAL_BYTES = 1_200_000
MAX_FILES = 80


def skills_root() -> Path:
    root = Path(settings.skills_root).expanduser()
    if not root.is_absolute():
        # 相对路径相对 backend 工作目录
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def pack_path_for(user_id: int, skill_id: int) -> Path:
    return skills_root() / str(user_id) / str(skill_id)


def relative_pack_dir(user_id: int, skill_id: int) -> str:
    return f"{user_id}/{skill_id}"


def _safe_rel_path(rel: str) -> str | None:
    p = rel.replace("\\", "/").strip("/")
    if not p or p.startswith("../") or "/../" in f"/{p}/" or p.startswith("/") or ":" in p:
        return None
    parts = [x for x in p.split("/") if x and x not in (".",)]
    if any(x.lower() in SKIP_DIR_NAMES for x in parts):
        return None
    if any(x.startswith(".") and x not in (".env.example",) for x in parts[:-1]):
        return None
    return "/".join(parts)


def write_pack_files(user_id: int, skill_id: int, files: list[tuple[str, bytes]]) -> list[str]:
    """写入技能包目录,返回写入的相对路径列表。files: (relative_path, content)."""
    base = pack_path_for(user_id, skill_id)
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel, raw in files:
        safe = _safe_rel_path(rel)
        if not safe:
            continue
        if len(raw) > MAX_FILE_BYTES:
            continue
        dest = base / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        written.append(safe)
        if len(written) >= MAX_FILES:
            break
    return written


def list_pack_files(skill: Skill) -> list[dict]:
    if not skill.pack_dir:
        return []
    base = skills_root() / skill.pack_dir
    if not base.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base).as_posix()
        out.append({"path": rel, "size": p.stat().st_size, "text": _is_text_file(p)})
    return out


def _is_text_file(path: Path) -> bool:
    name = path.name.lower()
    if name == "skill.md" or name.endswith(".md"):
        return True
    suf = path.suffix.lower()
    if suf in TEXT_SUFFIXES:
        return True
    # 无后缀小文件尝试当文本
    return suf == "" and path.stat().st_size < 64_000


def _read_text(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_FILE_BYTES:
        raw = raw[:MAX_FILE_BYTES]
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def build_skill_context(skill: Skill, *, max_total: int = MAX_TOTAL_BYTES) -> str:
    """组装注入 AI 的技能上下文:优先整包文件,否则回落 system_prompt。"""
    parts: list[str] = [f"## 技能包「{skill.name}」(请整体遵循)"]
    used = 0

    if skill.pack_dir:
        base = skills_root() / skill.pack_dir
        if base.is_dir():
            files = [p for p in sorted(base.rglob("*")) if p.is_file()]
            # SKILL.md 优先
            files.sort(key=lambda p: (0 if p.name.lower() == "skill.md" else 1, p.as_posix().lower()))
            for p in files[:MAX_FILES]:
                rel = p.relative_to(base).as_posix()
                if not _is_text_file(p):
                    parts.append(f"### 附件(二进制跳过): {rel}")
                    continue
                text = _read_text(p)
                if text is None:
                    continue
                chunk = f"### 文件: {rel}\n{text.strip()}"
                if used + len(chunk) > max_total:
                    remain = max_total - used
                    if remain > 200:
                        parts.append(chunk[:remain] + "\n…(截断)")
                        used = max_total
                    parts.append("…(技能包其余文件因长度限制未全部注入)")
                    break
                parts.append(chunk)
                used += len(chunk)
            if len(parts) > 1:
                return "\n\n".join(parts)

    prompt = (skill.system_prompt or "").strip()
    if prompt:
        parts.append(prompt)
    return "\n\n".join(parts)


def delete_pack(skill: Skill) -> None:
    if not skill.pack_dir:
        return
    base = skills_root() / skill.pack_dir
    if base.is_dir():
        shutil.rmtree(base, ignore_errors=True)


def extract_entry_prompt(files: list[tuple[str, bytes]]) -> str:
    """从包内找 SKILL.md 作为入口提示;没有则拼接短摘要。"""
    by_lower = {rel.replace("\\", "/").lower(): (rel, raw) for rel, raw in files}
    for key in sorted(by_lower.keys(), key=lambda k: (0 if k.endswith("skill.md") else 1, k)):
        if key.endswith("skill.md") or key.endswith("skill.markdown"):
            rel, raw = by_lower[key]
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")
    # 无 SKILL.md:用第一个 md/txt
    for rel, raw in files:
        lower = rel.replace("\\", "/").lower()
        if lower.endswith((".md", ".markdown", ".txt")):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
            return text[:8000]
    names = [rel.replace("\\", "/") for rel, _ in files[:20]]
    return "本技能为文件夹技能包,包含文件:\n" + "\n".join(f"- {n}" for n in names)


def group_zip_or_folder_into_packs(
    files: list[tuple[str, bytes]],
) -> list[tuple[str, list[tuple[str, bytes]]]]:
    """
    将扁平相对路径文件分成多个技能包。
    - 若存在多个顶级目录且各自含 SKILL.md → 每个目录一个包
    - 否则整个上传视为一个包(去掉公共根目录前缀)
    返回 [(pack_name, [(rel_inside_pack, bytes), ...]), ...]
    """
    normalized: list[tuple[str, bytes]] = []
    for rel, raw in files:
        safe = _safe_rel_path(rel)
        if safe:
            normalized.append((safe, raw))
    if not normalized:
        return []

    tops = {p.split("/", 1)[0] for p, _ in normalized if "/" in p}
    # 多顶级目录且至少两个带 skill.md
    skill_tops = set()
    for p, _ in normalized:
        parts = p.split("/")
        if len(parts) >= 2 and parts[-1].lower() in ("skill.md", "skill.markdown"):
            skill_tops.add(parts[0])
    if len(tops) >= 2 and len(skill_tops) >= 2:
        packs: list[tuple[str, list[tuple[str, bytes]]]] = []
        for top in sorted(skill_tops):
            inner = []
            for p, raw in normalized:
                if p == top or p.startswith(top + "/"):
                    rel = p[len(top) + 1 :] if p.startswith(top + "/") else parts_name(top)
                    if not rel:
                        rel = "SKILL.md" if p.lower().endswith("skill.md") else p
                    inner.append((rel, raw))
            if inner:
                packs.append((top, inner))
        return packs

    # 单根目录:剥掉公共前缀
    common = _common_root_dir([p for p, _ in normalized])
    if common:
        name = common.rstrip("/").split("/")[-1]
        inner = []
        for p, raw in normalized:
            if p.startswith(common):
                rel = p[len(common) :]
                inner.append((rel or Path(p).name, raw))
            elif p == common.rstrip("/"):
                continue
            else:
                inner.append((p, raw))
        return [(name, inner)]

    # 扁平
    hint = "imported-skill"
    for p, _ in normalized:
        if p.lower() in ("skill.md", "skill.markdown"):
            hint = "skill-pack"
            break
        if "/" not in p and p.lower().endswith((".md", ".txt")):
            hint = Path(p).stem
            break
    return [(hint, normalized)]


def parts_name(top: str) -> str:
    return top


def _common_root_dir(paths: list[str]) -> str | None:
    if not paths:
        return None
    split = [p.split("/") for p in paths]
    if all(len(s) == 1 for s in split):
        return None
    # 若所有路径共享同一顶级目录
    tops = {s[0] for s in split if s}
    if len(tops) == 1:
        top = next(iter(tops))
        # 必须至少有一个在子路径下
        if any(len(s) > 1 for s in split):
            return top + "/"
    return None


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "-", name.strip(), flags=re.U)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s or "skill")[:48]
