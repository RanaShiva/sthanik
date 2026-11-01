"""What this computer is, and which llama.cpp build and model files suit it.

`detect()` reads the operating system, CPU, memory and graphics cards. `choose_runtime()` turns that into an ordered list of llama.cpp builds
to try (the best GPU build first, the CPU build last); the installer keeps the first one whose `llama-server --list-devices` really shows a
usable GPU, so a missing driver falls through to the next build instead of failing later. `choose_quant()` picks the weights' precision from
the memory the models would run in, and `gpu_layers()` decides how many layers go to the GPU when a model loads.

Nothing here claims more than it measured: a card whose memory cannot be read has `vram_mb: None`, and the loader then lets llama.cpp
fit the layers itself (`-ngl auto`) instead of guessing.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import paths

GIB = 1024 ** 3
MIB = 1024 ** 2

# Memory a loaded model needs beyond its weights (context cache, compute buffers). Measured on an RTX 3060 6 GB with a 32K window: Text
# 4.6 GB for 3.5 GB of weights, Omni 5.1 GB with its projector kept in system memory. Rounded up.
OVERHEAD_MB = 1600


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **paths.QUIET).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def os_name() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x86_64" if m in ("x86_64", "amd64") else m


# ---------------------------------------------------------------- memory and CPU
def ram_mb() -> int | None:
    try:
        import psutil
        return int(psutil.virtual_memory().total / MIB)
    except Exception:  # noqa: BLE001 - psutil missing or unreadable: try the OS directly
        pass
    try:
        if os_name() == "linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
        if os_name() == "macos":
            return int(int(_run(["sysctl", "-n", "hw.memsize"]).strip()) / MIB)
    except (OSError, ValueError):
        pass
    return None


def cpu_name() -> str:
    try:
        if os_name() == "macos":
            return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.processor() or "CPU"
        if os_name() == "linux":
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    return " ".join(line.split(":", 1)[1].split())
    except OSError:
        pass
    return platform.processor() or "CPU"


# ---------------------------------------------------------------- graphics cards
def _nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os_name() == "windows":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("SystemRoot")):
            for rel in ("NVIDIA Corporation/NVSMI/nvidia-smi.exe", "System32/nvidia-smi.exe"):
                if base and (Path(base) / rel).is_file():
                    return str(Path(base) / rel)
    return None


def _nvidia() -> list[dict]:
    smi = _nvidia_smi()
    if not smi:
        return []
    out = _run([smi, "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader,nounits"])
    if not out.strip() or "not a valid field" in out.lower():  # drivers older than ~510 have no compute_cap
        out = _run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    # The newest CUDA the installed driver can run: "CUDA Version: 12.4" on most drivers, "CUDA UMD Version: 13.4" on the newest
    m = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", _run([smi]))
    cuda = (int(m.group(1)), int(m.group(2))) if m else None
    gpus = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 3 or not p[1].isdigit():
            continue
        try:
            cc = float(p[3]) if len(p) > 3 else None
        except ValueError:
            cc = None
        gpus.append({"vendor": "nvidia", "name": p[0], "vram_mb": int(p[1]), "driver": p[2], "cuda": cuda, "compute_cap": cc, "integrated": False})
    return gpus


def _linux_other() -> list[dict]:
    """AMD, Intel and other cards from sysfs (names from lspci when it is installed). NVIDIA cards are read by nvidia-smi instead."""
    names: dict[str, str] = {}
    for line in _run(["lspci", "-mm"]).splitlines():
        parts = re.findall(r'"([^"]*)"|(\S+)', line)
        f = [a or b for a, b in parts]
        if len(f) >= 4 and re.search(r"VGA|3D|Display", f[1]):
            names[f[0]] = f"{f[2]} {f[3]}".replace("Corporation", "").replace("[AMD/ATI]", "AMD").strip()
    gpus = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        dev = card / "device"
        try:
            vendor = (dev / "vendor").read_text().strip().lower()
        except OSError:
            continue
        kind = {"0x1002": "amd", "0x8086": "intel"}.get(vendor)
        if not kind:
            continue
        slot = dev.resolve().name.removeprefix("0000:")
        vram = None
        try:
            vram = int(int((dev / "mem_info_vram_total").read_text()) / MIB)  # amdgpu only
        except (OSError, ValueError):
            pass
        name = next((n for s, n in names.items() if s.endswith(slot)), None) or ("AMD graphics" if kind == "amd" else "Intel graphics")
        # an APU reports only the small memory block set aside for it in the firmware; it really shares system memory
        gpus.append({"vendor": kind, "name": name, "vram_mb": vram, "integrated": kind == "intel" or (vram is not None and vram < 2048)})
    return gpus


def _windows_other() -> list[dict]:
    out = _run(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterCompatibility | ConvertTo-Json -Compress"], 15)
    try:
        rows = json.loads(out)
    except ValueError:
        return []
    gpus = []
    for r in rows if isinstance(rows, list) else [rows]:
        text = f"{r.get('Name', '')} {r.get('AdapterCompatibility', '')}".lower()
        kind = "amd" if ("amd" in text or "radeon" in text) else "intel" if "intel" in text else None
        if kind:  # NVIDIA is read by nvidia-smi; Win32_VideoController cannot report memory above 4 GB, so none is claimed here
            gpus.append({"vendor": kind, "name": r.get("Name") or kind, "vram_mb": None, "integrated": kind == "intel" or "graphics" in text and "radeon rx" not in text})
    return gpus


def _apple() -> list[dict]:
    if os_name() == "macos" and arch() == "arm64":
        return [{"vendor": "apple", "name": cpu_name(), "vram_mb": ram_mb(), "integrated": True, "unified": True}]
    return []


def gpus() -> list[dict]:
    found = _nvidia() + _apple()
    if os_name() == "linux":
        found += _linux_other()
    elif os_name() == "windows":
        found += _windows_other()
    return found


def detect() -> dict:
    cards = gpus()
    return {"os": os_name(), "arch": arch(), "cpu": cpu_name(), "threads": os.cpu_count() or 1, "ram_mb": ram_mb(), "gpus": cards,
            "disk_free_mb": int(shutil.disk_usage(_existing(paths.HOME)).free / MIB)}


def _existing(p: Path) -> Path:
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


# ---------------------------------------------------------------- which llama.cpp build
def _best_gpu(hw: dict) -> dict | None:
    """The card to run on: NVIDIA first, then Apple, then the discrete card with the most memory, then any other."""
    order = {"nvidia": 0, "apple": 1, "amd": 2, "intel": 3}
    return min(hw["gpus"], key=lambda g: (order.get(g["vendor"], 9), g.get("integrated", False), -(g.get("vram_mb") or 0)), default=None)


def choose_runtime(hw: dict) -> list[dict]:
    """Ordered candidates: [{"backend", "asset", "extra": [asset...], "why"}]. The last one is always the CPU build.

    CUDA builds: the driver must support the build's CUDA major version (nvidia-smi's "CUDA Version"); CUDA 13 no longer targets GPUs
    older than compute capability 7.5, so those fall to the 12.x build. Vulkan builds cover AMD, Intel and older NVIDIA cards; whether a
    Vulkan driver is really installed is settled afterwards by `llama-server --list-devices`.
    """
    os_, ar = hw["os"], hw["arch"]
    g = _best_gpu(hw)
    out: list[dict] = []

    def add(backend: str, asset: str, extra: tuple[str, ...] = (), why: str = ""):
        out.append({"backend": backend, "asset": asset, "extra": list(extra), "why": why})

    if os_ == "linux":
        suffix = "x64" if ar == "x86_64" else ar
        if g and g["vendor"] == "nvidia" and ar in ("x86_64", "arm64"):
            cuda, cc = (tuple(g["cuda"]) if g.get("cuda") else None), g.get("compute_cap")  # a list after a JSON round trip  # cuda None: the driver did not say, so try the builds and let --list-devices decide
            if (cuda is None or cuda[0] >= 13) and (cc is None or cc >= 7.5):
                add("cuda", f"llama-{{tag}}-bin-ubuntu-cuda-13.4-{suffix}.tar.gz", (f"cudart-llama-{{tag}}-bin-ubuntu-cuda-13.4-{suffix}.tar.gz",), "NVIDIA card, driver supports CUDA 13")
            if ar == "x86_64" and (cuda is None or cuda >= (12, 8)):
                add("cuda", "llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz", ("cudart-llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz",), "NVIDIA card, driver supports CUDA 12.8")
        if g and g["vendor"] != "apple":
            add("vulkan", f"llama-{{tag}}-bin-ubuntu-vulkan-{suffix}.tar.gz", (), f"{g['vendor'].upper() if g['vendor'] != 'nvidia' else 'NVIDIA'} card through Vulkan")
        add("cpu", f"llama-{{tag}}-bin-ubuntu-{suffix}.tar.gz", (), "CPU only")
    elif os_ == "windows":
        if g and g["vendor"] == "nvidia" and ar == "x86_64":
            cuda, cc = (tuple(g["cuda"]) if g.get("cuda") else None), g.get("compute_cap")  # a list after a JSON round trip
            if (cuda is None or cuda[0] >= 13) and (cc is None or cc >= 7.5):
                add("cuda", "llama-{tag}-bin-win-cuda-13.4-x64.zip", ("cudart-llama-bin-win-cuda-13.4-x64.zip",), "NVIDIA card, driver supports CUDA 13")
            if cuda is None or cuda >= (12, 4):
                add("cuda", "llama-{tag}-bin-win-cuda-12.4-x64.zip", ("cudart-llama-bin-win-cuda-12.4-x64.zip",), "NVIDIA card, driver supports CUDA 12.4")
        if g and g["vendor"] != "apple" and ar in ("x86_64", "arm64"):
            add("vulkan", f"llama-{{tag}}-bin-win-vulkan-{'x64' if ar == 'x86_64' else 'arm64'}.zip", (), "graphics card through Vulkan")
        add("cpu", f"llama-{{tag}}-bin-win-cpu-{'x64' if ar == 'x86_64' else 'arm64'}.zip", (), "CPU only")
    else:  # macOS: the Apple build includes Metal
        add("metal" if ar == "arm64" else "cpu", f"llama-{{tag}}-bin-macos-{'arm64' if ar == 'arm64' else 'x64'}.tar.gz", (), "Apple silicon (Metal)" if ar == "arm64" else "Intel Mac, CPU only")
    return out


# ---------------------------------------------------------------- which weights
QUANTS = ("BF16", "Q8_0", "Q4_K_M")
QUANT_NOTE = {"BF16": "full precision, the largest files", "Q8_0": "8-bit, about half the size", "Q4_K_M": "4-bit, about a third of the size"}


def model_budget_mb(hw: dict) -> tuple[int, str]:
    """Memory the models would run in, and where that is: the card's memory, or system memory (Apple silicon shares one pool)."""
    g = _best_gpu(hw)
    ram = hw.get("ram_mb") or 0
    if g and g.get("unified"):
        return int(ram * 0.65), "unified memory"  # macOS lets the GPU use roughly two thirds of it by default
    if g and g["vendor"] == "nvidia" and g.get("vram_mb"):
        return g["vram_mb"], "GPU memory"
    if g and g.get("vram_mb") and not g.get("integrated"):
        return g["vram_mb"], "GPU memory"
    return int(ram * 0.6), "system memory"  # CPU: leave room for the system, the browser and the context cache


def choose_quant(hw: dict) -> str:
    """BF16 when a 3.5 GB model plus its working memory fits comfortably (the precision Liquid ships), 8-bit when it just fits, else 4-bit."""
    budget, _ = model_budget_mb(hw)
    on_gpu = _best_gpu(hw) is not None
    if on_gpu and budget >= 3466 + OVERHEAD_MB:
        return "BF16"
    if budget >= 1846 + OVERHEAD_MB:
        return "Q8_0" if (on_gpu or (hw.get("ram_mb") or 0) >= 12 * 1024) else "Q4_K_M"
    return "Q4_K_M"


def choose_ctx(hw: dict, default: int) -> int:
    budget, _ = model_budget_mb(hw)
    return default if budget >= 5000 else min(default, 16384) if budget >= 3000 else min(default, 8192)


# ---------------------------------------------------------------- the saved profile and the GPU layer count
PROFILE = paths.HOME / "config" / "hardware.json"


def load_profile() -> dict:
    try:
        d = json.loads(PROFILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_profile(p: dict):
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=2))
    tmp.replace(PROFILE)


def gpu_layers(weights_mb: float, offloaded_extra_mb: float = 0) -> str:
    """The -ngl value for a model of this size. LFM3_NGL overrides. With no usable GPU: 0. When the card's memory is known and the model
    plus its working memory fits: all layers. Otherwise "auto", which has llama.cpp fit as many layers as the free memory allows."""
    if os.environ.get("LFM3_NGL"):
        return os.environ["LFM3_NGL"]
    prof = load_profile()
    if prof and not prof.get("gpu"):
        return "0"
    vram = prof.get("vram_mb")
    if vram and weights_mb + offloaded_extra_mb + OVERHEAD_MB <= vram:
        return "all"
    if not prof:  # no first-run setup has recorded this machine: keep the behaviour the app always had
        return "all"
    return "auto"
