"""The private session that runs while safe browsing is off.

Nothing the session produces may reach the disk: chats, documents, pictures and every temp file are redirected into a directory on
RAM-backed tmpfs (/dev/shm, mode 0700), and that directory is overwritten and removed when safe browsing comes back on, when the app
stops, and (for a crash) the next time it starts. Things that would write to the disk anyway are refused or skipped while it is active:
memory, schedules, the image-generation log and the server log. The browser reloads on every switch (see safebrowsing.js), so no page
content outlives it.

Honest limits: tmpfs pages can be swapped out under memory pressure (this machine swaps to compressed RAM first and only then to a
disk file), and model files you downloaded stay in models/.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

import chats
import documents
import images

# Linux has a RAM-backed tmpfs for this. Windows and macOS have none without extra drivers, so there the folder is made in the system temp
# directory (still mode 0700 where that means something, zero-filled and removed when the session ends); `ram_backed()` tells the UI.
BASE = Path("/dev/shm") if sys.platform.startswith("linux") and Path("/dev/shm").is_dir() else Path(tempfile.gettempdir())
PREFIX = f"sthanik-{os.getuid() if hasattr(os, 'getuid') else os.environ.get('USERNAME', 'user')}-"

_dir: Path | None = None
_saved: dict | None = None


def active() -> bool:
    return _dir is not None


def ram_backed() -> bool:
    """True when the private folder lives in memory (Linux tmpfs); False when it is a temporary folder on the disk."""
    return BASE == Path("/dev/shm")


def _wipe(path: Path):
    """Overwrite every file with zeros, then delete the tree."""
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                size = p.stat().st_size
                with p.open("r+b") as f:
                    for off in range(0, size, 1 << 20):
                        f.write(b"\0" * min(1 << 20, size - off))
                    f.flush()
                    os.fsync(f.fileno())
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def _owner_alive(path: Path) -> bool:
    """Is the process that created this private folder (its pid is in the name) still running?"""
    m = re.match(rf"{re.escape(PREFIX)}(\d+)-", path.name)
    if not m:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(m.group(1)))  # os.kill(pid, 0) would terminate the process on Windows
    except ImportError:
        pass
    try:
        os.kill(int(m.group(1)), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sweep():
    """Remove private directories a crashed run of this app left behind. A folder whose owner is still running (another copy of the app) is left alone."""
    if BASE.is_dir():
        for p in BASE.glob(PREFIX + "*"):
            if p.is_dir() and p != _dir and not _owner_alive(p):
                _wipe(p)


def start() -> Path:
    global _dir, _saved
    if _dir:
        return _dir
    root = Path(tempfile.mkdtemp(prefix=f"{PREFIX}{os.getpid()}-{secrets.token_hex(4)}-", dir=BASE))
    root.chmod(0o700)
    for sub in ("chats", "documents", "images", "tmp"):
        (root / sub).mkdir(mode=0o700)
    _saved = {"chats": chats.CHATS_DIR, "docs": documents.DOCS_DIR, "images": images.IMAGES_DIR, "tempdir": tempfile.tempdir}
    chats.CHATS_DIR, documents.DOCS_DIR, images.IMAGES_DIR = root / "chats", root / "documents", root / "images"
    tempfile.tempdir = str(root / "tmp")  # PDF export, picture generation and anything else that uses a temp dir
    _dir = root
    logging.disable(logging.CRITICAL)  # a traceback or a request line could quote a title or a prompt
    return root


def stop():
    global _dir, _saved
    if not _dir:
        return
    root, saved = _dir, _saved
    _dir = _saved = None
    chats.CHATS_DIR, documents.DOCS_DIR, images.IMAGES_DIR = saved["chats"], saved["docs"], saved["images"]
    tempfile.tempdir = saved["tempdir"]
    logging.disable(logging.NOTSET)
    _wipe(root)
