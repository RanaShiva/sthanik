"""The look of the files Sthanik makes (palette, fonts, sizes), read from the `style` block in a skill's front-matter.

Each kind of file has a skill (docx, pdf, html, pptx, xlsx). Its header may hold

    metadata:
      style: {accent: "#14633a", font: "Noto Sans", body_pt: 11, ...}

and the renderers in documents.py use those values. Every key is optional; what is missing, or invalid, falls back to the built-in
look, which is also what the built-in skills spell out. Colours must be #rrggbb, fonts plain names (they end up in CSS), sizes sane.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path

import yaml

DEFAULT = {
    "accent": "#14633a",        # headings' rule, bullets, links, table accent, spreadsheet header
    "accent2": "#3ddc84",       # the bright accent on slides
    "ink": "#15201a",           # body text
    "heading": "#0f3d24",       # heading colour
    "dark": "#07100b",          # title slide, code blocks
    "paper": "#f5f8f6",         # slide background
    "tint": "#e6f0ea",          # table header background
    "font": "Noto Sans",        # body font of PDF / web page / slides PDF
    "office_font": "Calibri",   # Word and PowerPoint
    "body_pt": 11,
    "h1_pt": 25,
    "h2_pt": 15.5,
    "slide_title_pt": 36,
}
COLOURS = ("accent", "accent2", "ink", "heading", "dark", "paper", "tint")
RANGES = {"body_pt": (8, 16), "h1_pt": (16, 40), "h2_pt": (11, 28), "slide_title_pt": (24, 60)}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,38}$")

_current: ContextVar[dict] = ContextVar("style", default=DEFAULT)
SKILLS_DIR: Path | None = None  # set by the server
_cache: dict[str, tuple[float, dict]] = {}


def clean(raw) -> dict:
    """Merge a skill's style block over the defaults, dropping anything invalid."""
    out = dict(DEFAULT)
    if not isinstance(raw, dict):
        return out
    for k in COLOURS:
        v = raw.get(k)
        if isinstance(v, str) and _HEX.match(v.strip()):
            out[k] = v.strip().lower()
    for k in ("font", "office_font"):
        v = raw.get(k)
        if isinstance(v, str) and _FONT.match(v.strip()):
            out[k] = v.strip()
    for k, (lo, hi) in RANGES.items():
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            out[k] = v
    return out


def for_skill(name: str) -> dict:
    """The style of the skill `name` (defaults when the skill is missing, switched off by deletion, or has no style block)."""
    if SKILLS_DIR is None:
        return dict(DEFAULT)
    path = SKILLS_DIR / name / "SKILL.md"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return dict(DEFAULT)
    hit = _cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    style = dict(DEFAULT)
    try:
        m = re.match(r"^---\s*\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)
        meta = (yaml.safe_load(m.group(1)) or {}) if m else {}
        style = clean((meta.get("metadata") or {}).get("style"))
    except (OSError, yaml.YAMLError, AttributeError):
        pass
    _cache[name] = (mtime, style)
    return style


def use(skill: str) -> dict:
    """Make `skill`'s style the one the renderers see for the rest of this request."""
    st = for_skill(skill)
    _current.set(st)
    return st


def current() -> dict:
    return _current.get()


def rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(hex_colour: str, t: float) -> str:
    """The colour lightened towards white by t (0 = unchanged, 1 = white)."""
    r, g, b = rgb(hex_colour)
    return "#%02x%02x%02x" % (round(r + (255 - r) * t), round(g + (255 - g) * t), round(b + (255 - b) * t))


def css(text: str) -> str:
    """Rewrite the built-in stylesheet for the current style. With the default style the text comes back unchanged."""
    st = current()
    if st == DEFAULT:
        return text
    repl = {}
    for key in COLOURS:
        if st[key] != DEFAULT[key]:
            repl[DEFAULT[key]] = st[key]
    if st["heading"] != DEFAULT["heading"]:
        repl["#0b1a11"] = st["heading"]  # the title colour is a darker cousin of the heading colour
    if st["accent"] != DEFAULT["accent"]:  # the pale greens (borders, stripes, quote backgrounds) follow the accent
        for light, t in (("#cfe0d5", .80), ("#d3e0d8", .80), ("#eaf2ed", .92), ("#f1f7f3", .92), ("#f8fbf9", .96)):
            repl[light] = mix(st["accent"], t)
    if st["accent2"] != DEFAULT["accent2"]:
        repl["#a8d8bc"] = mix(st["accent2"], .55)  # the subtitle on the dark title slide
    for old, new in repl.items():
        text = re.sub(re.escape(old), new, text, flags=re.I)
    if st["font"] != DEFAULT["font"]:
        text = text.replace('"Noto Sans"', f'"{st["font"]}"')
    text = _size(text, "font-size: 11pt; line-height: 1.65", f"font-size: {st['body_pt']}pt; line-height: 1.65")
    text = _size(text, "h1 { font-size: 25pt", f"h1 {{ font-size: {st['h1_pt']}pt")
    text = _size(text, "h2 { font-size: 15.5pt", f"h2 {{ font-size: {st['h2_pt']}pt")
    text = _size(text, ".slide h2 { font-size: 36pt", f".slide h2 {{ font-size: {st['slide_title_pt']}pt")
    return text


def _size(text: str, old: str, new: str) -> str:
    return text.replace(old, new, 1)
