"""Persistent conversations: data/chats/<id>.json plus data/chats/<id>/media/* for attachments.

The browser owns the conversation and PUTs the whole thing after each turn. Attachments arrive as base64 once and are
stored as files (later saves refer to them by name), so a conversation file stays small even with video in it.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import time
from pathlib import Path

import paths

ROOT = paths.HOME  # user data, models and binaries; see paths.py
CHATS_DIR = Path(os.environ.get("LFM3_CHATS_DIR") or ROOT / "data" / "chats")

SCOPES = ("text", "vl", "omni")
ID_RE = re.compile(r"^[a-z0-9]{8,32}$")
FILE_RE = re.compile(r"^[a-z0-9]{6,40}\.[a-z0-9]{2,5}$")
MAX_BYTES = 220_000_000
# Only these are ever served back, each with a fixed safe type (no SVG/HTML: nothing here may run script on our origin).
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
               "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "mkv": "video/x-matroska",
               "wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac", "m4a": "audio/mp4"}
MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp", "video/mp4": "mp4",
            "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv", "audio/wav": "wav", "audio/x-wav": "wav",
            "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/flac": "flac", "audio/mp4": "m4a"}
KIND_DEFAULT = {"image": "png", "video": "mp4", "audio": "wav"}


class ChatError(Exception):
    """Invalid request (bad id, wrong shape, missing chat)."""


def _check_id(cid: str) -> str:
    if not ID_RE.match(cid or ""):
        raise ChatError("Invalid conversation id.")
    return cid


def _atomic_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


def _media_ref(chat_id: str, m: dict) -> dict:
    return {**{k: v for k, v in m.items() if k not in ("b64", "url")}, "url": f"/api/chats/{chat_id}/media/{m['file']}"} if m.get("file") else m


def _store_media(chat_id: str, history: list, items: list) -> dict:
    """Move base64 attachments out of the JSON into files; returns {media id: file name} for the client."""
    folder = CHATS_DIR / chat_id / "media"
    saved: dict[str, str] = {}
    for msg in history:
        for m in (msg.get("media") or []) if isinstance(msg, dict) else []:
            if not isinstance(m, dict):
                continue
            mid = re.sub(r"[^a-z0-9]", "", str(m.get("id", "")).lower())[:24]
            if not mid:
                continue
            if m.get("b64") and not m.get("file"):
                ext = MIME_EXT.get(str(m.get("mime", "")).lower()) or KIND_DEFAULT.get(m.get("kind"), "bin")
                if ext not in MEDIA_TYPES:
                    ext = KIND_DEFAULT.get(m.get("kind"), "png")
                try:
                    raw = base64.b64decode(m["b64"], validate=False)
                except (binascii.Error, ValueError):
                    raise ChatError("An attachment could not be decoded.")
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{mid}.{ext}").write_bytes(raw)
                m["file"] = f"{mid}.{ext}"
            if m.get("file"):
                if not FILE_RE.match(str(m["file"])):
                    raise ChatError("Invalid attachment reference.")
                saved[mid] = m["file"]
            m.pop("b64", None)
            m.pop("url", None)
    for it in items:
        for m in (it.get("media") or []) if isinstance(it, dict) else []:
            if isinstance(m, dict):
                mid = re.sub(r"[^a-z0-9]", "", str(m.get("id", "")).lower())[:24]
                if mid in saved:
                    m["file"] = saved[mid]
                m.pop("b64", None)
                m.pop("url", None)
    return saved


def _active(a) -> dict | None:
    """The document a conversation is working on: just a file name, title and format."""
    if not isinstance(a, dict) or not isinstance(a.get("name"), str) or not 3 <= len(a["name"]) <= 200:
        return None
    return {"name": a["name"], "title": str(a.get("title") or "")[:120], "format": str(a.get("format") or "")[:8]}


def _compaction(c) -> dict | None:
    """Where a conversation was summarized to fit the model's window: history[:upto] is replaced by `summary` in what the model sees."""
    if not isinstance(c, dict) or not isinstance(c.get("summary"), str) or not isinstance(c.get("upto"), int) or c["upto"] < 1:
        return None
    at = c.get("itemsAt")
    return {"upto": c["upto"], "summary": c["summary"][:8000], "itemsAt": at if isinstance(at, int) and at >= 0 else None,
            "at": int(c["at"]) if isinstance(c.get("at"), (int, float)) else 0}


def _project(name) -> str:
    """A project is a name that conversations share. Names that differ only in case are the same project."""
    return " ".join(str(name or "").split())[:60]


PROJECTS_FILE = "_projects.json"  # beside the conversations, so a private session (which moves CHATS_DIR) gets its own list


def _chat_files():
    """Conversation files only: ids are lowercase letters and digits, which keeps _projects.json (and any stray .tmp) out."""
    if not CHATS_DIR.exists():
        return []
    return [p for p in CHATS_DIR.glob("*.json") if ID_RE.match(p.stem)]


def _saved_projects() -> list[str]:
    try:
        d = json.loads((CHATS_DIR / PROJECTS_FILE).read_text())
        return [n for n in (_project(x) for x in d.get("projects", [])) if n] if isinstance(d, dict) else []
    except (OSError, ValueError):
        return []


def _save_projects(names: list[str]):
    seen, out = set(), []
    for n in names:
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            out.append(n)
    _atomic_write(CHATS_DIR / PROJECTS_FILE, {"projects": out})


def _canonical(name: str, known: list[str]) -> str:
    """Reuse the spelling an existing project already has ("work" joins "Work")."""
    return next((k for k in known if k.casefold() == name.casefold()), name)


def projects() -> list[str]:
    """Every project: the ones created on their own (kept even while empty) in their saved order, then any a conversation names."""
    names = _saved_projects()
    for p in _chat_files():
        try:
            n = _project(json.loads(p.read_text()).get("project"))
        except (OSError, ValueError):
            continue
        if n and n.casefold() not in {x.casefold() for x in names}:
            names.append(n)
    return names


def create_project(name: str) -> str:
    name = _project(name)
    if not name:
        raise ChatError("Give the project a name.")
    known = projects()
    name = _canonical(name, known)
    if name not in known:
        _save_projects([*_saved_projects(), name])
    return name


def _decorate(chat_id: str, chat: dict) -> dict:
    """Add display URLs to every attachment reference."""
    for msg in chat.get("history", []):
        msg["media"] = [_media_ref(chat_id, m) for m in msg.get("media") or []] if msg.get("media") else msg.get("media")
    for it in chat.get("items", []):
        if it.get("media"):
            it["media"] = [_media_ref(chat_id, m) for m in it["media"]]
    return chat


def save(chat_id: str, data: dict) -> dict:
    _check_id(chat_id)
    scope = data.get("scope")
    if scope not in SCOPES:
        raise ChatError("Unknown conversation type.")
    history, items = data.get("history"), data.get("items")
    if not isinstance(history, list) or not isinstance(items, list):
        raise ChatError("Malformed conversation.")
    path = CHATS_DIR / f"{chat_id}.json"
    prev = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text())
        except ValueError:
            prev = {}
    saved = _store_media(chat_id, history, items)
    now = time.time()
    chat = {"id": chat_id, "scope": scope, "title": str(data.get("title") or prev.get("title") or "New chat")[:120],
            "created": prev.get("created", now), "updated": now, "history": history, "items": items,
            "active": _active(data.get("active")), "compaction": _compaction(data.get("compaction")),
            "project": _canonical(_project(data["project"]), _saved_projects()) if "project" in data else _project(prev.get("project"))}
    _atomic_write(path, chat)
    return {"id": chat_id, "title": chat["title"], "updated": now, "media": saved}


def get(chat_id: str) -> dict:
    _check_id(chat_id)
    path = CHATS_DIR / f"{chat_id}.json"
    if not path.is_file():
        raise ChatError("No such conversation.")
    return _decorate(chat_id, json.loads(path.read_text()))


def list_chats(scope: str | None = None) -> list[dict]:
    out = []
    for p in _chat_files():
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if scope and c.get("scope") != scope:
            continue
        turns = sum(1 for it in c.get("items", []) if it.get("t") == "u")
        out.append({"id": c["id"], "scope": c.get("scope"), "title": c.get("title", "New chat"), "created": c.get("created"),
                    "updated": c.get("updated"), "turns": turns, "project": _project(c.get("project"))})
    return sorted(out, key=lambda c: c.get("updated") or 0, reverse=True)[:500]


def set_project(chat_id: str, project: str) -> dict:
    _check_id(chat_id)
    path = CHATS_DIR / f"{chat_id}.json"
    if not path.is_file():
        raise ChatError("No such conversation.")
    c = json.loads(path.read_text())
    c["project"] = create_project(project) if _project(project) else ""
    _atomic_write(path, c)  # moving a chat is not activity in it: "updated" stays, so the list order does not jump
    return {"id": chat_id, "project": c["project"]}


def rename_project(old: str, new: str) -> int:
    """Rename a project (or, with an empty new name, dissolve it: its conversations stay, ungrouped). Returns how many were changed."""
    old, new = _project(old), _project(new)
    if not old:
        raise ChatError("Choose a project.")
    known = projects()
    old = _canonical(old, known)
    new = _canonical(new, [k for k in known if k != old]) if new else ""
    n = 0
    for p in _chat_files():
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if _project(c.get("project")).casefold() == old.casefold():
            c["project"] = new
            _atomic_write(p, c)
            n += 1
    # keep the project's place in the saved order (a rename into an existing project merges the two)
    saved = _saved_projects()
    _save_projects([new if x.casefold() == old.casefold() else x for x in saved] if new else [x for x in saved if x.casefold() != old.casefold()])
    return n


def rename(chat_id: str, title: str) -> dict:
    _check_id(chat_id)
    path = CHATS_DIR / f"{chat_id}.json"
    if not path.is_file():
        raise ChatError("No such conversation.")
    c = json.loads(path.read_text())
    c["title"] = " ".join(title.split())[:120] or c.get("title", "New chat")
    _atomic_write(path, c)
    return {"id": chat_id, "title": c["title"]}


def delete(chat_id: str):
    _check_id(chat_id)
    path = CHATS_DIR / f"{chat_id}.json"
    if not path.is_file():
        raise ChatError("No such conversation.")
    path.unlink()
    shutil.rmtree(CHATS_DIR / chat_id, ignore_errors=True)


def media_path(chat_id: str, name: str) -> tuple[Path, str]:
    _check_id(chat_id)
    if not FILE_RE.match(name or ""):
        raise ChatError("No such file.")
    p = (CHATS_DIR / chat_id / "media" / name).resolve()
    if p.parent != (CHATS_DIR / chat_id / "media").resolve() or not p.is_file():
        raise ChatError("No such file.")
    ext = p.suffix.lstrip(".")
    if ext not in MEDIA_TYPES:
        raise ChatError("No such file.")
    return p, MEDIA_TYPES[ext]
