"""First-run setup: install the llama.cpp engine and the model files this computer can run.

What it does, in order (each step is skipped when its files are already there, so running it again repairs rather than repeats):

1. The engine. `hardware.choose_runtime()` lists llama.cpp builds best first. Each is downloaded from the release pinned in
   runtime_manifest.json, checked against the SHA-256 recorded there (a download that differs is deleted), unpacked, and asked
   `llama-server --list-devices`; the first build that really sees a GPU (or the CPU build, last) is kept.
2. The models. For every part you ticked: the weights at the precision you chose for that part (`hardware.choose_quant()` recommends one),
   from the LiquidAI repositories on Hugging Face, checked against the SHA-256 Hugging Face publishes for the file. Pictures have a
   precision for the drawing model and one for its text encoder. Files you already have can be used instead (see bringin.py).
3. The profile. What the engine can use is saved to config/hardware.json (the model loader reads it to decide how many layers go on the
   GPU) and config/models.json points each slot at the files that were fetched.

Only github.com (the engine) and huggingface.co (the models) are contacted, through LFM3_PROXY when that is set.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import httpx

import bringin
import hardware
import outbound
import paths
import registry

ROOT = paths.HOME
BIN_LLAMA = ROOT / "bin" / "llama"
BIN_SD = ROOT / "bin" / "sd"
MODELS = ROOT / "models"
MANIFEST = json.loads((paths.APP / "app" / "runtime_manifest.json").read_text())
EXE = ".exe" if sys.platform == "win32" else ""

# The language-model parts and the files each is made of. Names follow the repositories' own file names.
COMPONENTS = {
    "text": {"label": "Chat", "detail": "LFM3-1.7B: text chat, documents, tools", "repo": "LiquidAI/LFM3-1.7B-GGUF", "stem": "LFM3-1.7B", "mmproj": None, "required": True},
    "vl": {"label": "Vision", "detail": "LFM3-VL-2B: look at images and video", "repo": "LiquidAI/LFM3-VL-2B-GGUF", "stem": "LFM3-VL-2B", "mmproj": "VL"},
    "omni": {"label": "Omni", "detail": "LFM3-Omni-2B: images, video and audio in, speech out", "repo": "LiquidAI/LFM3-Omni-2B-GGUF", "stem": "LFM3-Omni-2B", "mmproj": "Omni"},
    "audio": {"label": "Voice", "detail": "LFM3-Audio-2B: speech recognition and reading aloud", "repo": "LiquidAI/LFM3-Audio-2B-GGUF", "stem": "LFM3-Audio-2B", "mmproj": "Audio"},
}
# Picture drawing: stable-diffusion.cpp plus Z-Image-Turbo (its three model files live in models/image/, see imagegen.py).
# The drawing model and its text encoder come in several precisions; which ones are in use is kept in config/image.json.
IMAGE_MODELS = {
    "diffusion": {"label": "Drawing model", "repo": "leejet/Z-Image-Turbo-GGUF", "name": r"z_image_turbo-({q})\.gguf", "pattern": r"Q\d_K|Q\d_[01]", "make": "z_image_turbo-{q}.gguf", "default": "Q4_K"},
    "llm": {"label": "Text encoder", "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF", "name": r"Qwen3-4B-Instruct-2507-({q})\.gguf", "pattern": r"F16|Q\d_K_[MSL]|Q\d_K|Q\d_[01]", "make": "Qwen3-4B-Instruct-2507-{q}.gguf", "default": "Q4_K_M"},
}
IMAGE_VAE = ("Comfy-Org/z_image_turbo", "split_files/vae/ae.safetensors")
IMAGE_CONFIG = ROOT / "config" / "image.json"


def image_selection() -> dict[str, str]:
    """{"diffusion": file name, "llm": file name, "vae": file name} in use for pictures (the defaults until another precision was installed)."""
    try:
        saved = json.loads(IMAGE_CONFIG.read_text())
    except (OSError, ValueError):
        saved = {}
    out = {}
    for k, m in IMAGE_MODELS.items():
        q = saved.get(k) if isinstance(saved, dict) else None
        out[k] = m["make"].format(q=q) if isinstance(q, str) and re.fullmatch(m["pattern"], q) else m["make"].format(q=m["default"])
    out["vae"] = Path(IMAGE_VAE[1]).name
    return out


def save_image_selection(quants: dict[str, str]):
    IMAGE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = IMAGE_CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(quants, indent=2))
    tmp.replace(IMAGE_CONFIG)


def published_quants(listing: dict[str, dict] | None, pattern: str, name: str) -> list[str]:
    """The precisions a repository publishes for one model, largest first. `name` has {q} where the precision sits; `pattern` says what counts as one."""
    rx = re.compile("^" + name.replace("{q}", pattern) + "$")
    found = {m.group(1) for f in (listing or {}) if "/" not in f for m in [rx.match(f)] if m}
    return sorted(found, key=hardware.quant_order)


class SetupError(Exception):
    """Something the user can act on; the message is safe to show."""


def _mmproj_quant(comp: str, quant: str, listing: dict[str, dict] | None = None) -> str:
    """The projector that goes with the weights: the same precision when the repository has one, else the nearest it has. Vision publishes
    only BF16/F16/Q8_0 projectors, so 4-bit weights take the 8-bit one."""
    stem = COMPONENTS[comp]["stem"]
    if listing:
        for q in (quant, "Q8_0", "BF16", "F16"):
            if f"mmproj-{stem}-{q}.gguf" in listing:
                return q
    return "Q8_0" if comp == "vl" and quant == "Q4_K_M" else quant


def model_quants(comp: str, listing: dict[str, dict] | None) -> list[str]:
    """The precisions the repository publishes for a part. Without a listing (offline) the three the app knows."""
    found = published_quants(listing, r"BF16|F16|Q\d_K_[MSL]|Q\d_K|Q\d_[01]|QAD-Q\d_[01]", re.escape(COMPONENTS[comp]["stem"]) + r"-({q})\.gguf")
    return found or list(hardware.QUANTS)


def model_files(comp: str, quant: str, listing: dict[str, dict] | None = None) -> list[dict]:
    c = COMPONENTS[comp]
    files = [{"repo": c["repo"], "file": f"{c['stem']}-{quant}.gguf", "role": "model"}]
    if c["mmproj"]:
        files.append({"repo": c["repo"], "file": f"mmproj-{c['stem']}-{_mmproj_quant(comp, quant, listing)}.gguf", "role": "mmproj"})
    for f in files:
        f["path"] = f"models/{comp}/{f['file']}"
    return files


# ---------------------------------------------------------------- the installed engine
def _find_server(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    direct = folder / f"llama-server{EXE}"
    if direct.is_file():
        return direct
    return next((p for p in sorted(folder.rglob(f"llama-server{EXE}")) if p.is_file()), None)


def installed_runtime() -> dict | None:
    """{"server": Path, "dir": Path, ...}: the engine this app should start. current.json names the one the installer chose; an engine
    unpacked by hand (bin/llama/llama-b*/) is found too."""
    try:
        cur = json.loads((BIN_LLAMA / "current.json").read_text())
        server = _find_server(BIN_LLAMA / cur["dir"])  # "dir" is a folder name here, or the full path of the user's own build
        if server:
            return {**cur, "server": server, "dir": server.parent, "manual": bool(cur.get("external"))}
    except (OSError, ValueError, KeyError):
        pass
    for d in sorted(BIN_LLAMA.glob("llama-b*"), reverse=True) if BIN_LLAMA.is_dir() else []:
        server = _find_server(d)
        if server:
            return {"server": server, "dir": server.parent, "tag": d.name.removeprefix("llama-"), "backend": "unknown", "manual": True}
    return None


def runtime_env(folder: Path) -> dict:
    """Environment for running the engine's programs: they load their shared libraries from their own folder."""
    env = dict(os.environ)
    if sys.platform == "win32":
        env["PATH"] = f"{folder}{os.pathsep}{env.get('PATH', '')}"
    elif sys.platform == "darwin":
        env["DYLD_LIBRARY_PATH"] = f"{folder}{os.pathsep}{env['DYLD_LIBRARY_PATH']}" if env.get("DYLD_LIBRARY_PATH") else str(folder)
    else:
        env["LD_LIBRARY_PATH"] = f"{folder}{os.pathsep}{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else str(folder)
    return env


DEVICE_RE = re.compile(r"^\s*([A-Za-z]+\d*):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", re.M)


def list_devices(server: Path) -> list[dict] | None:
    """What `llama-server --list-devices` reports: [{"id": "CUDA0", "name", "total_mb", "free_mb"}]. None when the program will not run at all."""
    try:
        r = subprocess.run([str(server), "--list-devices"], capture_output=True, text=True, timeout=60, env=runtime_env(server.parent), cwd=server.parent, **paths.QUIET)
    except (OSError, subprocess.SubprocessError):
        return None
    text = r.stdout + r.stderr
    if r.returncode != 0 and "Available devices" not in text:
        return None
    return [{"id": m[0], "name": m[1], "total_mb": int(m[2]), "free_mb": int(m[3])} for m in DEVICE_RE.findall(text)]


def write_profile(devices: list[dict], backend: str, tag: str):
    """Remember what the engine can use. The GPU with the most memory is the one models are sized for."""
    gpus = [d for d in devices if not d["id"].upper().startswith("CPU")]
    best = max(gpus, key=lambda d: d["total_mb"], default=None)
    hardware.save_profile({"backend": backend, "tag": tag, "gpu": bool(best), "device": best["name"] if best else None,
                           "vram_mb": best["total_mb"] if best else None, "devices": devices, "checked": int(time.time())})


def refresh_profile():
    """At every start: ask the engine what it can use now and keep config/hardware.json in step (an engine placed by hand has no profile yet;
    a driver installed later, or a card taken out, changes the answer). A run where the engine will not start leaves the profile alone."""
    rt = installed_runtime()
    if not rt:
        return
    devices = list_devices(rt["server"])
    if devices is None:
        return
    old = hardware.load_profile()
    cards = lambda ds: sorted((d["name"], d["total_mb"]) for d in ds or [] if not d["id"].upper().startswith("CPU"))  # noqa: E731
    if not old or cards(old.get("devices")) != cards(devices):
        backend = rt.get("backend") if rt.get("backend") not in (None, "unknown") else old.get("backend", "unknown")
        write_profile(devices, backend, rt.get("tag", ""))


# ---------------------------------------------------------------- downloading
class Cancelled(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


async def _fetch_once(client: httpx.AsyncClient, url: str, part: Path, step: dict, size: int | None):
    """One streaming attempt, continuing from whatever is already in `part`."""
    have = part.stat().st_size if part.exists() else 0
    for attempt in range(2):
        async with client.stream("GET", url, headers={"Range": f"bytes={have}-"} if have else {}) as r:
            if r.status_code == 416 and attempt == 0:  # the part file is already complete (or longer than the file): start over
                part.unlink(missing_ok=True)
                have = 0
                continue
            if r.status_code in (401, 403):
                raise SetupError("This download needs a login or licence acceptance, which the app does not do.")
            if r.status_code == 404:
                raise SetupError(f"The server has no file {part.name.removesuffix('.part')}. The repository may have renamed it.")
            if r.status_code not in (200, 206):
                raise SetupError(f"The download server answered {r.status_code}. Try again in a moment.")
            if r.status_code == 200:  # the server ignored the range: start over
                have = 0
            step["total"] = (have + int(r.headers["content-length"])) if r.headers.get("content-length") else (size or 0)
            if step["total"] and shutil.disk_usage(part.parent).free < (step["total"] - have) + 500_000_000:
                raise SetupError(f"Not enough free disk space for {part.name.removesuffix('.part')}.")
            step["done"] = have
            with part.open("ab" if have else "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
                    step["done"] += len(chunk)
        return


async def download(url: str, dest: Path, step: dict, sha256: str | None = None, size: int | None = None, attempts: int = 8):
    """Download into dest (via dest.part) with the progress in `step`. A dropped or stalled connection resumes from the part file; a wrong
    SHA-256 deletes the file and raises SetupError."""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(20, read=30), proxy=outbound.proxy(), headers={"User-Agent": "Sthanik-setup"}) as client:
        for attempt in range(attempts):
            try:
                await _fetch_once(client, url, part, step, size)
                break
            except httpx.TransportError as e:  # timeouts, resets, a dropped network: carry on where it stopped
                if attempt == attempts - 1:
                    raise SetupError(f"The connection to {httpx.URL(url).host} kept dropping ({type(e).__name__}). Check the network and start the setup again; it resumes.")
                step["note"] = f"connection lost, resuming ({attempt + 1}/{attempts - 1})"
                await asyncio.sleep(min(2 ** attempt, 15))
    got = part.stat().st_size
    if (size and got != size) or (not size and step.get("total") and got != step["total"]):
        raise SetupError(f"{dest.name} ended early ({got} of {size or step['total']} bytes). Start the setup again to resume.")
    if sha256 and await asyncio.to_thread(_sha256, part) != sha256.lower():
        part.unlink(missing_ok=True)
        raise SetupError(f"{dest.name} does not match its published SHA-256, so it was deleted. Try again; if it repeats, the download is being altered on the way.")
    part.replace(dest)


def _safe_extract(archive: Path, into: Path):
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()
    if archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if not (root / m.name).resolve().is_relative_to(root) or m.issym() and not (root / m.name).parent.joinpath(m.linkname).resolve().is_relative_to(root):
                    raise SetupError(f"{archive.name} contains a path outside its folder; not unpacking it.")
            t.extractall(into, filter="data")
    else:
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if not (root / n).resolve().is_relative_to(root):
                    raise SetupError(f"{archive.name} contains a path outside its folder; not unpacking it.")
            z.extractall(into)


def _make_executable(folder: Path):
    for p in folder.rglob("*"):
        if p.is_file() and (p.suffix in ("", ".sh") or ".so" in p.name or p.suffix == ".dylib") and sys.platform != "win32":
            p.chmod(p.stat().st_mode | stat.S_IXUSR)


# ---------------------------------------------------------------- huggingface listing
_hf_cache: dict[str, tuple[float, dict]] = {}


async def hf_files(repo: str, folder: str = "") -> dict[str, dict]:
    """{path: {"size", "sha256"}} for one folder of a repository, from the Hugging Face API (cached for ten minutes)."""
    key = f"{repo}/{folder}"
    if key in _hf_cache and time.time() - _hf_cache[key][0] < 600:
        return _hf_cache[key][1]
    url = f"https://huggingface.co/api/models/{repo}/tree/main" + (f"/{quote(folder)}" if folder else "")
    async with httpx.AsyncClient(timeout=20, proxy=outbound.proxy(), follow_redirects=True) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise SetupError(f"Hugging Face did not list {repo} ({r.status_code}).")
    out = {f["path"]: {"size": (f.get("lfs") or {}).get("size", f.get("size", 0)), "sha256": (f.get("lfs") or {}).get("oid")} for f in r.json() if f.get("type") == "file"}
    _hf_cache[key] = (time.time(), out)
    return out


async def hf_info(repo: str, path: str) -> dict:
    folder, _, _ = path.rpartition("/")
    try:
        info = (await hf_files(repo, folder)).get(path)
    except (httpx.HTTPError, SetupError):
        info = None
    return info or {"size": 0, "sha256": None}


# ---------------------------------------------------------------- the plan the setup page shows
def _asset(name: str) -> dict | None:
    return MANIFEST["llama"]["assets"].get(name.replace("{tag}", MANIFEST["llama"]["tag"]))


def runtime_plan(hw: dict) -> dict:
    cands = []
    for c in hardware.choose_runtime(hw):
        main, extra = _asset(c["asset"]), [_asset(e) for e in c["extra"]]
        if main and all(extra):
            cands.append({**c, "files": [main["url"].rsplit("/", 1)[-1], *[e["url"].rsplit("/", 1)[-1] for e in extra]],
                          "size": main["size"] + sum(e["size"] for e in extra)})
    rt = installed_runtime()
    return {"installed": bool(rt), "tag": MANIFEST["llama"]["tag"], "candidates": cands,
            "current": {"tag": rt.get("tag"), "backend": rt.get("backend"), "external": bool(rt.get("external")), "path": str(rt["server"])} if rt else None}


def sd_plan(hw: dict) -> dict | None:
    """The stable-diffusion.cpp build for this system, or None where none is published."""
    a = MANIFEST["sd"]["assets"]
    gpu = hardware._best_gpu(hw)
    pick = None
    if hw["os"] == "linux" and hw["arch"] == "x86_64":
        pick = next((n for n in a if n.endswith("Ubuntu-24.04-x86_64-vulkan.zip")), None) if gpu else next((n for n in a if n.endswith("Ubuntu-24.04-x86_64.zip")), None)
    elif hw["os"] == "windows" and hw["arch"] == "x86_64":
        suffix = "win-cuda12-x64.zip" if gpu and gpu["vendor"] == "nvidia" else "win-vulkan-x64.zip" if gpu else "win-cpu-x64.zip"
        pick = next((n for n in a if n.endswith(suffix)), None)
    elif hw["os"] == "macos" and hw["arch"] == "arm64":
        pick = next((n for n in a if "Darwin-macOS" in n and n.endswith("arm64.zip")), None)
    if not pick:
        return None
    names = [pick] + (["cudart-sd-bin-win-cu12-x64.zip"] if "cuda12" in pick else [])
    return {"assets": names, "size": sum(a[n]["size"] for n in names)}


async def _listing(repo: str) -> dict[str, dict] | None:
    try:
        return await hf_files(repo)
    except (httpx.HTTPError, SetupError):
        return None


def _present(path: Path, info: dict) -> bool:
    return path.is_file() and (not info["size"] or path.stat().st_size == info["size"])


def _pick(wanted: str | None, offered: list[str], fallback: str) -> str:
    """The precision asked for if the repository has it, else the fallback if it has that, else its largest."""
    return wanted if wanted in offered else fallback if fallback in offered else offered[0]


async def plan(options: dict | None = None) -> dict:
    """What setup would do on this computer. options["quants"] maps a part (or "diffusion" / "llm" for pictures) to a precision;
    options["quant"] is the one for every part not named there."""
    options = options or {}
    hw = await asyncio.to_thread(hardware.detect)
    recommended = hardware.choose_quant(hw)
    default_q = options.get("quant") if options.get("quant") else recommended
    wanted = options.get("quants") if isinstance(options.get("quants"), dict) else {}
    budget, where = hardware.model_budget_mb(hw)
    overrides = registry.load_overrides()
    listings = dict(zip(COMPONENTS, await asyncio.gather(*[_listing(c["repo"]) for c in COMPONENTS.values()])))
    comps = []
    for cid, c in COMPONENTS.items():
        listing = listings[cid]
        offered = model_quants(cid, listing)
        quant = _pick(wanted.get(cid) or default_q, offered, recommended)
        choices = []
        for q in offered:
            fs = model_files(cid, q, listing)
            infos = await asyncio.gather(*[hf_info(f["repo"], f["file"]) for f in fs])
            todo = sum(i["size"] for f, i in zip(fs, infos) if not _present(ROOT / f["path"], i))
            choices.append({"id": q, "note": hardware.quant_note(q), "size": todo, "total": sum(i["size"] for i in infos), "installed": todo == 0 and all(i["size"] for i in infos)})
        files = model_files(cid, quant, listing)
        infos = await asyncio.gather(*[hf_info(f["repo"], f["file"]) for f in files])
        for f, i in zip(files, infos):
            f["size"], f["sha256"] = i["size"], i["sha256"]
            f["present"] = _present(ROOT / f["path"], i)
        # weights of the user's own behind this part (set by "use my own files" or on the Models page) count as installed
        slot = overrides.get(cid, {})
        known = {f["path"] for q in offered for f in model_files(cid, q, listing)}
        mine = slot.get("model") if slot.get("model") and slot["model"] not in known and (ROOT / slot["model"]).is_file() and (not c["mmproj"] or (slot.get("mmproj") and (ROOT / slot["mmproj"]).is_file())) else None
        comps.append({"id": cid, "label": c["label"], "detail": c["detail"], "required": bool(c.get("required")), "files": files, "quant": quant,
                      "quants": choices, "needs_projector": bool(c["mmproj"]), "installed": all(f["present"] for f in files) or bool(mine),
                      "own": Path(mine).name if mine else None, "size": sum(f["size"] for f in files if not f["present"])})
    sd = sd_plan(hw)
    img = None
    if sd:
        chosen_img = image_selection()
        parts, size = {}, sd["size"]
        for k, m in IMAGE_MODELS.items():
            listing = await _listing(m["repo"])
            offered = published_quants(listing, m["pattern"], m["name"]) or [m["default"]]
            sel = _pick(wanted.get(k), offered, m["default"]) if wanted.get(k) else _pick(_installed_image_quant(k, chosen_img), offered, m["default"])
            choices = []
            for q in offered:
                fname = m["make"].format(q=q)
                info = await hf_info(m["repo"], fname)
                there = _present(MODELS / "image" / fname, info)
                choices.append({"id": q, "note": hardware.quant_note(q), "size": 0 if there else info["size"], "total": info["size"], "installed": there and bool(info["size"])})
            parts[k] = {"label": m["label"], "quant": sel, "quants": choices}
            size += next(c["size"] for c in choices if c["id"] == sel)
        vae = await hf_info(*IMAGE_VAE)
        vae_todo = 0 if _present(MODELS / "image" / Path(IMAGE_VAE[1]).name, vae) else vae["size"]
        have = all((MODELS / "image" / m["make"].format(q=parts[k]["quant"])).is_file() for k, m in IMAGE_MODELS.items()) and (MODELS / "image" / Path(IMAGE_VAE[1]).name).is_file()
        img = {"id": "image", "label": "Pictures", "detail": "Z-Image-Turbo through stable-diffusion.cpp: draw pictures (experimental)", "required": False, "parts": parts,
               "engine_size": 0 if (BIN_SD / f"sd-cli{EXE}").is_file() else sd["size"], "vae_size": vae_todo,
               "installed": have and (BIN_SD / f"sd-cli{EXE}").is_file(), "size": size + vae_todo, "files": []}
    return {"hardware": hw, "runtime": runtime_plan(hw), "quant": default_q if default_q in hardware.QUANTS else recommended, "recommended": recommended,
            "quants": [{"id": q, "note": hardware.quant_note(q)} for q in hardware.QUANTS],
            "budget_mb": budget, "budget_where": where, "ctx": hardware.choose_ctx(hw, 32768), "components": comps, "image": img,
            "needed": needed(), "disk_free_mb": hw["disk_free_mb"]}


def _installed_image_quant(kind: str, selection: dict[str, str]) -> str | None:
    m = re.fullmatch(IMAGE_MODELS[kind]["name"].replace("{q}", IMAGE_MODELS[kind]["pattern"]), selection[kind])
    return m.group(1) if m else None


def needed() -> bool:
    """The app cannot chat yet: no engine, or no text model."""
    text = ROOT / registry.load_overrides().get("text", {}).get("model", "models/text/LFM3-1.7B-BF16.gguf")
    return installed_runtime() is None or not text.is_file()


# ---------------------------------------------------------------- the job
class Setup:
    def __init__(self):
        self.job: dict = {"state": "idle", "steps": [], "error": "", "started": 0, "finished": 0}
        self.task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        return self.job

    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def start(self, options: dict) -> dict:
        if self.running():
            raise SetupError("Setup is already running.")
        self.job = {"state": "running", "steps": [], "error": "", "started": time.time(), "finished": 0}
        self.task = asyncio.create_task(self._run(options))
        return self.job

    def cancel(self):
        if self.running():
            self.task.cancel()

    def _step(self, sid: str, label: str) -> dict:
        s = {"id": sid, "label": label, "state": "pending", "done": 0, "total": 0, "note": ""}
        self.job["steps"].append(s)
        return s

    async def _run(self, options: dict):
        made: list[Path] = []  # files this job put into models/ for the user's own weights: removed again when the check fails
        try:
            p = await plan(options)
            own = options.get("own") or {}
            own_models = own.get("models") or {}
            # a part with files of the user's own is not downloaded
            chosen = [c for c in p["components"] if (c["id"] in set(options.get("components") or []) or (c["required"] and not c["installed"])) and c["id"] not in own_models]
            want_image = bool(options.get("image")) and p["image"] is not None
            # one place to say all the steps first, so the page can show the whole list at once
            steps = {}
            if own.get("engine"):
                steps["own-engine"] = self._step("own-engine", "Your llama.cpp: checking it")
            elif options.get("engine", True):
                steps["engine"] = self._step("engine", "Engine (llama.cpp)")
            for c in chosen:
                for f in c["files"]:
                    steps[f["path"]] = self._step(f["path"], f"{c['label']} ({c['quant']}): {f['file']}")
            if want_image:
                steps["image"] = self._step("image", "Pictures: engine and model files")
            labels = {c["id"]: c["label"] for c in p["components"]}
            for cid in own_models:
                steps[f"own-{cid}"] = self._step(f"own-{cid}", f"{labels[cid]}: your files")
            if own:
                steps["trial"] = self._step("trial", "Trying the engine on the model")
            steps["finish"] = self._step("finish", "Finishing")

            need = sum(f["size"] for c in chosen for f in c["files"] if not f["present"]) + (p["image"]["size"] if want_image else 0)
            if p["disk_free_mb"] * 1024 * 1024 < need + 1_000_000_000:
                raise SetupError(f"These parts need about {need / 1e9:.1f} GB and only {p['disk_free_mb'] / 1024:.1f} GB is free in {paths.HOME}. Untick some parts or free up space.")

            backend, tag = None, MANIFEST["llama"]["tag"]
            new_engine = None
            if "own-engine" in steps:
                new_engine = await self._own_engine(steps["own-engine"], own["engine"])
                backend = new_engine["backend"]
            elif "engine" in steps:
                backend = await self._engine(steps["engine"], p["runtime"], options)
            overrides = registry.load_overrides()
            for c in chosen:
                for f in c["files"]:
                    s = steps[f["path"]]
                    dest = ROOT / f["path"]
                    if f["present"]:
                        s.update(state="skipped", note="already there", done=dest.stat().st_size, total=dest.stat().st_size)
                        continue
                    s["state"] = "running"
                    await download(f"https://huggingface.co/{f['repo']}/resolve/main/{quote(f['file'])}", dest, s, f["sha256"], f["size"] or None)
                    s["state"] = "done"
                # only a file other than the slot's built-in BF16 one needs the slot pointed at it
                slot = overrides.get(c["id"], {})
                for f in c["files"]:
                    key = "model" if f["role"] == "model" else "mmproj"
                    builtin = f"models/{c['id']}/{c['stem']}-BF16.gguf" if key == "model" else f"models/{c['id']}/mmproj-{c['stem']}-BF16.gguf"
                    if f["path"] == builtin:
                        slot.pop(key, None)
                    else:
                        slot[key] = f["path"]
                if slot:
                    overrides[c["id"]] = slot
                else:
                    overrides.pop(c["id"], None)
            if want_image:
                await self._image(steps["image"], p, options)
            trial_pair = None
            for cid, files in own_models.items():
                model, mmproj = await self._own_model(steps[f"own-{cid}"], cid, files, made)
                overrides[cid] = {**overrides.get(cid, {}), "model": model.relative_to(ROOT).as_posix(), **({"mmproj": mmproj.relative_to(ROOT).as_posix()} if mmproj else {})}
                if trial_pair is None or cid == "text":
                    trial_pair = (model, mmproj)
            if "trial" in steps:
                await self._trial(steps["trial"], new_engine, trial_pair, overrides)
            s = steps["finish"]; s["state"] = "running"
            if new_engine:
                self._commit_engine(new_engine)
            registry.save_overrides(overrides)
            rt = installed_runtime()
            if rt:
                devices = await asyncio.to_thread(list_devices, rt["server"])
                if devices is not None:
                    write_profile(devices, backend or rt.get("backend", "unknown"), rt.get("tag", tag))
            if want_image:
                save_image_selection({k: p["image"]["parts"][k]["quant"] for k in IMAGE_MODELS})
            s["state"] = "done"
            self.job.update(state="done", finished=time.time())
        except asyncio.CancelledError:
            self._fail_running("cancelled")
            self._undo(made)
            self.job.update(state="cancelled", finished=time.time())
        except (SetupError, bringin.BringInError, httpx.HTTPError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:
            self._fail_running("failed")
            self._undo(made)
            self.job.update(state="failed", error=str(e) or type(e).__name__, finished=time.time())

    @staticmethod
    def _undo(made: list[Path]):
        for f in made:
            f.unlink(missing_ok=True)

    # ---- the user's own engine and weights
    async def _own_engine(self, step: dict, given: str) -> dict:
        """Check a llama-server the user already has: it starts, says its version and lists its devices. Nothing is saved yet."""
        step["state"] = "running"
        server = bringin.find_server(given)
        env = runtime_env(server.parent)
        facts = await asyncio.to_thread(bringin.engine_facts, server, env)
        step["note"] = f"{facts['text']}: checking which devices it can use"
        devices = await asyncio.to_thread(list_devices, server)
        if devices is None:
            raise SetupError(f"{server} starts but would not list its devices (llama-server --list-devices failed).")
        want = int(re.sub(r"\D", "", MANIFEST["llama"]["tag"]) or 0)
        gpu = [d for d in devices if not d["id"].upper().startswith("CPU")]
        build = facts["build"]
        note = ", ".join(d["name"] for d in gpu) if gpu else "no GPU, running on the CPU"
        if build and want and build != want:
            note += f" (build {build}; this app was tested with {want})"
        step.update(state="done", note=f"{server.parent}: {note}")
        return {"server": server, "build": build, "devices": devices, "backend": bringin.backend_of(devices)}

    def _commit_engine(self, engine: dict) -> None:
        BIN_LLAMA.mkdir(parents=True, exist_ok=True)
        tag = f"b{engine['build']}" if engine["build"] else "unknown"
        (BIN_LLAMA / "current.json").write_text(json.dumps({"dir": str(engine["server"].parent), "tag": tag, "backend": engine["backend"], "external": True}))

    async def _own_model(self, step: dict, cid: str, files: dict, made: list[Path]) -> tuple[Path, Path | None]:
        """Check the user's weights (and projector) and bring them into the models folder."""
        step["state"] = "running"
        c = COMPONENTS[cid]
        src_model = Path(str(files.get("model") or "")).expanduser()
        src_proj = Path(str(files["mmproj"])).expanduser() if files.get("mmproj") else None
        if not files.get("model"):
            raise SetupError(f"{c['label']}: give the path of the model file (.gguf).")
        if c["mmproj"] and not src_proj:
            raise SetupError(f"{c['label']} also needs its projector file (mmproj-….gguf). Give both.")
        if src_proj and not c["mmproj"]:
            raise SetupError(f"{c['label']} does not use a projector file. Leave that field empty.")
        info = await asyncio.to_thread(bringin.check_model_file, src_model, False)
        if src_proj:
            await asyncio.to_thread(bringin.check_model_file, src_proj, True)
        out = []
        for src in [src_model, *([src_proj] if src_proj else [])]:
            total = src.stat().st_size
            step.update(done=0, total=total, note=f"{src.name}: moving into the models folder")
            dest, created = await asyncio.to_thread(bringin.bring_in, src, MODELS, lambda n: step.update(done=n))
            if created:
                made.append(dest)
            out.append(dest)
        step.update(state="done", done=step["total"], note=f"{info['name'] or info['arch']} ({info['arch']})")
        return out[0], out[1] if src_proj else None

    async def _trial(self, step: dict, new_engine: dict | None, pair: tuple[Path, Path | None] | None, overrides: dict):
        """Start the engine on a model for real. Without a model of the user's own, use one already installed."""
        step["state"] = "running"
        rt = None if new_engine else installed_runtime()
        server = new_engine["server"] if new_engine else rt["server"] if rt else None
        if server is None:
            raise SetupError("There is no engine to try the model on.")
        if pair is None:
            for cid in ("text", "vl", "audio", "omni"):
                slot = overrides.get(cid, {})
                c = COMPONENTS[cid]
                m = ROOT / slot.get("model", f"models/{cid}/{c['stem']}-BF16.gguf")
                mm = ROOT / slot.get("mmproj", f"models/{cid}/mmproj-{c['stem']}-BF16.gguf") if c["mmproj"] else None
                if m.is_file() and (mm is None or mm.is_file()):
                    pair = (m, mm)
                    break
        if pair is None:
            step.update(state="skipped", note="no model installed yet to try it on")
            return
        step["note"] = f"loading {pair[0].name} (this takes a little while)"
        said = await bringin.trial(server, runtime_env(server.parent), pair[0], pair[1])
        step.update(state="done", note=f"it loaded {pair[0].name}" + (f" and answered: “{said[:60]}”" if said and said != "loaded" else ""))

    def _fail_running(self, state: str):
        for s in self.job["steps"]:
            if s["state"] == "running":
                s["state"] = state

    async def _engine(self, step: dict, rplan: dict, options: dict) -> str:
        """Install the first candidate build that works; returns its backend."""
        step["state"] = "running"
        current = installed_runtime()
        if current and not options.get("reinstall_engine") and not current.get("manual"):
            step.update(state="skipped", note=f"already installed ({current.get('backend')}, {current.get('tag')})")
            return current.get("backend", "unknown")
        if current and current.get("manual") and not options.get("reinstall_engine"):
            step.update(state="skipped", note="using your own llama.cpp" if current.get("external") else "using the engine already in bin/llama")
            return current.get("backend", "unknown")
        tried = []
        for cand in rplan["candidates"]:
            step["note"] = f"{cand['backend']}: {cand['why']}"
            stage = BIN_LLAMA / f".stage-{cand['backend']}-{os.getpid()}"
            shutil.rmtree(stage, ignore_errors=True)
            try:
                names = [cand["asset"], *cand["extra"]]
                for n in names:
                    a = _asset(n)
                    archive = BIN_LLAMA / ".downloads" / a["url"].rsplit("/", 1)[-1]
                    step.update(done=0, total=a["size"], note=f"{cand['backend']}: downloading {archive.name}")
                    await download(a["url"], archive, step, a["sha256"], a["size"])
                    step["note"] = f"{cand['backend']}: unpacking {archive.name}"
                    await asyncio.to_thread(_safe_extract, archive, stage)
                    archive.unlink(missing_ok=True)
                main = _find_server(stage)
                if not main:
                    raise SetupError("The engine download did not contain llama-server.")
                # the CUDA runtime libraries unpack into their own folder: they must sit beside llama-server to be found
                for extra in stage.iterdir():
                    if extra.is_dir() and extra != main.parent and extra.name.startswith("cudart"):
                        for f in extra.iterdir():
                            shutil.move(str(f), main.parent / f.name)
                        shutil.rmtree(extra, ignore_errors=True)
                await asyncio.to_thread(_make_executable, main.parent)
                step["note"] = f"{cand['backend']}: checking which devices it can use"
                devices = await asyncio.to_thread(list_devices, main)
                gpu = [d for d in (devices or []) if not d["id"].upper().startswith("CPU")]
                if devices is None:
                    tried.append(f"{cand['backend']}: the program would not start")
                elif cand["backend"] in ("cuda", "vulkan", "metal") and not gpu:
                    tried.append(f"{cand['backend']}: it found no usable GPU (is the driver installed?)")
                else:
                    final = BIN_LLAMA / f"{MANIFEST['llama']['tag']}-{cand['backend']}"
                    shutil.rmtree(final, ignore_errors=True)
                    stage.replace(final)
                    (BIN_LLAMA / "current.json").write_text(json.dumps({"dir": final.name, "tag": MANIFEST["llama"]["tag"], "backend": cand["backend"], "asset": cand["asset"].replace("{tag}", MANIFEST["llama"]["tag"])}))
                    step.update(state="done", done=step["total"], note=f"{cand['backend']}: " + (", ".join(d["name"] for d in gpu) if gpu else "no GPU, running on the CPU"))
                    return cand["backend"]
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        raise SetupError("No llama.cpp build worked on this computer: " + "; ".join(tried))

    async def _image(self, step: dict, p: dict, options: dict):
        step["state"] = "running"
        sd = sd_plan(p["hardware"])
        for name in sd["assets"]:
            a = MANIFEST["sd"]["assets"][name]
            archive = BIN_SD.parent / ".downloads" / name
            step.update(done=0, total=a["size"], note=f"downloading {name}")
            await download(a["url"], archive, step, a["sha256"], a["size"])
            await asyncio.to_thread(_safe_extract, archive, BIN_SD)
            archive.unlink(missing_ok=True)
        await asyncio.to_thread(_make_executable, BIN_SD)
        wanted = [(m["repo"], m["make"].format(q=p["image"]["parts"][k]["quant"])) for k, m in IMAGE_MODELS.items()] + [IMAGE_VAE]
        for repo, path in wanted:
            dest = MODELS / "image" / Path(path).name
            info = await hf_info(repo, path)
            if dest.is_file() and (not info["size"] or dest.stat().st_size == info["size"]):
                continue
            step.update(done=0, total=info["size"], note=f"downloading {dest.name}")
            await download(f"https://huggingface.co/{repo}/resolve/main/{quote(path)}", dest, step, info["sha256"], info["size"] or None)
        step.update(state="done", note="")
