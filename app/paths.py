"""Where Sthanik keeps things.

APP   the program's own read-only files (the UI, the built-in skills). In a packaged build this is the unpacked bundle.
HOME  everything that belongs to the user and grows: data/ (chats, documents, pictures), models/, bin/ (llama.cpp, stable-diffusion.cpp),
      config/ and skills/. Paths such as "models/text/x.gguf" in the settings are relative to it.

HOME is, in this order: $STHANIK_HOME; the folder of the program when running from a source checkout (so `./run.sh` keeps using the
repository's own data/ and models/); a folder named in a `portable.txt` beside a packaged program ("." = next to it); otherwise the
per-user application folder of the operating system.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
SOURCE = Path(__file__).resolve().parent.parent
APP = Path(getattr(sys, "_MEIPASS", SOURCE)) if FROZEN else SOURCE
STATIC = APP / "app" / "static"
BUILTIN_SKILLS = APP / "app" / "builtin_skills"


def _user_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Sthanik"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Sthanik"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "sthanik"


def _home() -> Path:
    if os.environ.get("STHANIK_HOME"):
        return Path(os.environ["STHANIK_HOME"]).expanduser().resolve()
    if not FROZEN:
        return SOURCE
    marker = Path(sys.executable).resolve().parent / "portable.txt"
    if marker.is_file():
        sub = marker.read_text().strip() or "."
        return (marker.parent / sub).resolve()
    return _user_dir()


HOME = _home()

# Extra arguments for subprocess calls: on Windows a packaged (windowless) app would otherwise flash a console for every helper it starts.
QUIET: dict = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
