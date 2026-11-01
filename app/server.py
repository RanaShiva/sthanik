"""Sthanik backend.

Owns the llama.cpp server process (one LFM3 model resident at a time, because
the BF16 weights fill most of a 6 GB GPU), reports GPU/model status, proxies
the OpenAI-compatible API + realtime WebSocket, and serves the static UI.
"""

import asyncio
import collections
import copy
import contextlib
import json
import os
import re
import hashlib
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import bringin
import chats
import diagrams
import docagent
import documents as docs
import hardware
import imagegen
import installer
import images
import memory
import paths
import picturereview
import private
import registry
import safebrowsing
import webimages
from sysmetrics import cpu_stats
from scheduler import ScheduleError, Scheduler, preview as preview_schedule
from skills import SkillError, SkillStore
from tools import ToolError, ToolHub

ROOT = paths.HOME  # user data, models and binaries; see paths.py
STATIC = paths.STATIC

os.umask(0o077)  # everything the app creates is private to this user
for _k in ("HF_HUB_OFFLINE", "DO_NOT_TRACK", "HF_HUB_DISABLE_TELEMETRY"):
    os.environ.setdefault(_k, "1")


def lock_down_data():
    """Make the folders that hold chats, documents, pictures and settings readable by this user only."""
    for d in (ROOT / "data", ROOT / "config"):
        if d.is_dir():
            for p in [d, *d.rglob("*")]:
                try:
                    p.chmod(0o700 if p.is_dir() else 0o600)
                except OSError:
                    pass


UI_PORT = int(os.environ.get("LFM3_UI_PORT", "7860"))
LLAMA_PORT = int(os.environ.get("LFM3_LLAMA_PORT", "8081"))
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"
# llama-server allows any origin (CORS *), so give it a per-run key that only this backend knows.
LLAMA_KEY = secrets.token_urlsafe(24)
LLAMA_AUTH = {"Authorization": f"Bearer {LLAMA_KEY}"}
ALLOWED_HOSTS = {f"127.0.0.1:{UI_PORT}", f"localhost:{UI_PORT}"}

# Every model is the BF16 GGUF (the checkpoint's native precision); the
# projector carries the vision / audio encoders and the speech decoder.
# On a 6 GB card the speech-capable models keep their (large) projector on the
# CPU so every language-model layer stays on the GPU: measured 75 tok/s vs ~17-27
# with partial offload, and TTS runs ~2x real-time instead of ~1x.
# Context sizes were picked from measured VRAM on a 6 GB card (KV cache is cheap for these hybrid models;
# the weights and projector dominate): Omni/Audio 32K ~5.1 GB, Vision 24K ~5.6 GB, Text 32K ~4.6 GB.
MODELS = {
    "text": {
        "label": "LFM3-1.7B",
        "role": "Text chat",
        "description": "Text-in / text-out language model with a 256K-token context.",
        "repo": "LiquidAI/LFM3-1.7B-GGUF",
        "model": "models/text/LFM3-1.7B-BF16.gguf",
        "mmproj": None,
        "ctx": 32768,
        "inputs": ["text"],
        "outputs": ["text"],
        "args": [],
    },
    "vl": {
        "label": "LFM3-VL-2B",
        "role": "Vision & video",
        "description": "Understands images and video alongside text.",
        "repo": "LiquidAI/LFM3-VL-2B-GGUF",
        "model": "models/vl/LFM3-VL-2B-BF16.gguf",
        "mmproj": "models/vl/mmproj-LFM3-VL-2B-BF16.gguf",
        "ctx": 24576,
        "inputs": ["text", "image", "video"],
        "outputs": ["text"],
        "args": [],
    },
    "audio": {
        "label": "LFM3-Audio-2B",
        "role": "Speech",
        "description": "Speech recognition, text-to-speech and spoken conversation in 18+ languages.",
        "repo": "LiquidAI/LFM3-Audio-2B-GGUF",
        "model": "models/audio/LFM3-Audio-2B-BF16.gguf",
        "mmproj": "models/audio/mmproj-LFM3-Audio-2B-BF16.gguf",
        "ctx": 32768,
        "inputs": ["text", "audio"],
        "outputs": ["text", "audio"],
        "args": ["--reasoning", "off", "--no-mmproj-offload"],
    },
    "omni": {
        "label": "LFM3-Omni-2B",
        "role": "Omni-modal",
        "description": "Text, image, video and audio in; text and speech out.",
        "repo": "LiquidAI/LFM3-Omni-2B-GGUF",
        "model": "models/omni/LFM3-Omni-2B-BF16.gguf",
        "mmproj": "models/omni/mmproj-LFM3-Omni-2B-BF16.gguf",
        "ctx": 32768,
        "inputs": ["text", "image", "video", "audio"],
        "outputs": ["text", "audio"],
        "args": ["--reasoning", "off", "--no-mmproj-offload"],
    },
}

DEFAULT_MODELS = copy.deepcopy(MODELS)
registry.apply(MODELS, registry.load_overrides())  # config/models.json: other weights behind a slot
downloads = registry.Downloads()
setup = installer.Setup()


class LlamaProcess:
    """Lifecycle of the single llama-server child process."""

    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.key: str | None = None
        self.state = "idle"  # idle | loading | ready | error
        self.error: str | None = None
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.logs: collections.deque[str] = collections.deque(maxlen=400)
        self.lock = asyncio.Lock()
        self.variant = ""  # a non-default way the resident model was started (see vision_session)
        self.weights: str | None = None  # the slot whose files are loaded; differs from `key` for Text while safe browsing is off
        self._reader: asyncio.Task | None = None

    def snapshot(self):
        return {
            "key": self.key,
            "weights": self.weights,
            "state": self.state,
            "error": self.error,
            "load_seconds": round(self.ready_at - self.started_at, 1)
            if self.ready_at and self.started_at
            else None,
        }

    async def _pump_logs(self, proc):
        assert proc.stdout
        async for raw in proc.stdout:
            self.logs.append(raw.decode(errors="replace").rstrip())
        await proc.wait()
        if self.proc is proc and self.state in ("ready", "loading"):  # it died on its own (not stopped by us): do not keep saying "ready"
            oom = any("out of memory" in x for x in self.logs)
            self.state = "error"
            self.error = ("The model ran out of GPU memory and stopped. Close other programs using the GPU, or use a smaller context." if oom
                          else f"The model process exited unexpectedly: {' | '.join(list(self.logs)[-2:])[:300]}")

    async def stop(self):
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if self._reader:
            self._reader.cancel()
            self._reader = None
        self.key, self.weights, self.state, self.error, self.variant = None, None, "idle", None, ""
        self.started_at = self.ready_at = None

    async def load(self, key: str, ctx: int | None = None, extra_args: list[str] | None = None, variant: str = ""):
        async with self.lock:
            weights = safebrowsing.weights_slot(key)
            spec = MODELS[weights]
            if self.key == key and self.weights == weights and self.state in ("ready", "loading") and self.variant == variant:
                return
            await self.stop()
            runtime = installer.installed_runtime()
            if runtime is None:
                raise HTTPException(409, "The llama.cpp engine is not installed yet. Open Setup and install it.")
            model = ROOT / spec["model"]
            mmproj = ROOT / spec["mmproj"] if spec["mmproj"] else None
            missing = [p.name for p in (model, mmproj) if p and not p.exists()]
            if missing:
                raise HTTPException(409, f"Model files not downloaded yet: {', '.join(missing)}")

            args = [*spec["args"], *(extra_args or [])]
            weights_mb = model.stat().st_size / hardware.MIB
            # a projector that stays in system memory (--no-mmproj-offload) takes no GPU memory
            mm_gpu_mb = mmproj.stat().st_size / hardware.MIB if mmproj and "--no-mmproj-offload" not in args else 0
            cmd = [
                str(runtime["server"]),
                "-m", str(model),
                "--host", "127.0.0.1",
                "--port", str(LLAMA_PORT),
                "-c", str(ctx or spec["ctx"]),
                "-ngl", hardware.gpu_layers(weights_mb, mm_gpu_mb),  # how many layers go on the GPU: all when they fit, none without a GPU, else llama.cpp fits them
                "-a", spec["label"],
                "--no-webui",
                "--offline",  # the model server never needs the network
                "--parallel", "1",
                "--api-key", LLAMA_KEY,
                *args,
            ]
            if mmproj:
                cmd += ["-mm", str(mmproj)]

            env = installer.runtime_env(runtime["dir"])
            self.logs.clear()
            self.logs.append("$ " + " ".join(cmd).replace(LLAMA_KEY, "***"))
            self.key, self.weights, self.state, self.error, self.variant = key, weights, "loading", None, variant
            self.started_at, self.ready_at = time.time(), None
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, env=env, cwd=runtime["dir"], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **paths.QUIET
            )
            self._reader = asyncio.create_task(self._pump_logs(self.proc))
            asyncio.create_task(self._await_ready(self.proc, key))

    async def _await_ready(self, proc, key):
        async with httpx.AsyncClient() as client:
            deadline = time.time() + 300
            while time.time() < deadline:
                if proc.returncode is not None:
                    break
                try:
                    r = await client.get(f"{LLAMA_URL}/health", timeout=2)
                    if r.status_code == 200:
                        if self.proc is proc:
                            self.state, self.ready_at = "ready", time.time()
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        if self.proc is proc:
            tail = " | ".join(list(self.logs)[-3:])
            self.state = "error"
            self.error = f"llama-server exited early: {tail}" if proc.returncode is not None else "Timed out while loading"


llama = LlamaProcess()


def gpu_stats():
    smi = hardware._nvidia_smi()
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, **paths.QUIET,
        ).stdout.strip().splitlines()[0]
        name, used, total, util, temp, power = [x.strip() for x in out.split(",")]
        return {
            "name": name, "mem_used_mb": int(used), "mem_total_mb": int(total),
            "util_pct": int(util), "temp_c": int(temp),
            "power_w": float(power) if power.replace(".", "", 1).isdigit() else None,
        }
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None


async def context_stats():
    """Context window use of the resident model: llama-server's /slots (prompt size + tokens produced so far)."""
    if llama.state != "ready":
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{LLAMA_URL}/slots", headers=LLAMA_AUTH, timeout=0.8)
        slot = r.json()[0]
        n_ctx = int(slot["n_ctx"])
        nxt = (slot.get("next_token") or [{}])[0]
        prompt = int(slot.get("n_prompt_tokens") or 0)
        busy = bool(slot.get("is_processing")) and prompt > 0  # a slot with no prompt is just waiting
        decoded = int(nxt.get("n_decoded") or 0) if busy else 0
        params = slot.get("params") or {}
        max_new = params.get("max_tokens") or params.get("n_predict")
        return {"n_ctx": n_ctx, "used": min(prompt + decoded, n_ctx), "prompt": prompt, "decoded": decoded, "busy": busy,
                "reading": busy and decoded == 0, "max_new": int(max_new) if isinstance(max_new, int) and max_new > 0 else None}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None


skills = SkillStore()
import skills as _skills_module  # noqa: E402
import style  # noqa: E402
style.SKILLS_DIR = _skills_module.SKILLS_DIR  # the look of every file comes from its skill's style block
hub = ToolHub(skills)
# The scheduler waits for quiet before swapping models, so the proxy reports chat traffic here.
activity = {"in_flight": 0, "last": time.time()}


async def _free_gpu():
    if llama.state != "idle":
        await llama.stop()  # the image model needs the whole card; the next chat request reloads the language model


def _image_busy(delta: int):
    activity["in_flight"] += delta  # keeps the scheduler from loading a model mid-picture
    activity["last"] = time.time()


imagegen_ = imagegen.ImageGen(_free_gpu, _image_busy)
hub.imagegen = imagegen_


async def _complete(body: dict) -> str:
    """One non-streaming completion from whatever model is resident (loading the text model if none is)."""
    if imagegen_.state.get("busy"):
        raise RuntimeError("A picture is being made and needs the whole GPU.")
    _image_busy(1)  # keep the scheduler away while we use the model
    try:
        if llama.state in ("idle", "error"):
            await llama.load("text")
        deadline = time.time() + 300
        while llama.state == "loading" and time.time() < deadline:
            await asyncio.sleep(0.3)
        if llama.state != "ready":
            raise RuntimeError(llama.error or "The model is not available.")
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as client:
            r = await client.post(f"{LLAMA_URL}/v1/chat/completions", headers=LLAMA_AUTH, json={"reasoning_effort": "none", **body})
        if r.status_code != 200:
            raise RuntimeError(f"The model answered {r.status_code}")
        return r.json()["choices"][0]["message"]["content"] or ""
    finally:
        _image_busy(-1)


async def llm_json(messages: list, schema: dict, max_tokens: int = 3000) -> dict:
    out = await _complete({"messages": messages, "temperature": 0.2, "max_tokens": max_tokens,
                           "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}})
    return json.loads(out)


async def _vision_json(messages: list, schema: dict, max_tokens: int = 200) -> dict:
    """A JSON answer from the resident vision-capable model (only valid inside vision_session)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5)) as client:
            r = await client.post(f"{LLAMA_URL}/v1/chat/completions", headers=LLAMA_AUTH, json={
                "messages": messages, "temperature": 0.1, "max_tokens": max_tokens, "reasoning_effort": "none",
                "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}})
    except httpx.TransportError as e:
        raise RuntimeError(llama.error or "The vision model stopped responding (likely out of GPU memory). Free GPU memory and try again.") from e
    if r.status_code != 200:
        raise RuntimeError(f"The vision model answered {r.status_code}: {r.text[:200]}")
    return json.loads(r.json()["choices"][0]["message"]["content"] or "{}")


@contextlib.asynccontextmanager
async def vision_session():
    """Make a model that can see resident for the duration (Vision, or Omni when that is what is loaded), then bring back the model that was loaded.
    Only one model fits on the card, so this swaps; the scheduler and chat proxy are kept off while it runs."""
    if imagegen_.state.get("busy"):
        raise RuntimeError("A picture is being made and needs the whole GPU.")
    _image_busy(1)
    prev = llama.key if llama.state in ("ready", "loading") else None
    try:
        if not (llama.key in ("vl", "omni") and llama.state == "ready"):
            try:
                # Lighter than the chat configuration: the review only needs small pictures, and the projector on the CPU plus a short
                # context leaves room next to the desktop and browser on a 6 GB card.
                await llama.load("vl", ctx=8192, extra_args=["--no-mmproj-offload"], variant="review")
            except HTTPException as e:
                raise RuntimeError(str(e.detail)) from e
            deadline = time.time() + 300
            while llama.state == "loading" and time.time() < deadline:
                await asyncio.sleep(0.3)
            if llama.state != "ready":
                raise RuntimeError(llama.error or "The vision model did not start.")
        yield
    finally:
        try:
            if prev and prev not in ("vl", "omni"):
                await llama.load(prev)
            elif prev is None:
                async with llama.lock:
                    await llama.stop()
        finally:
            _image_busy(-1)


async def review_pictures(name: str, fix: bool = True, only: list[str] | None = None) -> dict:
    async with vision_session():
        return await picturereview.review(name, _vision_json, fix, only)


async def llm_text(messages: list, max_tokens: int = 3000) -> str:
    return await _complete({"messages": messages, "temperature": 0.3, "max_tokens": max_tokens, "repeat_penalty": 1.1})


hub.llm = llm_json
hub.review_pictures = review_pictures
hub.llm_text = llm_text
scheduler = Scheduler(llama=llama, hub=hub, skills=skills, models=MODELS, llama_url=LLAMA_URL, llama_auth=LLAMA_AUTH, activity=activity)


@contextlib.asynccontextmanager
async def lifespan(_app):
    lock_down_data()
    private.sweep()  # a crashed private session may have left its RAM-only folder behind
    asyncio.get_running_loop().run_in_executor(None, installer.refresh_profile)  # what the engine can use right now, for the GPU layer count
    await hub.start()
    await scheduler.start()
    yield
    setup.cancel()
    await scheduler.stop()
    await hub.stop()
    await llama.stop()
    private.stop()


app = FastAPI(title="Sthanik", lifespan=lifespan)

HARDENING_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "microphone=(self), camera=(self), geolocation=(), payment=(), usb=(), interest-cohort=()",
}


def _inline_script_hashes() -> str:
    """CSP hashes for the page's own inline scripts, so 'unsafe-inline' is not needed for scripts."""
    html = (STATIC / "index.html").read_text()
    return " ".join(f"'sha256-{__import__('base64').b64encode(hashlib.sha256(m.encode()).digest()).decode()}'"
                    for m in re.findall(r"<script>(.*?)</script>", html, re.S))


# The page may talk only to this server: no remote images, scripts, fonts, media, frames or connections, so nothing in a model's answer, a
# search result or a document can make the browser contact (or send data to) another host.
UI_CSP = ("default-src 'none'; script-src 'self' {hashes}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
          "media-src 'self' blob: data:; connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; frame-src 'none'; "
          "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Reject DNS-rebinding (foreign Host) and cross-site requests (foreign Origin)."""
    if request.headers.get("host", "") not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
    if "\x00" in request.url.path or "\x00" in request.url.query:  # no file or setting name holds one; it would end in a 500 from the file system
        return JSONResponse({"detail": "Invalid character in the address."}, status_code=400)
    if (not safebrowsing.is_safe() and request.method in ("POST", "PUT", "PATCH", "DELETE")
            and request.url.path.startswith(("/api/schedules", "/api/runs")) and request.url.path != "/api/schedules/preview"):
        return JSONResponse({"detail": "Schedules are paused while safe browsing is off: their prompts and results would be written to disk."}, status_code=409)
    docs.TURN.set((request.headers.get("x-sthanik-turn") or "")[:40] or None)  # which chat turn this request belongs to (for grouped undo)
    resp = await call_next(request)
    for k, v in HARDENING_HEADERS.items():
        resp.headers.setdefault(k, v)
    if request.url.path.startswith(("/api/", "/llama/")):
        resp.headers.setdefault("Cache-Control", "no-store")  # chats, pictures and search results must not sit in the browser cache
    return resp


@app.get("/api/status")
async def status():
    models = {}
    for key, spec in MODELS.items():
        files = [ROOT / spec["model"], *([ROOT / spec["mmproj"]] if spec["mmproj"] else [])]
        present = all(f.exists() for f in files)
        size = sum(f.stat().st_size for f in files if f.exists())
        models[key] = {
            **{k: spec[k] for k in ("label", "role", "description", "repo", "ctx", "inputs", "outputs")}, "backs": spec.get("backs"),
            "precision": registry.precision(spec["model"]),
            "downloaded": present,
            "size_gb": round(size / 1e9, 2),
        }
    return {"llama": llama.snapshot(), "gpu": gpu_stats(), "cpu": cpu_stats(), "ctx": await context_stats(), "image": {**imagegen_.snapshot(), "batch": dict(docagent.progress)}, "models": models, "safe_browsing": safebrowsing.is_safe(), "private_ram": private.ram_backed(), "can_quit": hasattr(app.state, "shutdown"), "slots": safebrowsing.slots(),
            "scheduler": {"running": len(scheduler.snapshot()["running"]), "last_finished": scheduler.last_finished},
            "setup": {"needed": installer.needed(), "running": setup.running()}}


@app.post("/api/quit")
async def quit_app():
    stop = getattr(app.state, "shutdown", None)
    if not stop:
        raise HTTPException(404, "This copy was not started by the launcher, so it cannot stop itself.")
    asyncio.get_running_loop().call_later(0.4, stop)  # after this answer has been sent
    return {"ok": True}


# ---- first-run setup: detect this computer, install the engine and the models it can run
def _quants(raw) -> dict[str, str]:
    """{"text": "Q8_0", ...}: only known parts and plain precision names."""
    ok = set(installer.COMPONENTS) | set(installer.IMAGE_MODELS)
    return {str(k): str(v) for k, v in (raw.items() if isinstance(raw, dict) else []) if k in ok and re.fullmatch(r"[A-Za-z0-9_\-]{2,20}", str(v))}


def _own(raw) -> dict:
    """The user's own engine and weights, as the Setup page sends them: {"engine": path, "models": {part: {"model": path, "mmproj": path}}}."""
    if not isinstance(raw, dict):
        return {}
    path = lambda v: str(v).strip()[:1024] if isinstance(v, str) and v.strip() and "\x00" not in v else ""  # noqa: E731
    models = {}
    for k, v in (raw.get("models") or {}).items() if isinstance(raw.get("models"), dict) else []:
        if k in installer.COMPONENTS and isinstance(v, dict) and path(v.get("model")):
            models[k] = {"model": path(v.get("model")), "mmproj": path(v.get("mmproj")) or None}
    out = {}
    if path(raw.get("engine")):
        out["engine"] = path(raw["engine"])
    if models:
        out["models"] = models
    return out


@app.get("/api/setup/plan")
async def setup_plan(quant: str = "", quants: str = ""):
    try:
        try:
            per_part = _quants(json.loads(quants)) if quants else {}
        except ValueError:
            per_part = {}
        return await installer.plan({"quant": quant, "quants": per_part})
    except (installer.SetupError, httpx.HTTPError) as e:
        raise HTTPException(502, f"Could not prepare the plan: {e}")


@app.get("/api/setup/detect")
async def setup_detect():
    """llama-server programs and .gguf files already on this computer, to offer as choices on the Setup page."""
    return await asyncio.to_thread(bringin.detect, registry.MODELS_DIR)


@app.get("/api/setup/status")
async def setup_status():
    return {**setup.snapshot(), "needed": installer.needed()}


@app.post("/api/setup/start")
async def setup_start(request: Request):
    body = await _body(request)
    opts = {"components": [str(c) for c in body.get("components") or []] if isinstance(body.get("components"), list) else [], "quant": str(body.get("quant") or ""),
            "quants": _quants(body.get("quants")), "image": bool(body.get("image")), "own": _own(body.get("own")),
            "engine": body.get("engine", True) is not False, "reinstall_engine": bool(body.get("reinstall_engine"))}
    try:
        return setup.start(opts)
    except installer.SetupError as e:
        raise HTTPException(409, str(e))


@app.post("/api/setup/cancel")
async def setup_cancel():
    setup.cancel()
    return setup.snapshot()


@app.get("/api/hardware")
async def hardware_get():
    return {"detected": await asyncio.to_thread(hardware.detect), "profile": hardware.load_profile(), "runtime": (lambda r: {k: r[k] for k in ("tag", "backend")} if r else None)(installer.installed_runtime())}


@app.get("/api/logs")
async def logs():
    return {"lines": list(llama.logs)}


# ---- changing which weights back a slot
def _slot_view(key: str) -> dict:
    cur, dflt = MODELS[key], DEFAULT_MODELS[key]
    pick = lambda m: {k: m.get(k) for k in registry.EDITABLE}  # noqa: E731
    return {"current": pick(cur), "default": pick(dflt), "overridden": pick(cur) != pick(dflt), "needs_projector": bool(dflt.get("mmproj"))}


@app.get("/api/models/registry")
async def registry_get():
    image_roles = {"diffusion": ("Z-Image Turbo", "Draws the picture"), "llm": ("Qwen3-4B text encoder", "Reads the prompt"), "vae": ("Image decoder (VAE)", "Turns the result into pixels")}
    image_files = [{"path": f.relative_to(ROOT).as_posix(), "size": f.stat().st_size, "label": image_roles.get(k, (f.name, ""))[0], "role": image_roles.get(k, ("", ""))[1]}
                   for k, f in imagegen.files().items() if f.is_file()]
    return {"models": {k: _slot_view(k) for k in MODELS}, "files": registry.discover(), "image_files": image_files, "downloads": downloads.snapshot()}


@app.put("/api/models/{key}/registry")
async def registry_put(key: str, request: Request):
    if key not in MODELS:
        raise HTTPException(404, "unknown model")
    try:
        ov = registry.validate(DEFAULT_MODELS[key], await _body(request))
    except registry.RegistryError as e:
        _bad(e)
    saved = registry.load_overrides()
    saved[key] = {**saved.get(key, {}), **ov}
    registry.save_overrides(saved)
    MODELS[key].update(ov)
    if llama.weights == key and llama.state != "idle":
        await llama.stop()  # the running process still has the old weights
    return {"models": {k: _slot_view(k) for k in MODELS}}


@app.delete("/api/models/{key}/registry")
async def registry_reset(key: str):
    if key not in MODELS:
        raise HTTPException(404, "unknown model")
    saved = registry.load_overrides()
    saved.pop(key, None)
    registry.save_overrides(saved)
    MODELS[key].update({k: DEFAULT_MODELS[key].get(k) for k in registry.EDITABLE})
    if llama.weights == key and llama.state != "idle":
        await llama.stop()
    return {"models": {k: _slot_view(k) for k in MODELS}}


@app.delete("/api/models/files")
async def model_file_delete(request: Request):
    b = await _body(request)
    in_use = {m[k] for m in (*MODELS.values(), *DEFAULT_MODELS.values()) for k in ("model", "mmproj") if m.get(k)}
    try:
        freed = registry.delete_file(b.get("path", "") if isinstance(b, dict) else "", in_use)
    except registry.RegistryError as e:
        _bad(e)
    return {"freed": freed, "files": registry.discover()}


@app.get("/api/models/downloads")
async def downloads_list():
    return {"downloads": downloads.snapshot()}


@app.post("/api/models/downloads")
async def downloads_start(request: Request):
    b = await _body(request)
    try:
        return downloads.start(str(b.get("repo", "")).strip(), str(b.get("file", "")).strip())
    except registry.RegistryError as e:
        _bad(e)


@app.delete("/api/models/downloads/{did}")
async def downloads_cancel(did: str):
    downloads.cancel(did)
    return {"downloads": downloads.snapshot()}


@app.post("/api/models/{key}/load")
async def load_model(key: str):
    if key not in MODELS:
        raise HTTPException(404, "unknown model")
    if MODELS[key].get("backs"):
        raise HTTPException(400, f"{MODELS[MODELS[key]['backs']]['label']} loads this model itself when safe browsing is switched off.")
    if imagegen_.state.get("busy"):
        raise HTTPException(409, "A picture is being made and needs the whole GPU. Try again in a minute.")
    await llama.load(key)
    return llama.snapshot()


@app.get("/api/safe-browsing")
async def safe_browsing_get():
    return {"safe": safebrowsing.is_safe()}


@app.put("/api/safe-browsing")
async def safe_browsing_put(request: Request):
    b = await _body(request)
    if not isinstance(b, dict) or not isinstance(b.get("safe"), bool):
        raise HTTPException(400, "Send {\"safe\": true|false}.")
    safe = b["safe"]
    if safe != safebrowsing.is_safe():
        async with llama.lock:
            if (llama.state != "idle" and activity["in_flight"] > 0) or imagegen_.state.get("busy"):
                raise HTTPException(409, "Something is still being generated. Switch again when it has finished.")
            if safe:
                safebrowsing.set_safe(True)
                private.stop()  # wipes the RAM-only session
            else:
                try:
                    private.start()  # private storage first: never switch the search filter off without it
                except OSError as e:
                    raise HTTPException(500, f"Could not create private storage, so the private session stays off ({e.strerror or e}).")
                safebrowsing.set_safe(False)
            await llama.stop()  # drops the resident model, its prompt cache and its log: the next request loads the right weights
            llama.logs.clear()
            webimages.CANDIDATES.clear()
    return {"safe": safebrowsing.is_safe()}


@app.post("/api/models/unload")
async def unload_model():
    async with llama.lock:
        await llama.stop()
    return llama.snapshot()


@app.get("/api/tools")
async def list_tools():
    return hub.describe()


@app.post("/api/tools/call")
async def call_tool(request: Request):
    body = await _body(request)
    ctx = body.get("context") or {}
    return await hub.call(str(body.get("name", "")), body.get("arguments") or {}, str(ctx.get("active_document") or "") or None, ctx.get("only_sections") or None, str(ctx.get("turn") or "")[:40] or None)


@app.post("/api/tools/files-root")
async def set_files_root(request: Request):
    body = await _body(request)
    try:
        hub.set_files_root((body.get("path") or "").strip() or None)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return hub.describe()


@app.post("/api/mcp/reload")
async def mcp_reload():
    await hub.reload_mcp()
    return hub.describe()


@app.post("/api/mcp/servers")
async def mcp_add(request: Request):
    body = await _body(request)
    try:
        if body.get("command"):
            await hub.add_local_server(str(body.get("name", "")), str(body.get("command", "")), str(body.get("args", "")))
        else:
            await hub.add_http_server(str(body.get("name", "")), str(body.get("url", "")))
    except ToolError as e:
        raise HTTPException(400, str(e))
    return hub.describe()


@app.post("/api/mcp/servers/{name}/enabled")
async def mcp_enable(name: str, request: Request):
    body = await _body(request)
    try:
        await hub.set_server_enabled(name, bool(body.get("enabled")))
    except ToolError as e:
        raise HTTPException(400, str(e))
    return hub.describe()


@app.delete("/api/mcp/servers/{name}")
async def mcp_remove(name: str):
    try:
        await hub.remove_server(name)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return hub.describe()


def _bad(e: Exception):
    raise HTTPException(400, str(e))


async def _body(request: Request) -> dict:
    """The JSON object a request carries. A body that is not one is the caller's mistake (400), not a server error; so is a NUL
    character, which no file name can hold."""
    raw = await request.body()
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "The request body must be valid JSON.") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "The request body must be a JSON object.")
    if b"\\u0000" in raw:
        raise HTTPException(400, "The request contains an invalid character.")
    return data


# ---- skills
def skills_payload() -> dict:
    return {"skills": skills.list(), "removed_builtin": skills.state.get("removed", [])}


@app.get("/api/skills")
async def skills_list():
    return skills_payload()


@app.post("/api/skills/match")
async def skills_match(request: Request):
    b = await _body(request)
    return {"skills": skills.match(str(b.get("text", ""))[:20000], exclude=set(b.get("exclude") or []))}


_DIAGRAM_WORDS = re.compile(r"\b(diagram|flow ?chart|sequence|timeline|mind ?map|org(?:anisation|anization)? chart|gantt|process flow|workflow|architecture)\b", re.I)


@app.post("/api/skills/writing")
async def skills_writing(request: Request):
    """Writing rules for plan mode, by output format (the skill that makes that kind of file), plus diagrams when the text asks for one."""
    b = await _body(request)
    fmt, kind, text = str(b.get("format") or ""), str(b.get("kind") or ""), str(b.get("text") or "")[:4000]
    names = []
    if kind == "slides":
        names.append("pptx")
    elif kind == "document":
        names.append({"pdf": "pdf", "docx": "docx", "html": "html"}.get(fmt, ""))
    if _DIAGRAM_WORDS.search(text):
        names.append("mermaid")
    return {"names": [n for n in names if n], "text": skills.writing_guidance([n for n in names if n])}


@app.post("/api/skills/restore-defaults")
async def skills_restore():
    skills.restore_builtin()
    return skills_payload()


@app.get("/api/skills/{name}")
async def skills_get(name: str):
    try:
        return skills.get(name)
    except SkillError as e:
        raise HTTPException(404, str(e))


@app.get("/api/skills/{name}/file")
async def skills_file(name: str, path: str):
    try:
        return {"path": path, "text": skills.read_file(name, path)}
    except SkillError as e:
        raise HTTPException(404, str(e))


@app.post("/api/skills")
async def skills_create(request: Request):
    b = await _body(request)
    try:
        skills.save_custom(str(b.get("name", "")), str(b.get("description", "")), str(b.get("body", "")), b.get("triggers") if isinstance(b.get("triggers"), list) else None)
    except SkillError as e:
        _bad(e)
    return skills_payload()


@app.post("/api/skills/install")
async def skills_install(request: Request):
    b = await _body(request)
    try:
        names = await skills.install_github(str(b.get("repo", "")), str(b.get("path", "")), str(b.get("ref", "")), bool(b.get("overwrite")))
    except SkillError as e:
        _bad(e)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach GitHub: {e}")
    return {"installed": names, **skills_payload()}


@app.post("/api/skills/{name}/enabled")
async def skills_enable(name: str, request: Request):
    b = await _body(request)
    try:
        skills.set_enabled(name, bool(b.get("enabled")))
    except SkillError as e:
        _bad(e)
    return skills_payload()


@app.delete("/api/skills/{name}")
async def skills_delete(name: str):
    try:
        skills.delete(name)
    except SkillError as e:
        _bad(e)
    return skills_payload()


# ---- conversations
@app.get("/api/chats")
async def chats_list(scope: str = ""):
    return {"chats": chats.list_chats(scope or None), "projects": chats.projects()}


@app.get("/api/chats/{cid}")
async def chats_get(cid: str):
    try:
        return chats.get(cid)
    except chats.ChatError as e:
        raise HTTPException(404, str(e))


@app.put("/api/chats/{cid}")
async def chats_save(cid: str, request: Request):
    raw = await request.body()
    if len(raw) > chats.MAX_BYTES:
        raise HTTPException(413, "This conversation is too large to save (attachments over ~200 MB).")
    try:
        return chats.save(cid, json.loads(raw))
    except (chats.ChatError, ValueError) as e:
        _bad(e)


@app.post("/api/chats/{cid}/project")
async def chats_project(cid: str, request: Request):
    try:
        return chats.set_project(cid, str((await _body(request)).get("project", "")))
    except chats.ChatError as e:
        raise HTTPException(404, str(e))


@app.post("/api/projects")
async def projects_create(request: Request):
    try:
        return {"name": chats.create_project(str((await _body(request)).get("name", "")))}
    except chats.ChatError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/rename")
async def projects_rename(request: Request):
    body = await _body(request)
    try:
        return {"changed": chats.rename_project(str(body.get("from", "")), str(body.get("to", "")))}
    except chats.ChatError as e:
        raise HTTPException(400, str(e))


@app.post("/api/chats/{cid}/title")
async def chats_rename(cid: str, request: Request):
    try:
        return chats.rename(cid, str((await _body(request)).get("title", "")))
    except chats.ChatError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/chats/{cid}")
async def chats_delete(cid: str):
    try:
        chats.delete(cid)
    except chats.ChatError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.get("/api/chats/{cid}/media/{name}")
async def chats_media(cid: str, name: str):
    try:
        path, mime = chats.media_path(cid, name)
    except chats.ChatError as e:
        raise HTTPException(404, str(e))
    return FileResponse(path, media_type=mime, headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=3600"})


# ---- documents
@app.get("/api/documents")
async def documents_list():
    return {"documents": docs.list_documents(), "pdf": docs.find_chrome() is not None}


@app.post("/api/documents/export")
async def documents_export(request: Request):
    b = await _body(request)
    try:
        return await docs.export(str(b.get("title") or "Document"), str(b.get("markdown", "")), str(b.get("format", "pdf")))
    except docs.DocError as e:
        _bad(e)


@app.get("/api/documents/{name}/source")
async def documents_source(name: str):
    try:
        return docs.get_source(name)
    except docs.DocError as e:
        raise HTTPException(404, str(e))


@app.put("/api/documents/{name}/source")
async def documents_update(name: str, request: Request):
    b = await _body(request)
    try:
        return await docs.update_source(name, str(b.get("source", "")), (str(b["title"]) if b.get("title") else None))
    except docs.DocError as e:
        _bad(e)


@app.post("/api/documents/{name}/edit")
async def documents_edit(name: str, request: Request):
    b = await _body(request)
    try:
        r = await docagent.edit_document(name, str(b.get("instruction", "")), llm_json, llm_text)
    except (docagent.AgentError, docs.DocError) as e:
        _bad(e)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"summary": r["summary"], "applied": r["applied"], "failed": r["failed"], "info": r["info"]}


@app.post("/api/documents/{name}/verify-edit")
async def documents_verify_edit(name: str, request: Request):
    b = await _body(request)
    try:
        return await docagent.finish_edit(name, str(b.get("instruction", "")), str(b.get("before", "")), llm_json, llm_text)
    except (docagent.AgentError, docs.DocError) as e:
        _bad(e)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/documents/{name}/pictures")
async def documents_pictures(name: str, request: Request):
    b = await _body(request)
    try:
        return await docagent.attach_pictures(name, [str(x) for x in (b.get("images") or [])], (str(b["section"]) if b.get("section") else None), int(b.get("count") or 0))
    except (docagent.AgentError, docs.DocError) as e:
        _bad(e)


@app.get("/api/documents/{name}/audit")
async def documents_audit(name: str):
    import docaudit
    try:
        return {"issues": docaudit.audit(docs.get_source(name)["source"])}
    except docs.DocError as e:
        raise HTTPException(404, str(e))


@app.post("/api/documents/{name}/tidy")
async def documents_tidy(name: str):
    import docaudit
    try:
        cur = docs.get_source(name)["source"]
        new, fixed = docaudit.tidy(cur)
        info = await docs.update_source(name, new, by="model") if new != cur else None
        return {"fixed": fixed, "remaining": docaudit.audit(new), "info": info}
    except docs.DocError as e:
        _bad(e)


@app.post("/api/documents/{name}/remove-pictures")
async def documents_remove_pictures(name: str, request: Request):
    b = await _body(request)
    try:
        return await docagent.remove_pictures(name, (str(b["section"]) if b.get("section") else None), (str(b["image"]) if b.get("image") else None), [str(x) for x in (b.get("only") or [])] or None)
    except (docagent.AgentError, docs.DocError) as e:
        _bad(e)


@app.post("/api/documents/{name}/review-pictures")
async def documents_review_pictures(name: str, request: Request):
    b = await _body(request) if request.headers.get("content-length", "0") != "0" else {}
    try:
        r = await review_pictures(name, bool(b.get("fix", True)), [str(x) for x in (b.get("only") or [])] or None)
        return {**r, "summary": picturereview.summary(r)}
    except (docagent.AgentError, docs.DocError) as e:
        _bad(e)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/documents/{name}/illustrate")
async def documents_illustrate(name: str, request: Request):
    b = await _body(request)
    try:
        r = await docagent.illustrate_document(name, int(b["count"]) if b.get("count") else None, llm_json, imagegen_, str(b.get("source") or "auto"), bool(b.get("replace")), [str(x) for x in (b.get("only") or [])] or None)
    except (docagent.AgentError, docs.DocError) as e:
        _bad(e)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return r


@app.post("/api/documents/{name}/undo")
async def documents_undo(name: str):
    try:
        return await docs.revert(name)
    except docs.DocError as e:
        _bad(e)


@app.get("/api/documents/{name}")
async def documents_get(name: str, inline: int = 0):
    try:
        p = docs.doc_path(name)
    except docs.DocError as e:
        raise HTTPException(404, str(e))
    ext = p.suffix.lstrip(".")
    # Only PDFs may open inline; everything else downloads, and nothing can run script on our origin.
    return FileResponse(p, media_type=docs.MIME[ext], filename=p.name,
                        content_disposition_type="inline" if (inline and ext == "pdf") else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "no-cache"})


@app.delete("/api/documents/{name}")
async def documents_delete(name: str):
    try:
        docs.delete_document(name)
    except docs.DocError as e:
        raise HTTPException(404, str(e))
    return {"documents": docs.list_documents()}


# ---- memory
@app.get("/api/memory")
async def memory_get():
    return memory.snapshot()


@app.get("/api/memory/prompt")
async def memory_prompt():
    return {"text": memory.prompt_block()}


@app.post("/api/memory")
async def memory_add(request: Request):
    b = await _body(request)
    try:
        return memory.add(str(b.get("text", "")), source=str(b.get("source") or "user")[:12])
    except memory.MemoryError_ as e:
        _bad(e)


@app.put("/api/memory/settings")
async def memory_settings(request: Request):
    try:
        memory.set_enabled(bool((await _body(request)).get("enabled")))
    except memory.MemoryError_ as e:
        _bad(e)
    return memory.snapshot()


@app.put("/api/memory/{mid}")
async def memory_update(mid: str, request: Request):
    b = await _body(request)
    try:
        return memory.update(mid, b.get("text"), b.get("pinned"))
    except memory.MemoryError_ as e:
        _bad(e)


@app.delete("/api/memory/{mid}")
async def memory_delete(mid: str):
    try:
        memory.delete(mid)
    except memory.MemoryError_ as e:
        raise HTTPException(404, str(e))
    return memory.snapshot()


# ---- pictures from the web
@app.post("/api/webimages/search")
async def webimages_search(request: Request):
    b = await _body(request)
    try:
        return {"results": await webimages.search(str(b.get("query", "")), 12)}
    except webimages.WebImageError as e:
        _bad(e)


PICTURE_FOR_ANSWER = (
    "Decide whether a picture would help the person who asked this. Say yes when the question is about a specific real place, landmark, building, "
    "city, person, animal, plant, object, artwork, map or event and what it looks like matters (\"tell me about X\", \"what does X look like\", "
    "\"how did X look\"). Say no for code, maths, writing help, opinions, advice, news, definitions, small talk and anything abstract. "
    "When yes, `query` is 2-6 words naming the thing to photograph (include the place name; add words like ruins, temple, aerial or map if the "
    "question is about how it looked). Answer in JSON.")


@app.post("/api/webimages/for-answer")
async def webimages_for_answer(request: Request):
    """After a chat answer that showed no picture: decide whether one would help and, if so, fetch a real one."""
    b = await _body(request)
    q, a = str(b.get("question", ""))[:600], str(b.get("answer", ""))[:700]
    if len(q.split()) < 2:
        return {"images": []}
    schema = {"type": "object", "properties": {"picture": {"type": "boolean"}, "query": {"type": "string"}}, "required": ["picture", "query"]}
    try:
        d = await llm_json([{"role": "system", "content": PICTURE_FOR_ANSWER}, {"role": "user", "content": f"Question: {q}\n\nAnswer given: {a}"}], schema, 120)
    except Exception:  # noqa: BLE001 - a missing picture must never break a chat
        return {"images": []}
    query = " ".join(str(d.get("query") or "").split())[:100]
    if not d.get("picture") or not query:
        return {"images": []}
    try:
        saved, problems = await webimages.find(query, 1)
    except webimages.WebImageError:
        return {"images": []}
    return {"query": query, "images": saved, "problems": problems[:3]}


@app.get("/api/webimages/thumb/{cid}")
async def webimages_thumb(cid: str):
    try:
        data, ctype = await webimages.thumbnail(cid)
    except webimages.WebImageError as e:
        raise HTTPException(404, str(e))
    return Response(data, media_type=ctype, headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=1800"})


@app.post("/api/webimages/save")
async def webimages_save(request: Request):
    b = await _body(request)
    try:
        return await webimages.save(str(b.get("id", "")))
    except webimages.WebImageError as e:
        _bad(e)


# ---- diagrams
@app.post("/api/diagrams/check")
async def diagrams_check(request: Request):
    b = await _body(request)
    info, err = await diagrams.render(str(b.get("code", "")))
    return {"ok": err is None, "error": err}


# ---- images (generated pictures and rendered diagrams)
@app.post("/api/images/generate")
async def images_generate(request: Request):
    b = await _body(request)
    try:
        return await imagegen_.generate(str(b.get("prompt", "")), str(b.get("size") or "square"), int(b.get("steps") or 8),
                                        int(b["seed"]) if str(b.get("seed", "")).lstrip("-").isdigit() else None)
    except imagegen.ImageError as e:
        _bad(e)


@app.get("/api/images/status")
async def images_status():
    return {**imagegen_.snapshot(), "batch": dict(docagent.progress)}


@app.post("/api/images/cancel")
async def images_cancel():
    imagegen_.cancel()
    return {"ok": True}


@app.get("/api/images")
async def images_list():
    return {"images": images.list_images()}


@app.get("/api/images/{name}")
async def images_get(name: str):
    p = images.image_path(name)
    if p is None:
        raise HTTPException(404, "No such image.")
    return FileResponse(p, media_type=images.MIME[p.suffix.lstrip(".")],
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=86400"})


@app.delete("/api/images/{name}")
async def images_delete(name: str):
    try:
        images.delete_image(name)
    except FileNotFoundError:
        raise HTTPException(404, "No such image.")
    return {"images": images.list_images()}


# ---- scheduled tasks
@app.get("/api/schedules")
async def schedules_list():
    return scheduler.snapshot()


@app.post("/api/schedules/preview")
async def schedules_preview(request: Request):
    b = await _body(request)
    try:
        return {"next": preview_schedule(b.get("schedule") or {})}
    except (ScheduleError, ValueError, KeyError, TypeError) as e:
        return {"next": [], "error": str(e)}


@app.post("/api/schedules")
async def schedules_create(request: Request):
    try:
        scheduler.create(await _body(request))
    except (ScheduleError, ValueError, TypeError) as e:
        _bad(e)
    return scheduler.snapshot()


@app.put("/api/schedules/{tid}")
async def schedules_update(tid: str, request: Request):
    try:
        scheduler.update(tid, await _body(request))
    except (ScheduleError, ValueError, TypeError) as e:
        _bad(e)
    return scheduler.snapshot()


@app.delete("/api/schedules/{tid}")
async def schedules_delete(tid: str):
    try:
        scheduler.delete(tid)
    except ScheduleError as e:
        raise HTTPException(404, str(e))
    return scheduler.snapshot()


@app.post("/api/schedules/{tid}/enabled")
async def schedules_enable(tid: str, request: Request):
    try:
        scheduler.set_enabled(tid, bool((await _body(request)).get("enabled")))
    except ScheduleError as e:
        _bad(e)
    return scheduler.snapshot()


@app.post("/api/schedules/{tid}/run")
async def schedules_run(tid: str):
    try:
        scheduler.run_now(tid)
    except ScheduleError as e:
        _bad(e)
    return scheduler.snapshot()


@app.get("/api/runs")
async def runs_list(task: str = ""):
    return {"runs": [r for r in scheduler.runs if not task or r["task_id"] == task][:60]}


@app.post("/api/runs/{rid}/cancel")
async def runs_cancel(rid: str):
    scheduler.cancel_run(rid)
    return {"ok": True}


@app.delete("/api/runs/{rid}")
async def runs_delete(rid: str):
    scheduler.delete_run(rid)
    return {"ok": True}


HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding"}


@app.api_route("/llama/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy(path: str, request: Request):
    if llama.state != "ready":
        return JSONResponse({"error": {"message": f"No model is ready (state: {llama.state})"}}, status_code=503)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP | {"authorization", "origin", "cookie"}}
    headers.update(LLAMA_AUTH)
    client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5))
    activity["in_flight"] += 1
    try:
        upstream = await client.send(
            client.build_request(request.method, f"{LLAMA_URL}/{path}", params=request.query_params,
                                 headers=headers, content=await request.body()),
            stream=True,
        )
    except BaseException:
        activity["in_flight"] -= 1
        activity["last"] = time.time()
        await client.aclose()
        raise

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            activity["in_flight"] -= 1
            activity["last"] = time.time()
            await upstream.aclose()
            await client.aclose()

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP | {"content-encoding"}}
    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)


@app.websocket("/ws/realtime")
async def realtime_bridge(ws: WebSocket):
    if ws.headers.get("host", "") not in ALLOWED_HOSTS or (
        ws.headers.get("origin") and ws.headers["origin"].split("://", 1)[-1] not in ALLOWED_HOSTS
    ):
        await ws.close(code=1008)
        return
    await ws.accept()
    if llama.state != "ready":
        await ws.send_text(json.dumps({"type": "error", "error": {"message": "No model is ready"}}))
        await ws.close()
        return
    query = ws.url.query
    url = f"ws://127.0.0.1:{LLAMA_PORT}/v1/realtime" + (f"?{query}" if query else "")
    try:
        async with websockets.connect(url, max_size=64 * 1024 * 1024, additional_headers=LLAMA_AUTH) as upstream:

            async def up():
                while True:
                    await upstream.send(await ws.receive_text())

            async def down():
                async for msg in upstream:
                    await ws.send_text(msg if isinstance(msg, str) else msg.decode())

            tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except (WebSocketDisconnect, websockets.ConnectionClosed, OSError):
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.get("/")
async def index():
    # Remote images are never loaded (model and web text could otherwise embed trackers); framing is refused.
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if not safebrowsing.is_safe():
        html = html.replace('<html lang="en"', '<html lang="en" data-private', 1)  # the purple private look is there from the first paint
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Content-Security-Policy": UI_CSP.format(hashes=_inline_script_hashes(), port=UI_PORT)})


class NoCacheStatic(StaticFiles):
    """Serve the UI uncached so edits show up on a plain refresh."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store"
        return resp


app.mount("/", NoCacheStatic(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=UI_PORT, log_level="warning")
