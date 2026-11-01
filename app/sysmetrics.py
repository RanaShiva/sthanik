"""CPU / memory readings for the sidebar: straight from /proc and /sys on Linux, through psutil on Windows and macOS."""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import psutil
except ImportError:  # the Linux readings below do not need it
    psutil = None

LINUX = sys.platform.startswith("linux")

_prev: tuple[int, int] | None = None
_NAME: str | None = None
_TEMP_CHIPS = ("k10temp", "coretemp", "zenpower", "cpu_thermal")


def _cpu_name() -> str:
    global _NAME
    if _NAME is None:
        _NAME = "CPU"
        if not LINUX:
            import hardware
            _NAME = hardware.cpu_name()
            return _NAME
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    n = line.split(":", 1)[1].strip()
                    for junk in ("(R)", "(TM)", "with Radeon Graphics", "CPU", "Processor", "  "):
                        n = n.replace(junk, " ")
                    _NAME = " ".join(n.split())
                    break
        except OSError:
            pass
    return _NAME


def _cpu_pct() -> float | None:
    """Busy share of all cores since the previous call (first call: since boot)."""
    global _prev
    if not LINUX:
        return round(psutil.cpu_percent(interval=None), 1) if psutil else None
    try:
        f = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    except (OSError, ValueError, IndexError):
        return None
    idle, total = f[3] + f[4], sum(f)
    prev, _prev = _prev, (idle, total)
    if not prev or total <= prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def _cpu_temp() -> float | None:
    if not LINUX:
        return None  # no portable way to read it
    for hw in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            if (hw / "name").read_text().strip() not in _TEMP_CHIPS:
                continue
            temps = {}
            for t in hw.glob("temp*_input"):
                label = (hw / t.name.replace("_input", "_label"))
                temps[label.read_text().strip() if label.exists() else t.name] = int(t.read_text()) / 1000
            for want in ("Tctl", "Package id 0", "Tdie"):
                if want in temps:
                    return round(temps[want], 1)
            if temps:
                return round(max(temps.values()), 1)
        except (OSError, ValueError):
            continue
    return None


def _memory() -> tuple[int, int] | None:
    if not LINUX:
        if not psutil:
            return None
        m = psutil.virtual_memory()
        return (m.total - m.available) // 1048576, m.total // 1048576
    try:
        m = {k: int(v.split()[0]) for k, v in (ln.split(":", 1) for ln in Path("/proc/meminfo").read_text().splitlines())}
        return (m["MemTotal"] - m["MemAvailable"]) // 1024, m["MemTotal"] // 1024
    except (OSError, ValueError, KeyError):
        return None


def cpu_stats() -> dict:
    mem = _memory()
    return {"name": _cpu_name(), "cores": os.cpu_count(), "util_pct": _cpu_pct(), "temp_c": _cpu_temp(),
            "load1": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
            "ram_used_mb": mem[0] if mem else None, "ram_total_mb": mem[1] if mem else None}
