"""Text-to-image with Z-Image-Turbo through stable-diffusion.cpp (Vulkan build), run one picture at a time.

The language models and the image model do not fit on a 6 GB GPU together, so a picture first frees the GPU (the server
stops the resident llama-server; the next chat request reloads it) and then runs `sd-cli` as a child process. Prompts are
passed as argv entries, never through a shell.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable

import images
import installer
import paths
import safebrowsing

ROOT = paths.HOME  # user data, models and binaries; see paths.py
SD_DIR = Path(os.environ.get("LFM3_SD_DIR") or ROOT / "bin" / "sd")
SD_CLI = SD_DIR / ("sd-cli.exe" if sys.platform == "win32" else "sd-cli")
IMG_DIR = ROOT / "models" / "image"


def files() -> dict[str, Path]:
    """The three model files in use. The precision of the first two is the one chosen in Setup (config/image.json)."""
    return {k: IMG_DIR / name for k, name in installer.image_selection().items()}


# Sizes are multiples of 64 and small enough for a laptop GPU with the weights offloaded to RAM.
SIZES = {"square": (768, 768), "landscape": (960, 576), "portrait": (576, 960), "wide": (1024, 512)}
TIMEOUT = 15 * 60
LOG = Path(os.environ.get("LFM3_SD_LOG") or ROOT / "data" / "sd.log")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PROGRESS = re.compile(r"(\d+)\s*/\s*(\d+)\s*-\s*[\d.]+\s*(?:s/it|it/s)")


class ImageError(Exception):
    """Something the user can act on (missing files, bad request, the run failed)."""


def missing() -> list[str]:
    out = [f"{SD_CLI.relative_to(ROOT)}"] if not SD_CLI.is_file() else []
    out += [str(p.relative_to(ROOT)) for p in files().values() if not p.is_file()]
    return out


def available() -> bool:
    return not missing() and os.access(SD_CLI, os.X_OK)


def _vulkan_device() -> str | None:
    """Index of the discrete GPU in Vulkan's list (so the integrated GPU or a software renderer is not picked)."""
    if os.environ.get("LFM3_SD_DEVICE"):
        return os.environ["LFM3_SD_DEVICE"]
    if not shutil.which("vulkaninfo"):
        return None
    try:
        out = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=15, **paths.QUIET).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    for m in re.finditer(r"GPU(\d+):(.*?)(?=GPU\d+:|\Z)", out, re.S):
        if "DISCRETE_GPU" in m.group(2):
            return m.group(1)
    return None


class ImageGen:
    def __init__(self, free_gpu: Callable[[], Awaitable[None]], busy: Callable[[int], None]):
        self.free_gpu, self.busy = free_gpu, busy
        self.lock = asyncio.Lock()
        self.proc: asyncio.subprocess.Process | None = None
        self.state: dict = {"busy": False}

    @staticmethod
    def available() -> bool:
        return available()

    @staticmethod
    def missing() -> list[str]:
        return missing()

    def snapshot(self) -> dict:
        s = dict(self.state)
        if s.get("busy"):
            s["elapsed"] = round(time.time() - s["started"], 1)
        return {"available": available(), "missing": missing(), "sizes": {k: list(v) for k, v in SIZES.items()}, **s}

    def cancel(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            self.state["cancelled"] = True

    async def generate(self, prompt: str, size: str = "square", steps: int = 8, seed: int | None = None) -> dict:
        prompt = " ".join((prompt or "").split())[:1000].lstrip("-").strip()
        if not prompt:
            raise ImageError("Describe the picture you want.")
        if not available():
            raise ImageError("Image generation is not set up: missing " + ", ".join(missing() or [str(SD_CLI.relative_to(ROOT)) + " (not executable)"]) + ".")
        if size not in SIZES:
            raise ImageError(f"Unknown size '{size}'. Choose one of: {', '.join(SIZES)}.")
        steps = max(1, min(int(steps or 8), 20))
        seed = secrets.randbelow(2**31) if seed is None or int(seed) < 0 else int(seed)
        w, h = SIZES[size]
        if self.lock.locked():
            raise ImageError("A picture is already being made. Wait for it to finish.")
        async with self.lock:
            self.state = {"busy": True, "prompt": prompt[:200], "started": time.time(), "stage": "Freeing the GPU…", "step": 0, "steps": steps}
            self.busy(1)
            try:
                await self.free_gpu()
                with tempfile.TemporaryDirectory(prefix="sthanik-sd-") as tmp:
                    out = Path(tmp) / "out.png"
                    f = files()
                    cmd = [str(SD_CLI), "--diffusion-model", str(f["diffusion"]), "--vae", str(f["vae"]), "--llm", str(f["llm"]),
                           "-p", prompt, "--cfg-scale", "1.0", "--steps", str(steps), "-W", str(w), "-H", str(h), "--seed", str(seed),
                           "--offload-to-cpu", "--diffusion-fa", "-o", str(out)]
                    env = installer.runtime_env(SD_DIR)
                    dev = _vulkan_device()
                    if dev is not None:
                        env["GGML_VK_VISIBLE_DEVICES"] = dev
                    self.state["stage"] = "Loading the image model…"
                    self.proc = await asyncio.create_subprocess_exec(*cmd, cwd=tmp, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, **paths.QUIET)
                    text = await self._watch(self.proc)
                    rc = self.proc.returncode
                    self._write_log(cmd, rc, text)
                    if self.state.get("cancelled"):
                        raise ImageError("Cancelled.")
                    data = out.read_bytes() if out.is_file() else b""
                    # The Vulkan driver can crash while the program is shutting down, after the picture is already on disk:
                    # a complete PNG is a success whatever the exit code was.
                    if not data.startswith(PNG_MAGIC):
                        lines = [ln for ln in text.splitlines() if ln.strip() and not PROGRESS.search(ln)]
                        how = f"was killed by signal {-rc}" if rc and rc < 0 else f"stopped with exit code {rc}"
                        raise ImageError(f"The image model {how} without making a picture. " + " / ".join(ln.strip()[:160] for ln in lines[-2:]) + " (full output: data/sd.log)")
                return images.save_image(data, prompt[:40], "png", {"kind": "image", "prompt": prompt, "seed": seed, "size_px": [w, h], "steps": steps})
            finally:
                self.proc = None
                self.state = {"busy": False}
                self.busy(-1)

    @staticmethod
    def _write_log(cmd: list[str], rc: int | None, text: str):
        if not safebrowsing.is_safe():  # the log quotes the prompt
            return
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(ln for ln in text.replace("\r", "\n").splitlines() if ln.strip())[-60000:]
            LOG.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  exit code {rc}\n$ {' '.join(cmd[:3])} …\n\n{body}\n")
        except OSError:
            pass

    async def _watch(self, proc: asyncio.subprocess.Process) -> str:
        """Read the child's stderr (progress arrives with carriage returns), tracking the sampling step. Returns all of it."""
        chunks: list[str] = []

        async def pump():
            while chunk := await proc.stderr.read(4096):
                text = chunk.decode("utf-8", "replace")
                if sum(map(len, chunks)) < 400_000:
                    chunks.append(text)
                for m in PROGRESS.finditer(text):
                    self.state.update({"step": int(m.group(1)), "steps": int(m.group(2)), "stage": "Drawing"})
                if "generating image" in text.lower() or "sampling" in text.lower():
                    self.state.setdefault("stage", "Drawing")

        try:
            await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ImageError("The image model took too long and was stopped.")
        return "".join(chunks)
