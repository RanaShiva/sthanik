"""Locate and run a headless Chrome/Chromium/Brave (used for PDF export and for drawing Mermaid diagrams)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import paths

CHROME_NAMES = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "brave-browser")


def _installed_paths() -> list[Path]:
    if sys.platform == "darwin":
        return [Path(f"/Applications/{n}.app/Contents/MacOS/{n}") for n in ("Google Chrome", "Chromium", "Brave Browser", "Microsoft Edge")]
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
        rel = ("Google/Chrome/Application/chrome.exe", "Chromium/Application/chrome.exe", "BraveSoftware/Brave-Browser/Application/brave.exe", "Microsoft/Edge/Application/msedge.exe")
        return [Path(r) / x for r in roots if r for x in rel]
    return []


def find_chrome() -> str | None:
    for n in CHROME_NAMES:
        if (p := shutil.which(n)):
            return p
    return next((str(p) for p in _installed_paths() if p.is_file()), None)


async def run(args: list[str], timeout: float = 60) -> tuple[bytes, bytes]:
    """Run Chrome headless with a throwaway profile; returns (stdout, stderr). Raises RuntimeError on timeout / no browser."""
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("This needs Chrome, Chromium, Brave or Edge installed.")
    with tempfile.TemporaryDirectory(prefix="sthanik-chrome-") as profile:
        proc = await asyncio.create_subprocess_exec(
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", f"--user-data-dir={profile}", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **paths.QUIET,
        )
        try:
            return await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("The browser timed out.")
