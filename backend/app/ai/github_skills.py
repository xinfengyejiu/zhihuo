"""从 GitHub 地址拉取 SKILL.md 并解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx
from fastapi import HTTPException

from ..config import settings

MAX_SKILLS_PER_INSTALL = 30
MAX_FILE_BYTES = 512 * 1024


@dataclass
class GithubTarget:
    owner: str
    repo: str
    ref: str | None
    path: str  # 目录或文件相对路径,可为空


@dataclass
class FetchedSkillMd:
    path: str
    markdown: str
    name_hint: str | None


def parse_github_url(url: str) -> GithubTarget:
    raw = (url or "").strip()
    if not raw:
        raise HTTPException(400, "请填写 GitHub 地址")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = unquote(parsed.path or "").strip("/")

    if host == "raw.githubusercontent.com":
        parts = path.split("/")
        if len(parts) < 4:
            raise HTTPException(400, "raw.githubusercontent.com 地址格式不正确")
        owner, repo, ref = parts[0], parts[1], parts[2]
        file_path = "/".join(parts[3:])
        return GithubTarget(owner=owner, repo=repo.removesuffix(".git"), ref=ref, path=file_path)

    if host not in ("github.com", "www.github.com"):
        raise HTTPException(400, "仅支持 github.com / raw.githubusercontent.com 地址")

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise HTTPException(400, "请提供完整仓库地址,如 https://github.com/owner/repo")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    ref: str | None = None
    sub = ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        ref = parts[3]
        sub = "/".join(parts[4:])
    elif len(parts) > 2 and parts[2] not in ("tree", "blob", "releases", "issues", "pull"):
        # github.com/owner/repo/path — 少见,当作默认分支下路径
        sub = "/".join(parts[2:])
    return GithubTarget(owner=owner, repo=repo, ref=ref, path=sub)


def _gh_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xiaohuo-skills-installer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (getattr(settings, "github_token", None) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _resolve_default_ref(owner: str, repo: str) -> str:
    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        resp = httpx.get(api, headers=_gh_headers(), timeout=20, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"访问 GitHub 失败:{e}") from e
    if resp.status_code == 404:
        raise HTTPException(404, "仓库不存在或无权访问(私有库需配置 GITHUB_TOKEN)")
    if resp.status_code == 403:
        raise HTTPException(403, "GitHub API 限流或无权限,可在后端配置 GITHUB_TOKEN")
    if resp.status_code >= 400:
        raise HTTPException(400, f"读取仓库信息失败:HTTP {resp.status_code}")
    data = resp.json()
    return data.get("default_branch") or "main"


def _fetch_raw(owner: str, repo: str, ref: str, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    try:
        resp = httpx.get(url, headers={"User-Agent": "xiaohuo-skills-installer"}, timeout=30, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"下载文件失败:{e}") from e
    if resp.status_code != 200:
        raise HTTPException(400, f"无法下载 {path}(HTTP {resp.status_code})")
    if len(resp.content) > MAX_FILE_BYTES:
        raise HTTPException(400, f"文件过大:{path}")
    return resp.text


def _list_skill_md_paths(owner: str, repo: str, ref: str, prefix: str) -> list[str]:
    """用 git trees API 递归查找 SKILL.md。"""
    api = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
    try:
        resp = httpx.get(api, headers=_gh_headers(), timeout=40, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"列举仓库文件失败:{e}") from e
    if resp.status_code == 404:
        # ref 可能是 commit/tag 以外的短名失败时,再试 contents
        return _list_via_contents(owner, repo, ref, prefix)
    if resp.status_code == 403:
        raise HTTPException(403, "GitHub API 限流或无权限,可在后端配置 GITHUB_TOKEN")
    if resp.status_code >= 400:
        raise HTTPException(400, f"列举仓库失败:HTTP {resp.status_code}")
    tree = resp.json().get("tree") or []
    prefix = prefix.strip("/")
    out: list[str] = []
    for item in tree:
        if item.get("type") != "blob":
            continue
        p = item.get("path") or ""
        base = p.rsplit("/", 1)[-1]
        if base.lower() not in ("skill.md", "skill.markdown"):
            continue
        if prefix and not (p == prefix or p.startswith(prefix + "/")):
            continue
        out.append(p)
    return out


def _list_via_contents(owner: str, repo: str, ref: str, prefix: str) -> list[str]:
    """浅层 contents API 兜底(非递归,只扫一层子目录)。"""
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{prefix}" if prefix else f"https://api.github.com/repos/{owner}/{repo}/contents"
    params = {"ref": ref}
    try:
        resp = httpx.get(api, headers=_gh_headers(), params=params, timeout=30, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"读取目录失败:{e}") from e
    if resp.status_code >= 400:
        raise HTTPException(400, f"读取目录失败:HTTP {resp.status_code}")
    data = resp.json()
    if isinstance(data, dict) and data.get("type") == "file":
        name = (data.get("name") or "").lower()
        if name in ("skill.md", "skill.markdown") or name.endswith(".md"):
            return [data.get("path") or prefix]
        return []
    if not isinstance(data, list):
        return []
    found: list[str] = []
    for item in data:
        if item.get("type") == "file":
            name = (item.get("name") or "").lower()
            if name in ("skill.md", "skill.markdown"):
                found.append(item.get("path") or "")
        elif item.get("type") == "dir":
            sub = item.get("path") or ""
            sub_api = f"https://api.github.com/repos/{owner}/{repo}/contents/{sub}"
            try:
                sub_resp = httpx.get(sub_api, headers=_gh_headers(), params=params, timeout=20, follow_redirects=True)
            except httpx.HTTPError:
                continue
            if sub_resp.status_code != 200:
                continue
            for child in sub_resp.json() if isinstance(sub_resp.json(), list) else []:
                if child.get("type") == "file" and (child.get("name") or "").lower() in ("skill.md", "skill.markdown"):
                    found.append(child.get("path") or "")
    return [p for p in found if p]


def peek_frontmatter(markdown: str) -> dict[str, str]:
    text = markdown.strip()
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("---", 3)
    if end == -1:
        return meta
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip().strip("\"'")
        if key:
            meta[key] = val
    return meta


def strip_frontmatter(markdown: str) -> str:
    text = markdown.strip()
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3 :].strip()


def fetch_skill_markdowns(url: str) -> list[FetchedSkillMd]:
    target = parse_github_url(url)
    ref = target.ref or _resolve_default_ref(target.owner, target.repo)
    path = target.path.strip("/")

    # 单文件
    if path and re.search(r"\.(md|markdown|txt)$", path, re.I):
        md = _fetch_raw(target.owner, target.repo, ref, path)
        meta = peek_frontmatter(md)
        hint = meta.get("name") or path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if hint.lower() == "skill":
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            hint = parent.rsplit("/", 1)[-1] if parent else hint
        return [FetchedSkillMd(path=path, markdown=md, name_hint=hint)]

    paths = _list_skill_md_paths(target.owner, target.repo, ref, path)
    if not paths and path:
        # 指定目录下没有 SKILL.md 时,尝试把该路径当文件名补全
        for candidate in (f"{path}/SKILL.md", f"{path}/skill.md", path):
            try:
                md = _fetch_raw(target.owner, target.repo, ref, candidate)
                meta = peek_frontmatter(md)
                hint = meta.get("name") or candidate.rsplit("/", 1)[-1]
                return [FetchedSkillMd(path=candidate, markdown=md, name_hint=hint)]
            except HTTPException:
                continue
    if not paths:
        raise HTTPException(404, "未找到 SKILL.md,请确认仓库路径是否包含技能文件")

    paths = paths[:MAX_SKILLS_PER_INSTALL]
    out: list[FetchedSkillMd] = []
    for p in paths:
        md = _fetch_raw(target.owner, target.repo, ref, p)
        meta = peek_frontmatter(md)
        hint = meta.get("name")
        if not hint:
            parent = p.rsplit("/", 1)[0] if "/" in p else ""
            hint = parent.rsplit("/", 1)[-1] if parent else p
        out.append(FetchedSkillMd(path=p, markdown=md, name_hint=hint))
    return out


@dataclass
class FetchedSkillPack:
    name: str
    files: list[tuple[str, bytes]]  # (rel_path_inside_pack, content)


def _fetch_raw_bytes(owner: str, repo: str, ref: str, path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    try:
        resp = httpx.get(url, headers={"User-Agent": "xiaohuo-skills-installer"}, timeout=30, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"下载文件失败:{e}") from e
    if resp.status_code != 200:
        raise HTTPException(400, f"无法下载 {path}(HTTP {resp.status_code})")
    if len(resp.content) > MAX_FILE_BYTES:
        return resp.content[:MAX_FILE_BYTES]
    return resp.content


def _list_all_blob_paths(owner: str, repo: str, ref: str, prefix: str) -> list[str]:
    api = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
    try:
        resp = httpx.get(api, headers=_gh_headers(), timeout=40, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"列举仓库文件失败:{e}") from e
    if resp.status_code >= 400:
        raise HTTPException(400, f"列举仓库失败:HTTP {resp.status_code}")
    tree = resp.json().get("tree") or []
    prefix = prefix.strip("/")
    out: list[str] = []
    for item in tree:
        if item.get("type") != "blob":
            continue
        p = item.get("path") or ""
        if prefix and not (p == prefix or p.startswith(prefix + "/")):
            continue
        out.append(p)
    return out


def fetch_skill_packs(url: str) -> list[FetchedSkillPack]:
    """拉取完整技能文件夹(以 SKILL.md 所在目录为一包;单文件则仅该文件)。"""
    target = parse_github_url(url)
    ref = target.ref or _resolve_default_ref(target.owner, target.repo)
    path = target.path.strip("/")

    # 指向单个 md 文件
    if path and re.search(r"\.(md|markdown|txt)$", path, re.I):
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent:
            blobs = _list_all_blob_paths(target.owner, target.repo, ref, parent)[:40]
            files: list[tuple[str, bytes]] = []
            for bp in blobs:
                rel = bp[len(parent) + 1 :] if bp.startswith(parent + "/") else bp
                try:
                    files.append((rel, _fetch_raw_bytes(target.owner, target.repo, ref, bp)))
                except HTTPException:
                    continue
            meta = peek_frontmatter(_fetch_raw(target.owner, target.repo, ref, path))
            name = meta.get("name") or parent.rsplit("/", 1)[-1]
            return [FetchedSkillPack(name=name[:64], files=files or [(path.rsplit("/", 1)[-1], _fetch_raw_bytes(target.owner, target.repo, ref, path))])]
        raw = _fetch_raw_bytes(target.owner, target.repo, ref, path)
        md = raw.decode("utf-8", errors="replace")
        meta = peek_frontmatter(md)
        name = meta.get("name") or path.rsplit(".", 1)[0]
        return [FetchedSkillPack(name=name[:64], files=[(path.rsplit("/", 1)[-1], raw)])]

    skill_mds = _list_skill_md_paths(target.owner, target.repo, ref, path)
    if not skill_mds and path:
        # 整个指定目录作为一个包
        blobs = _list_all_blob_paths(target.owner, target.repo, ref, path)[:60]
        if not blobs:
            raise HTTPException(404, "目录下没有可下载的文件")
        files = []
        for bp in blobs:
            rel = bp[len(path) + 1 :] if path and bp.startswith(path + "/") else bp
            try:
                files.append((rel, _fetch_raw_bytes(target.owner, target.repo, ref, bp)))
            except HTTPException:
                continue
        name = path.rsplit("/", 1)[-1] if path else target.repo
        return [FetchedSkillPack(name=name[:64], files=files)]

    if not skill_mds:
        raise HTTPException(404, "未找到 SKILL.md 或技能目录")

    packs: list[FetchedSkillPack] = []
    for sm in skill_mds[:MAX_SKILLS_PER_INSTALL]:
        parent = sm.rsplit("/", 1)[0] if "/" in sm else ""
        prefix = parent or ""
        if prefix:
            blobs = _list_all_blob_paths(target.owner, target.repo, ref, prefix)[:40]
        else:
            blobs = [sm]
        files = []
        for bp in blobs:
            rel = bp[len(prefix) + 1 :] if prefix and bp.startswith(prefix + "/") else bp
            try:
                files.append((rel, _fetch_raw_bytes(target.owner, target.repo, ref, bp)))
            except HTTPException:
                continue
        try:
            md = _fetch_raw(target.owner, target.repo, ref, sm)
            meta = peek_frontmatter(md)
            name = meta.get("name") or (parent.rsplit("/", 1)[-1] if parent else sm)
        except HTTPException:
            name = parent.rsplit("/", 1)[-1] if parent else sm
        packs.append(FetchedSkillPack(name=str(name)[:64], files=files))
    return packs
