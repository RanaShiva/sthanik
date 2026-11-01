"""Agent Skills (https://agentskills.io): folders with a SKILL.md (YAML frontmatter + instructions).

A skill is *text the model reads*, never code the app runs: bundled scripts are stored but nothing here
executes them. Installing from GitHub fetches text files only, with size and count limits, from a fixed pair
of hosts (api.github.com / raw.githubusercontent.com).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import httpx

import paths
import yaml

import outbound

ROOT = paths.HOME  # user data, models and binaries; see paths.py
SKILLS_DIR = ROOT / "skills"
STATE_FILE = ROOT / "config" / "skills.json"
BUILTIN_DIR = paths.BUILTIN_SKILLS  # skills that ship with the app

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
REPO_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".toml", ".py", ".js", ".ts", ".sh"}
MAX_FILE = 300_000
MAX_SKILL_BYTES = 2_000_000
MAX_FILES = 60
UA = "LFM3-Lab/1.0"


class SkillError(Exception):
    """A problem the user should see (bad name, GitHub error, missing file...)."""


def parse_skill_md(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise SkillError("SKILL.md must start with a --- YAML frontmatter block.")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"Invalid frontmatter: {e}") from e
    if not isinstance(meta, dict) or not isinstance(meta.get("name"), str) or not isinstance(meta.get("description"), str):
        raise SkillError("Frontmatter needs string 'name' and 'description'.")
    if not NAME_RE.match(meta["name"]):
        raise SkillError(f"Invalid skill name '{meta['name']}' (lowercase letters, digits and hyphens, max 64).")
    return meta, m.group(2).strip()


def _safe_rel(rel: str) -> str:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or "\\" in rel or any(part.startswith(".") for part in p.parts) or not rel:
        raise SkillError(f"Unsafe path: {rel}")
    return rel


class SkillStore:
    def __init__(self):
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.state = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            self.state = {}
        self.seed_builtin()

    # -- state
    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2) + "\n")
        tmp.replace(STATE_FILE)

    # -- built-in skills: copied into skills/ on start-up, refreshed when the bundled version is newer, and never
    #    resurrected after the user removes them (until they ask to restore).
    def seed_builtin(self):
        removed = set(self.state.get("removed", []))
        for src in sorted(BUILTIN_DIR.glob("*/SKILL.md")):
            try:
                meta, _ = parse_skill_md(src.read_text(encoding="utf-8"))
            except (SkillError, OSError, UnicodeDecodeError) as e:
                print(f"[skills] built-in '{src.parent.name}' could not be loaded: {e}", file=sys.stderr)
                continue
            name = meta["name"]
            if name in removed:
                continue
            md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
            version = int(md.get("version", 1))
            dest = SKILLS_DIR / name
            if dest.exists():
                try:
                    info = json.loads((dest / ".source.json").read_text())
                except (OSError, ValueError):
                    info = {}
                if info.get("type") != "builtin" or int(info.get("version", 0)) >= version:
                    continue  # the user's own skill with that name, or already current
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / "SKILL.md")
            (dest / ".source.json").write_text(json.dumps({"type": "builtin", "version": version}))

    def restore_builtin(self):
        self.state["removed"] = []
        self._save_state()
        self.seed_builtin()

    def is_enabled(self, name: str) -> bool:
        return self.state.get("enabled", {}).get(name, True)

    def set_enabled(self, name: str, on: bool):
        self._dir(name)
        self.state.setdefault("enabled", {})[name] = bool(on)
        self._save_state()

    # -- reading
    def _dir(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise SkillError("Unknown skill.")
        d = SKILLS_DIR / name
        if not (d / "SKILL.md").is_file():
            raise SkillError(f"Skill '{name}' is not installed.")
        return d

    def _files(self, d: Path) -> list[str]:
        return sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and not p.name.startswith(".") and p.name != "SKILL.md")

    def _summary(self, d: Path) -> dict | None:
        try:
            meta, body = parse_skill_md((d / "SKILL.md").read_text(encoding="utf-8"))
        except (SkillError, OSError, UnicodeDecodeError):
            return None
        try:
            source = json.loads((d / ".source.json").read_text())
        except (OSError, ValueError):
            source = {"type": "local"}
        md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
        return {"name": meta["name"], "title": str(md.get("title") or meta["name"]), "icon": str(md.get("icon") or "book"),
                "example": md.get("example"), "tools": [t for t in md.get("tools", []) if isinstance(t, str)] if isinstance(md.get("tools"), list) else [],
                "triggers": [t.strip().lower() for t in md.get("triggers", []) if isinstance(t, str) and t.strip()] if isinstance(md.get("triggers"), list) else [],
                "description": " ".join(meta["description"].split()), "license": meta.get("license"),
                "source": source, "enabled": self.is_enabled(meta["name"]), "files": self._files(d),
                "tokens": max(1, len(body) // 4), "custom": source.get("type") == "custom", "builtin": source.get("type") == "builtin"}

    def list(self) -> list[dict]:
        out = [s for d in sorted(SKILLS_DIR.iterdir()) if d.is_dir() and (s := self._summary(d))]
        return out

    def enabled(self) -> list[dict]:
        return [s for s in self.list() if s["enabled"]]

    def get(self, name: str) -> dict:
        d = self._dir(name)
        meta, body = parse_skill_md((d / "SKILL.md").read_text(encoding="utf-8"))
        return {**self._summary(d), "body": body, "meta": meta}

    def read_file(self, name: str, rel: str, limit: int = 30000) -> str:
        d = self._dir(name)
        p = (d / _safe_rel(rel)).resolve()
        if d.resolve() not in p.parents or not p.is_file():
            raise SkillError(f"No such file in skill '{name}': {rel}")
        data = p.read_bytes()[:MAX_FILE]
        if b"\x00" in data[:2048]:
            raise SkillError("Binary file.")
        text = data.decode("utf-8", errors="replace")
        return text if len(text) <= limit else text[:limit] + "\n…[truncated]"

    # -- writing
    def save_custom(self, name: str, description: str, body: str, triggers: list | None = None):
        name = (name or "").strip().lower()
        if not NAME_RE.match(name):
            raise SkillError("Name: lowercase letters, digits and hyphens only (max 64).")
        description = " ".join((description or "").split())
        if not description or not body.strip():
            raise SkillError("A skill needs a description and instructions.")
        if len(description) > 1024:
            raise SkillError("Description is limited to 1024 characters.")
        d = SKILLS_DIR / name
        if d.exists() and json.loads((d / ".source.json").read_text() if (d / ".source.json").exists() else "{}").get("type") != "custom":
            raise SkillError(f"'{name}' is an installed skill; choose another name.")
        d.mkdir(parents=True, exist_ok=True)
        meta: dict = {"name": name, "description": description}
        words = [w.strip().lower() for w in (triggers or []) if isinstance(w, str) and w.strip()][:20]
        if words:
            meta["metadata"] = {"triggers": words}
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=100)
        (d / "SKILL.md").write_text(f"---\n{front}---\n\n{body.strip()}\n", encoding="utf-8")
        (d / ".source.json").write_text(json.dumps({"type": "custom", "created": time.time()}))

    def delete(self, name: str):
        d = self._dir(name)
        try:
            builtin = json.loads((d / ".source.json").read_text()).get("type") == "builtin"
        except (OSError, ValueError):
            builtin = False
        shutil.rmtree(d)
        self.state.get("enabled", {}).pop(name, None)
        if builtin:  # stay removed across restarts until the user restores the built-ins
            self.state["removed"] = sorted(set(self.state.get("removed", [])) | {name})
        self._save_state()

    async def install_github(self, repo: str, path: str = "", ref: str = "", overwrite: bool = False) -> list[str]:
        repo = (repo or "").strip()
        m = re.match(r"^(?:https?://github\.com/)?([^/\s]+)/([^/\s#?]+?)(?:\.git)?(?:/tree/([^/\s]+)(?:/(.*))?)?/?$", repo)
        if not m:
            raise SkillError("Enter a GitHub repository like owner/name.")
        owner, name_, ref_in_url, path_in_url = m.groups()
        if not (REPO_PART.match(owner) and REPO_PART.match(name_)):
            raise SkillError("That doesn't look like a valid GitHub repository.")
        ref = (ref or ref_in_url or "HEAD").strip()
        prefix = _safe_rel((path or path_in_url or "").strip("/")) if (path or path_in_url) else ""
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,120}", ref):
            raise SkillError("Invalid branch or tag name.")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), headers=headers, follow_redirects=False, proxy=outbound.proxy()) as c:
            r = await c.get(f"https://api.github.com/repos/{owner}/{name_}/git/trees/{quote(ref, safe='/')}?recursive=1")
            if r.status_code == 404:
                raise SkillError("Repository or branch not found (private repositories aren't supported).")
            if r.status_code == 403:
                raise SkillError("GitHub rate limit reached; try again in a few minutes.")
            r.raise_for_status()
            tree = r.json()
            if tree.get("truncated"):
                raise SkillError("That repository is too large to scan.")
            blobs = {t["path"]: t.get("size", 0) for t in tree["tree"] if t["type"] == "blob"}
            roots = sorted(p.rsplit("/", 1)[0] if "/" in p else "" for p in blobs if (p == "SKILL.md" or p.endswith("/SKILL.md")) and p.startswith(prefix))
            if not roots:
                raise SkillError("No SKILL.md files found there.")
            installed: list[str] = []
            staging = Path(tempfile.mkdtemp(prefix="lfm3-skill-"))
            try:
                for root in roots:
                    pre = f"{root}/" if root else ""
                    files = [p for p in blobs if p.startswith(pre) and (p == f"{pre}SKILL.md" or Path(p).suffix.lower() in TEXT_EXT)
                             and blobs[p] <= MAX_FILE and not any(seg.startswith(".") for seg in p.split("/"))
                             and not any(o != root and p.startswith(f"{o}/") and o.startswith(pre) and o != "" for o in roots)]
                    if f"{pre}SKILL.md" not in files or len(files) > MAX_FILES or sum(blobs[p] for p in files) > MAX_SKILL_BYTES:
                        raise SkillError(f"Skill at '{root or '/'}' is missing SKILL.md or too large.")
                    texts = {}
                    for p in files:
                        raw = await c.get(f"https://raw.githubusercontent.com/{owner}/{name_}/{quote(ref, safe='/')}/{quote(p)}")
                        raw.raise_for_status()
                        texts[p] = raw.text
                    meta, _ = parse_skill_md(texts[f"{pre}SKILL.md"])
                    dest = staging / meta["name"]
                    for p, text in texts.items():
                        out = dest / _safe_rel(p[len(pre):])
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_text(text, encoding="utf-8")
                    (dest / ".source.json").write_text(json.dumps({"type": "github", "repo": f"{owner}/{name_}", "ref": ref, "path": root, "installed": time.time()}))
                    installed.append(meta["name"])
                if not overwrite:
                    clash = [n for n in installed if (SKILLS_DIR / n).exists()]
                    if clash:
                        raise SkillError(f"Already installed: {', '.join(clash)}. Remove it first or choose 'replace'.")
                for n in installed:
                    target = SKILLS_DIR / n
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.move(str(staging / n), str(target))
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return installed

    def match(self, text: str, exclude=()) -> list[dict]:
        """Enabled skills whose trigger words appear in `text` (whole words / phrases, case-insensitive), with their full text.

        Small models rarely decide to load a playbook on their own, so a matching skill is put in front of them directly.
        """
        low = (text or "").lower()
        out = []
        for s in self.enabled():
            if s["name"] in exclude:
                continue
            if any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low) for t in s["triggers"]):
                out.append({"name": s["name"], "title": s["title"], "body": self.get(s["name"])["body"]})
        return out

    def writing_guidance(self, names: list[str], limit: int = 6500) -> str:
        """The style rules of the named skills for a step-by-step writer that has no tools (plan mode). Only the rules sections are kept:
        layouts, storylines and workflows describe a whole file, and a writer that is making ONE section of it would copy them into its section."""
        enabled = {s["name"] for s in self.enabled()}
        keep_heading = re.compile(r"^#{2,3}\s*(quality rules|rules|supported markdown)\b", re.I)
        out = []
        for n in names:
            if n not in enabled:
                continue
            keep, take, fenced = [], False, False
            for line in self.get(n)["body"].split("\n"):
                if line.lstrip().startswith("```"):
                    fenced = not fenced
                if not fenced and line.startswith("#"):
                    take = bool(keep_heading.match(line))
                    continue
                if not take or re.search(r"create_document|create_slides|create_spreadsheet|edit_document|illustrate_document|use_skill|find_image|generate_image|the tool|tell the user|reply with|after creating|give the slide count", line, re.I):
                    continue
                keep.append(line)
            text = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
            if text:
                out.append(f"Style rules from the “{n}” skill:\n{text}")
        return "\n\n".join(out)[:limit]

    def catalog_text(self, limit: int = 280) -> str:
        return "\n".join(f"- {s['name']}: {s['description'][:limit]}" for s in self.enabled())
