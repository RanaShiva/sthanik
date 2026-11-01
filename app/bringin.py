"""Use what the user already has: a llama.cpp build and model files that were not downloaded by this app.

Nothing here is taken on trust. An engine is asked for its version and its devices, and a model is opened by the engine itself:
`trial()` starts the program on a free local port, waits for it to say it is healthy and asks for a few words. Only a pair that did
that is written into the settings (see installer.Setup._own).
"""

from __future__ import annotations

import asyncio
import collections
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path
from typing import Callable

import httpx

import paths

EXE = ".exe" if sys.platform == "win32" else ""
VERSION_RE = re.compile(r"build\s+(\d+)|version:\s*(\d+)(?![\d.])")  # "version: 0.8.0-dev (build 11979, commit ...)" today, "version: 11979 (abc)" before


class BringInError(Exception):
    """Something the user can act on; the message is safe to show."""


# ---------------------------------------------------------------- reading a GGUF header
_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def gguf_info(path: Path) -> dict:
    """{"arch", "name"} from the start of a GGUF file. Raises BringInError when it is not one (or is cut short).

    The `general.*` keys come first by convention, so reading stops at the first array (the vocabulary) and never loads the weights."""
    try:
        with path.open("rb") as f:
            if f.read(4) != b"GGUF":
                raise BringInError(f"{path.name} is not a GGUF model file.")
            head = f.read(20)
            if len(head) < 20:
                raise BringInError(f"{path.name} is cut short (a download that did not finish?).")
            version, _tensors, kvs = struct.unpack("<IQQ", head)
            if version not in (2, 3):
                raise BringInError(f"{path.name} is GGUF version {version}, which this app does not read.")
            out: dict = {}
            for _ in range(min(kvs, 64)):
                (n,) = struct.unpack("<Q", f.read(8))
                if n > 256:
                    break
                key = f.read(n).decode("utf-8", "replace")
                (t,) = struct.unpack("<I", f.read(4))
                if t == 8:
                    (n,) = struct.unpack("<Q", f.read(8))
                    if n > 4096:
                        break
                    value = f.read(n).decode("utf-8", "replace")
                    if key in ("general.architecture", "general.name"):
                        out[key.removeprefix("general.")] = value
                elif t in _FIXED:
                    f.read(_FIXED[t])
                else:
                    break  # an array: the general keys are over
                if "architecture" in out and "name" in out:
                    break
    except (OSError, struct.error) as e:
        raise BringInError(f"{path.name} could not be read ({e.strerror if isinstance(e, OSError) and e.strerror else 'damaged file'}).")
    if "architecture" not in out:
        raise BringInError(f"{path.name} has no model architecture in it; it may be damaged.")
    return {"arch": out["architecture"], "name": out.get("name", "")}


def check_model_file(path: Path, projector: bool) -> dict:
    """The file exists, is a GGUF and is the right kind (a projector is architecture `clip`, a language model is not)."""
    if not path.is_file():
        raise BringInError(f"There is no file at {path}.")
    if path.suffix.lower() != ".gguf":
        raise BringInError(f"{path.name} is not a .gguf file.")
    info = gguf_info(path)
    if projector and info["arch"] != "clip":
        raise BringInError(f"{path.name} is not a projector file (its architecture is '{info['arch']}'). A projector is named mmproj-….gguf.")
    if not projector and info["arch"] == "clip":
        raise BringInError(f"{path.name} is a projector file, not the model itself. Give it as the projector instead.")
    return info


# ---------------------------------------------------------------- the engine
def find_server(given: str) -> Path:
    """The llama-server program named by a path to it or to the folder that holds it."""
    if not given or "\x00" in given:
        raise BringInError("Give the path to llama-server, or to the folder it is in.")
    p = Path(given).expanduser()
    if p.is_dir():
        direct = p / f"llama-server{EXE}"
        found = direct if direct.is_file() else next((c for c in sorted(p.rglob(f"llama-server{EXE}")) if c.is_file()), None)
        if not found:
            raise BringInError(f"There is no {direct.name} in {p}.")
        p = found
    if not p.is_file():
        raise BringInError(f"There is nothing at {p}.")
    if p.name != f"llama-server{EXE}":
        raise BringInError(f"{p.name} is not llama-server{EXE}. Pick the llama-server program (llama-cli and the others are not the same).")
    if sys.platform != "win32" and not os.access(p, os.X_OK):
        raise BringInError(f"{p} is not executable (chmod +x).")
    return p.absolute()


def engine_facts(server: Path, env: dict) -> dict:
    """{"build": 11979 or None, "text": first line of --version}. Raises when the program does not run at all."""
    try:
        r = subprocess.run([str(server), "--version"], capture_output=True, text=True, timeout=60, env=env, cwd=server.parent, **paths.QUIET)
    except (OSError, subprocess.SubprocessError) as e:
        raise BringInError(f"{server} would not start ({e}). A missing library or a build for another system is the usual cause.")
    text = (r.stdout + r.stderr).strip()
    m = VERSION_RE.search(text)
    if r.returncode != 0 and not m:
        raise BringInError(f"{server.name} --version failed: {text.splitlines()[-1][:200] if text else 'no output'}")
    return {"build": int(m.group(1) or m.group(2)) if m else None, "text": next((ln for ln in text.splitlines() if "version" in ln), text[:120])}


def backend_of(devices: list[dict]) -> str:
    ids = " ".join(d["id"].upper() for d in devices if not d["id"].upper().startswith("CPU"))
    for key, name in (("CUDA", "cuda"), ("VULKAN", "vulkan"), ("MTL", "metal"), ("METAL", "metal"), ("ROCM", "rocm"), ("HIP", "rocm"), ("SYCL", "sycl")):
        if key in ids:
            return name
    return "cpu"


# ---------------------------------------------------------------- loading a model to see it work
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def trial(server: Path, env: dict, model: Path, mmproj: Path | None, timeout: float = 240) -> str:
    """Start the engine on the model and ask for a few words. Returns what it said; raises BringInError with the engine's own words otherwise.

    It runs on the CPU with a small context: this is a check that the pair works, not a speed test."""
    port, key = _free_port(), secrets.token_urlsafe(16)
    cmd = [str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(port), "-c", "512", "-ngl", "0", "--no-webui", "--offline",
           "--parallel", "1", "--api-key", key]
    if mmproj:
        cmd += ["-mm", str(mmproj), "--no-mmproj-offload"]
    tail: collections.deque[str] = collections.deque(maxlen=30)
    proc = await asyncio.create_subprocess_exec(*cmd, env=env, cwd=server.parent, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **paths.QUIET)

    async def pump():
        assert proc.stdout
        async for raw in proc.stdout:
            tail.append(raw.decode(errors="replace").rstrip())

    reader = asyncio.create_task(pump())

    def why() -> str:
        lines = [ln for ln in tail if ln.strip()]
        errs = [ln for ln in lines if re.search(r"error|failed|unknown model architecture|unsupported", ln, re.I)]
        return " | ".join((errs or lines)[-3:])[:400] or "no output"

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            end = asyncio.get_running_loop().time() + timeout
            while True:
                if proc.returncode is not None:
                    raise BringInError(f"The engine stopped while loading {model.name}: {why()}")
                if asyncio.get_running_loop().time() > end:
                    raise BringInError(f"{model.name} did not finish loading in {int(timeout)} seconds: {why()}")
                try:
                    if (await c.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            if mmproj:
                return "loaded"
            r = await c.post(f"http://127.0.0.1:{port}/completion", headers={"Authorization": f"Bearer {key}"}, json={"prompt": "The capital of France is", "n_predict": 6, "temperature": 0}, timeout=120)
            if r.status_code != 200:
                raise BringInError(f"The model loaded but did not answer (HTTP {r.status_code}): {r.text[:200]}")
            return str(r.json().get("content", "")).strip()
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        reader.cancel()


# ---------------------------------------------------------------- putting a file in the models folder
def bring_in(src: Path, models_dir: Path, progress: Callable[[int], None] | None = None) -> tuple[Path, bool]:
    """(path inside models/, whether this call made it). A file already inside models/ stays where it is. Otherwise a hard link is made
    (no extra space, instant) and when the file is on another disk, a copy."""
    src = src.resolve()
    models_dir = models_dir.resolve()
    if models_dir in src.parents:
        return src, False
    dest_dir = models_dir / "custom"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    n = 1
    while dest.exists():
        if dest.stat().st_size == src.stat().st_size and (os.path.samefile(dest, src) or _same_start(dest, src)):
            return dest, False
        n += 1
        dest = dest_dir / f"{src.stem}-{n}{src.suffix}"
    try:
        os.link(src, dest)
        return dest, True
    except OSError:
        pass
    part = dest.with_suffix(dest.suffix + ".part")
    done = 0
    try:
        with src.open("rb") as r, part.open("wb") as w:
            while chunk := r.read(8 * 1024 * 1024):
                w.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done)
        part.replace(dest)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise BringInError(f"Could not copy {src.name} into the models folder ({e.strerror or e}).")
    return dest, True


def _same_start(a: Path, b: Path) -> bool:
    """Same size and same first and last megabyte: the same file for our purpose (re-running setup does not copy it again)."""
    try:
        with a.open("rb") as fa, b.open("rb") as fb:
            if fa.read(1 << 20) != fb.read(1 << 20):
                return False
            fa.seek(max(a.stat().st_size - (1 << 20), 0))
            fb.seek(max(b.stat().st_size - (1 << 20), 0))
            return fa.read() == fb.read()
    except OSError:
        return False


# ---------------------------------------------------------------- what is on this computer
def detect(models_dir: Path) -> dict:
    """Suggestions for the setup page: llama-server programs and .gguf files in the usual places. Reads names and sizes only."""
    home = Path.home()
    engines: dict[str, dict] = {}

    def add(p: Path | None, source: str):
        try:
            if p and p.is_file() and p.name == f"llama-server{EXE}" and (sys.platform == "win32" or os.access(p, os.X_OK)):
                engines.setdefault(str(p.resolve()), {"path": str(p), "folder": str(p.parent), "source": source})
        except OSError:
            pass

    on_path = shutil.which("llama-server")
    add(Path(on_path) if on_path else None, "on your PATH")
    for d in ("llama.cpp/build/bin", "src/llama.cpp/build/bin", "code/llama.cpp/build/bin", "Projects/llama.cpp/build/bin", "llama.cpp", ".local/bin"):
        add(home / d / f"llama-server{EXE}", "a llama.cpp folder")
    for d in ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/opt/llama.cpp/bin"):
        add(Path(d) / f"llama-server{EXE}", "installed on the system")

    found: list[dict] = []
    seen: set[str] = set()
    own = models_dir.resolve()
    roots = [home / ".cache" / "huggingface" / "hub", home / ".cache" / "lm-studio" / "models", home / ".lmstudio" / "models", home / "models", home / "Downloads"]
    for root in roots:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for cur, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if len(Path(cur).parts) - base < 6 and not d.startswith(".git")]
            for name in files:
                if not name.lower().endswith(".gguf"):
                    continue
                p = Path(cur) / name
                try:
                    real = p.resolve()
                    if own in real.parents or str(real) in seen:
                        continue
                    seen.add(str(real))
                    found.append({"path": str(p), "name": name, "size": real.stat().st_size, "projector": "mmproj" in name.lower()})
                except OSError:
                    continue
                if len(found) >= 300:
                    break
            if len(found) >= 300:
                break
    found.sort(key=lambda f: f["name"].lower())
    return {"engines": list(engines.values()), "files": found}
