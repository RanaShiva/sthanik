"""Which weights back each model slot, and how to get new ones.

The four llama.cpp slots (text, vl, audio, omni) have built-in defaults in server.py. Overrides live in config/models.json
and may only change the label, the GGUF files, the context size and the source repo. Files must sit inside models/, so a
slot can never be pointed at an arbitrary path on the machine. Downloads come from huggingface.co only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import httpx

import paths

import outbound

ROOT = paths.HOME  # user data, models and binaries; see paths.py
MODELS_DIR = ROOT / "models"
CONFIG = Path(os.environ.get("LFM3_MODELS_CONFIG") or ROOT / "config" / "models.json")
EDITABLE = ("label", "model", "mmproj", "ctx", "repo", "description")

REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,95}/[A-Za-z0-9][\w.-]{0,95}$")
FILE_RE = re.compile(r"^[\w.\-+()/ ]{1,200}\.gguf$")


class RegistryError(Exception):
    """Invalid request; the message is safe to show."""


def load_overrides() -> dict:
    try:
        d = json.loads(CONFIG.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_overrides(data: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(CONFIG)


def apply(models: dict, overrides: dict):
    """Layer saved overrides over the defaults (in place)."""
    for key, ov in overrides.items():
        if key in models and isinstance(ov, dict):
            models[key].update({k: v for k, v in ov.items() if k in EDITABLE})


def _is_image_file(p: Path) -> bool:
    """The picture-drawing pipeline owns models/image/: those files are not language models and must never back a slot."""
    try:
        return p.resolve().relative_to(MODELS_DIR.resolve()).parts[0] == "image"
    except (ValueError, IndexError):
        return False


def discover() -> list[dict]:
    """Every .gguf under models/, relative to the project, biggest groups first."""
    if not MODELS_DIR.exists():
        return []
    out = []
    for p in sorted(MODELS_DIR.rglob("*.gguf")):
        if p.is_file() and MODELS_DIR.resolve() in p.resolve().parents and not _is_image_file(p):
            out.append({"path": p.relative_to(ROOT).as_posix(), "size": p.stat().st_size, "projector": "mmproj" in p.name.lower()})
    return out


def precision(rel: str) -> str:
    """The quantisation named in a weights file name (BF16, Q6_K, Q4_K_M ...)."""
    m = re.search(r"(?<![A-Za-z0-9])(BF16|F16|F32|I?Q\d(?:_[A-Z0-9]+)*)(?![A-Za-z0-9])", Path(rel).name, re.I)
    return m.group(1).upper() if m else "-"


def is_gguf(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def check_file(rel: str | None, what: str, required: bool) -> str | None:
    if not rel:
        if required:
            raise RegistryError(f"Choose a {what} file.")
        return None
    p = (ROOT / rel).resolve()
    if MODELS_DIR.resolve() not in p.parents or p.suffix.lower() != ".gguf" or not p.is_file():
        raise RegistryError(f"The {what} must be an existing .gguf file inside the models folder.")
    if _is_image_file(p):
        raise RegistryError(f"The {what} belongs to picture drawing, not to a language model.")
    if not is_gguf(p):
        raise RegistryError(f"The {what} is not a valid GGUF file (it may be damaged or half-downloaded).")
    return p.relative_to(ROOT).as_posix()


def delete_file(rel: str, in_use: set[str]) -> int:
    """Remove a downloaded weights file (only under models/custom, never one a slot points at). Returns the bytes freed."""
    p = (ROOT / str(rel)).resolve()
    custom = (MODELS_DIR / "custom").resolve()
    if custom not in p.parents or p.suffix.lower() != ".gguf" or not p.is_file():
        raise RegistryError("Only models you downloaded (in models/custom) can be deleted here.")
    if p.relative_to(ROOT).as_posix() in in_use:
        raise RegistryError("A model slot still uses this file. Point the slot at other weights (or reset it) first.")
    size = p.stat().st_size
    p.unlink()
    try:
        p.parent.rmdir()  # the repository folder, once empty
    except OSError:
        pass
    return size


def validate(spec: dict, body: dict) -> dict:
    """Clean a request body into an override dict for `spec`'s slot."""
    ov = {}
    if "label" in body:
        label = " ".join(str(body["label"]).split())[:60]
        if not label:
            raise RegistryError("The name cannot be empty.")
        ov["label"] = label
    if "model" in body:
        ov["model"] = check_file(body["model"], "model", True)
    if "mmproj" in body:
        # Only slots that need a projector (vision / audio encoders) take one.
        ov["mmproj"] = check_file(body["mmproj"], "projector", bool(spec.get("mmproj")))
    if "ctx" in body:
        try:
            ctx = int(body["ctx"])
        except (TypeError, ValueError):
            raise RegistryError("The context size must be a number.")
        if not 2048 <= ctx <= 1_048_576:
            raise RegistryError("The context size must be between 2,048 and 1,048,576 tokens.")
        ov["ctx"] = ctx
    if "repo" in body:
        repo = str(body["repo"] or "").strip()
        if repo and not REPO_RE.match(repo):
            raise RegistryError("The source must look like owner/name (a Hugging Face repository).")
        ov["repo"] = repo or spec.get("repo", "")
    if "description" in body:
        ov["description"] = str(body["description"])[:200]
    return ov


class Downloads:
    """Background downloads of single GGUF files from Hugging Face, resumable and cancellable."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def snapshot(self) -> list[dict]:
        return sorted(self.items.values(), key=lambda d: d["started"], reverse=True)[:12]

    def start(self, repo: str, file: str) -> dict:
        if not REPO_RE.match(repo or ""):
            raise RegistryError("The repository must look like owner/name.")
        if not FILE_RE.match(file or "") or ".." in file or file.startswith("/") or "//" in file:
            raise RegistryError("The file must be a .gguf file name from that repository.")
        if sum(1 for d in self.items.values() if d["state"] == "running") >= 2:
            raise RegistryError("Two downloads are already running. Wait for one to finish.")
        base = file.rsplit("/", 1)[-1]
        dest = MODELS_DIR / "custom" / repo.replace("/", "__") / base
        if any(d["state"] == "running" and d["repo"] == repo and d["file"] == file for d in self.items.values()):
            raise RegistryError("That file is already downloading.")
        if dest.exists():
            raise RegistryError(f"{base} is already downloaded ({dest.relative_to(ROOT).as_posix()}). Use it from Downloaded files below.")
        did = f"{int(time.time() * 1000):x}"
        item = {"id": did, "repo": repo, "file": file, "path": dest.relative_to(ROOT).as_posix(), "state": "running", "done": 0, "total": 0,
                "error": "", "started": time.time()}
        self.items[did] = item
        self.tasks[did] = asyncio.create_task(self._run(item, dest))
        return item

    def cancel(self, did: str):
        t = self.tasks.get(did)
        if t and not t.done():
            t.cancel()

    async def _run(self, item: dict, dest: Path):
        url = f"https://huggingface.co/{item['repo']}/resolve/main/{quote(item['file'])}"
        part = dest.with_name(dest.name + ".part")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            have = part.stat().st_size if part.exists() else 0
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=90), proxy=outbound.proxy()) as client:
                async with client.stream("GET", url, headers={"Range": f"bytes={have}-"} if have else {}) as r:
                    if r.status_code == 416:  # the part file is already complete
                        have, total = 0, part.stat().st_size
                    elif r.status_code in (401, 403):
                        raise RegistryError("That repository needs a Hugging Face login or licence acceptance, which this app does not do. Download it in a browser and copy the .gguf into the models folder.")
                    elif r.status_code == 404:
                        raise RegistryError("Hugging Face has no such file. Check the repository and the exact file name (including upper and lower case).")
                    elif r.status_code not in (200, 206):
                        raise RegistryError(f"Hugging Face answered {r.status_code}. Try again in a moment.")
                    else:
                        if r.status_code == 200:
                            have = 0  # the server ignored the range: start over
                        total = have + int(r.headers.get("content-length", 0))
                        if total and shutil.disk_usage(dest.parent).free < (total - have) + 1_000_000_000:
                            raise RegistryError("Not enough free disk space for this file.")
                        item["total"] = total
                        with part.open("ab" if have else "wb") as f:
                            item["done"] = have
                            async for chunk in r.aiter_bytes(1 << 20):
                                f.write(chunk)
                                item["done"] += len(chunk)
            if item["total"] and part.stat().st_size != item["total"]:
                raise RegistryError("The download ended early. Start it again to resume.")
            with part.open("rb") as f:
                if f.read(4) != b"GGUF":
                    part.unlink(missing_ok=True)
                    raise RegistryError("That file is not a GGUF model.")
            part.replace(dest)
            item["state"] = "done"
        except asyncio.CancelledError:
            item["state"] = "cancelled"
        except RegistryError as e:
            item["state"], item["error"] = "failed", str(e)
        except (httpx.HTTPError, OSError) as e:
            item["state"], item["error"] = "failed", f"{type(e).__name__}: {e}"[:200]
        finally:
            try:
                dest.parent.rmdir()  # a failed or cancelled download with nothing saved leaves no empty folder
            except OSError:
                pass
