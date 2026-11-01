"""Generated images (AI pictures and rendered diagrams), stored in data/images/.

Documents refer to them as `![caption](img:NAME)`. Only names that exist in this folder resolve; arbitrary URLs and paths are
never fetched, so model or web text cannot make a document pull in anything from elsewhere.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import paths

ROOT = paths.HOME  # user data, models and binaries; see paths.py
IMAGES_DIR = Path(os.environ.get("LFM3_IMAGES_DIR") or ROOT / "data" / "images")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,90}\.(png|jpg|jpeg|webp)$")
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def image_path(name: str) -> Path | None:
    """The file behind an image reference, or None if the name is not a stored image."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    p = (IMAGES_DIR / name).resolve()
    return p if p.parent == IMAGES_DIR.resolve() and p.is_file() else None


def resolve(name: str) -> Path | None:
    """The stored picture a reference means, even when the model mangled the name (spaces, a cut-off tail, a missing timestamp or
    extension). Exact names win; otherwise a unique prefix match on the name without its timestamp."""
    if not isinstance(name, str):
        return None
    name = name.strip().removeprefix("img:").strip()
    if (p := image_path(name)):
        return p
    stem = re.sub(r"(\.\w{2,4})$", "", name.lower())
    stem = re.sub(r"[\s_]+", "-", re.sub(r"[…]|\.{3}", "", stem)).strip("-")
    stem = re.sub(r"-?\d{8}-\d{6}(-\d+)?$", "", stem)  # the timestamp part
    if len(stem) < 8 or not IMAGES_DIR.exists():
        return None
    hits = [p for p in IMAGES_DIR.iterdir() if p.is_file() and NAME_RE.match(p.name) and re.sub(r"-\d{8}-\d{6}(-\d+)?\.\w+$", "", p.name.lower()).startswith(stem[:40])]
    if len(hits) == 1:
        return hits[0]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits and len({re.sub(r"-\d{8}-\d{6}(-\d+)?\.\w+$", "", p.name.lower()) for p in hits}) == 1 else None


def info(p: Path) -> dict:
    st = p.stat()
    meta = {}
    mp = p.with_name(p.name + ".json")
    if mp.exists():
        try:
            meta = json.loads(mp.read_text())
        except (OSError, ValueError):
            meta = {}
    return {"name": p.name, "format": p.suffix.lstrip("."), "size": st.st_size, "created": st.st_mtime, "url": f"/api/images/{p.name}",
            "kind": meta.get("kind", "image"), "prompt": meta.get("prompt"), "seed": meta.get("seed"), "size_px": meta.get("size_px"),
            "origin": meta.get("origin", "generated" if meta.get("seed") is not None else "image"), "credit": meta.get("credit"), "source_url": meta.get("source_url")}


def save_image(data: bytes, stem: str, ext: str = "png", meta: dict | None = None) -> dict:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()[:40] or "image"
    p = IMAGES_DIR / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.{ext}"
    n = 2
    while p.exists():
        p = IMAGES_DIR / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-{n}.{ext}"
        n += 1
    p.write_bytes(data)
    if meta:
        p.with_name(p.name + ".json").write_text(json.dumps(meta))
    return info(p)


def list_images() -> list[dict]:
    if not IMAGES_DIR.exists():
        return []
    files = [p for p in IMAGES_DIR.iterdir() if p.is_file() and NAME_RE.match(p.name)]
    return [info(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]


def delete_image(name: str):
    p = image_path(name)
    if p is None:
        raise FileNotFoundError(name)
    p.unlink()
    meta = p.with_name(p.name + ".json")
    if meta.exists():
        meta.unlink()
