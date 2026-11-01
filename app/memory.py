"""Long-term memory: short facts about the user and their work that every conversation can see.

Stored in data/memory.json. Facts are deliberately small (one sentence) and capped, because they are put into every prompt.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

import paths
import safebrowsing

ROOT = paths.HOME  # user data, models and binaries; see paths.py
PATH = Path(os.environ.get("LFM3_MEMORY_FILE") or ROOT / "data" / "memory.json")
MAX_ITEMS = 100
MAX_LEN = 240
PROMPT_CHARS = 1600


class MemoryError_(Exception):
    """Invalid request."""


def _load() -> dict:
    try:
        d = json.loads(PATH.read_text())
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return {"enabled": bool(d.get("enabled", True)), "items": [i for i in d["items"] if isinstance(i, dict) and i.get("text")]}
    except (OSError, ValueError):
        pass
    return {"enabled": True, "items": []}


def _save(d: dict):
    if not safebrowsing.is_safe():
        raise MemoryError_("Nothing is remembered while safe browsing is off: this session leaves no trace on disk.")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    tmp.replace(PATH)


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def snapshot() -> dict:
    d = _load()
    d["items"] = sorted(d["items"], key=lambda i: (not i.get("pinned"), -i.get("updated", 0)))
    return d


def add(text: str, source: str = "user") -> dict:
    text = " ".join((text or "").split())[:MAX_LEN]
    if len(text) < 3:
        raise MemoryError_("Say what to remember.")
    d = _load()
    key = _norm(text)
    for i in d["items"]:
        if _norm(i["text"]) == key:  # already known: just refresh it
            i["updated"] = time.time()
            _save(d)
            return i
    if len(d["items"]) >= MAX_ITEMS:
        raise MemoryError_(f"Memory is full ({MAX_ITEMS} facts). Delete some first.")
    item = {"id": uuid.uuid4().hex[:10], "text": text, "source": source, "created": time.time(), "updated": time.time(), "pinned": False}
    d["items"].append(item)
    _save(d)
    return item


def update(mid: str, text: str | None = None, pinned: bool | None = None) -> dict:
    d = _load()
    for i in d["items"]:
        if i["id"] == mid:
            if text is not None:
                t = " ".join(text.split())[:MAX_LEN]
                if len(t) < 3:
                    raise MemoryError_("The fact cannot be empty.")
                i["text"] = t
            if pinned is not None:
                i["pinned"] = bool(pinned)
            i["updated"] = time.time()
            _save(d)
            return i
    raise MemoryError_("No such memory.")


def delete(mid_or_text: str) -> str:
    """Delete by id, or by the best text match. Returns the deleted text."""
    d = _load()
    q = _norm(mid_or_text)
    hit = next((i for i in d["items"] if i["id"] == mid_or_text), None) or next((i for i in d["items"] if q and (q in _norm(i["text"]) or _norm(i["text"]) in q)), None)
    if hit is None:
        raise MemoryError_("Nothing in memory matches that.")
    d["items"].remove(hit)
    _save(d)
    return hit["text"]


def set_enabled(on: bool):
    d = _load()
    d["enabled"] = bool(on)
    _save(d)


def search(query: str, limit: int = 8) -> list[dict]:
    words = [w for w in _norm(query).split() if len(w) > 2]
    items = snapshot()["items"]
    if not words:
        return items[:limit]
    scored = sorted(((sum(w in _norm(i["text"]) for w in words), i) for i in items), key=lambda x: -x[0])
    return [i for s, i in scored if s > 0][:limit]


def prompt_block() -> str:
    """What goes into the system prompt (empty when memory is off or empty)."""
    d = snapshot()
    if not d["enabled"] or not d["items"]:
        return ""
    lines, used = [], 0
    for i in d["items"]:
        line = f"- {i['text']}"
        if used + len(line) > PROMPT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return ("Things you remember about the user and their work (use them when relevant; do not mention them unless it helps). "
            "If the user asks you to remember something, call remember with a short sentence.\n" + "\n".join(lines))
