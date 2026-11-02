"""Mathematics in the files Sthanik makes.

A small model writes formulas in whatever form it half remembers: `$x^2$`, `\\(x^2\\)`, `[ \\frac{a}{b} ]` on a line of its own, `(\\theta)` in
the middle of a sentence, sometimes an empty `[]` where the formula should be. The chat screen renders only the first kinds; a PDF used to
print all of it as source text. Three steps fix that for every file:

normalize()   repairs the delimiters, so the stored text uses `$...$` and `$$...$$` (and the chat and the editor render it too);
render()      typesets each formula with the KaTeX that the chat screen already uses, run once in the headless Chrome that makes the PDF, and
              returns static HTML (no script travels with the file), plus the CSS and the fonts it needs, embedded;
to_text()     turns a formula into readable Unicode (θ ← θ − η∇J(θ)) for the formats that cannot hold typeset formulas (Word, PowerPoint, plain
              text), and for a PDF or HTML file when no browser is installed.

Nothing here executes model text: formulas are passed to KaTeX as inert JSON, with `trust` off.
"""

from __future__ import annotations

import base64
import html as htmllib
import json
import re
import tempfile
from pathlib import Path

import chrome
import paths

KATEX_DIR = paths.STATIC / "vendor" / "katex"
_CODE = re.compile(r"(```[\s\S]*?(?:```|$)|`[^`\n]*`)")  # odd indexes of a split on this are code: left alone
_CMD = re.compile(r"\\[A-Za-z]+")
_PLACE = re.compile(r"@@MATH(\d+)@@")


# ---------------------------------------------------------------- repairing the delimiters
def _balanced(s: str, start: int) -> int:
    """Index just past the parenthesis that closes the one at `start`, or -1."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _is_math(inner: str) -> bool:
    inner = inner.strip()
    return bool(inner) and (bool(_CMD.search(inner)) or (("_" in inner or "^" in inner) and "=" in inner))


def _repair_formula(tex: str) -> str:
    tex = re.sub(r"(?<=[A-Za-z}])!(?=\\(?:big|Big|bigg|Bigg|left|right)\b)", r"\\!", tex)  # "\sigma!\big(" lost the backslash of \!
    return tex.strip()


_LINE = re.compile(r"^(?P<indent>\s*(?:[-*+]\s+|\d+[.)]\s+)?)\[\s*(?P<inner>.+?)\s*\]\s*(?P<tail>[.,;]?)\s*$")
_GROUP = re.compile(r"(?<![\w\\$])\(\s*\\")  # "(\" : a parenthesis that opens on a TeX command
_SINGLE = re.compile(r"(?<=\S[ \t])\(\s*(?P<v>\\[A-Za-z]+|[A-Za-z])(?P<sub>_(?:\{[A-Za-z0-9,+\-]{1,6}\}|[A-Za-z0-9]))?\s*\)(?![\w(])")


def _repair_segment(seg: str) -> str:
    # \[ ... \] and \( ... \) are valid; only the forms a model invents are rewritten
    lines = seg.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s in ("[]", "[ ]"):  # the formula was never written: a bare "[]" must not reach the reader
            i += 1
            continue
        m = _LINE.match(ln)
        if m and _is_math(m["inner"]) and not re.search(r"\]\(", ln):  # not a markdown link
            out.append(f"{m['indent']}$${_repair_formula(m['inner'])}$${m['tail']}")
            i += 1
            continue
        if s == "[":  # "[" on a line of its own, the formula below, "]" on a line of its own
            j = i + 1
            body: list[str] = []
            while j < len(lines) and lines[j].strip() != "]" and len(body) < 8:
                body.append(lines[j])
                j += 1
            if j < len(lines) and lines[j].strip() == "]" and _is_math("\n".join(body)):
                out.append(f"$${_repair_formula(' '.join(b.strip() for b in body))}$$")
                i = j + 1
                continue
        out.append(ln)
        i += 1
    seg = "\n".join(out)

    def groups(text: str) -> str:
        res, pos = [], 0
        for m in _GROUP.finditer(text):
            if m.start() < pos:
                continue
            end = _balanced(text, m.start())
            if end < 0:
                continue
            inner = text[m.start() + 1:end - 1]
            if _CMD.search(inner):
                res += [text[pos:m.start()], f"${_repair_formula(inner)}$"]
                pos = end
        return "".join(res) + text[pos:]

    seg = groups(seg)
    # "(x)" and "(n)" are variables when the line holds other maths; on their own they may be "(a)", "(b)" in a list run into a sentence
    return "\n".join(_SINGLE.sub(lambda m: f"${m['v']}{m['sub'] or ''}$", ln) if ("$" in ln or _CMD.search(ln)) else ln for ln in seg.split("\n"))


def normalize(text: str) -> str:
    """The text with invented math delimiters rewritten as $...$ and $$...$$. Code, and formulas that are already delimited, are left alone."""
    if not text or not re.search(r"[\\^_\[]|\(\s*[A-Za-z]\s*\)", text):
        return text
    parts = _CODE.split(text)
    for i in range(0, len(parts), 2):
        masked, raws = _mask(parts[i])
        repaired = _repair_segment(masked)
        parts[i] = re.sub(r"@@KEEP(\d+)@@", lambda m: raws[int(m[1])], repaired)
    return "".join(parts)


# ---------------------------------------------------------------- finding the formulas
_MATH_FORMS = (
    (re.compile(r"\$\$([\s\S]+?)\$\$"), True), (re.compile(r"\\\[([\s\S]+?)\\\]"), True), (re.compile(r"\\\(([\s\S]+?)\\\)"), False),
    (re.compile(r"(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])(?!\d)"), False),
)


def _mask(seg: str) -> tuple[str, list[str]]:
    """The segment with every delimited formula replaced by @@KEEPn@@, and the originals."""
    raws: list[str] = []

    def keep(m):
        raws.append(m.group(0))
        return f"@@KEEP{len(raws) - 1}@@"

    for rx, _ in _MATH_FORMS:
        seg = rx.sub(keep, seg)
    return seg, raws


def extract(text: str) -> tuple[str, list[dict]]:
    """(text with each formula replaced by @@MATHn@@, [{"tex", "display"}]) over $$..$$, \\[..\\], \\(..\\) and $..$."""
    store: list[dict] = []

    def keep(tex: str, display: bool) -> str:
        if not tex.strip():
            return ""  # the model left the formula out: nothing to typeset
        store.append({"tex": tex.strip(), "display": display})
        return f"@@MATH{len(store) - 1}@@"

    parts = _CODE.split(text or "")
    for i in range(0, len(parts), 2):
        s = parts[i]
        s = re.sub(r"\$\$([\s\S]+?)\$\$", lambda m: keep(m[1], True), s)
        s = re.sub(r"\\\[([\s\S]+?)\\\]", lambda m: keep(m[1], True), s)
        s = re.sub(r"\\\(([\s\S]+?)\\\)", lambda m: keep(m[1], False), s)
        s = re.sub(r"(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])(?!\d)", lambda m: keep(m[1], False), s)
        parts[i] = s
    return "".join(parts), store


def has_math(text: str) -> bool:
    return bool(extract(text)[1])


# ---------------------------------------------------------------- typesetting with KaTeX in the browser that makes the PDF
_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'"></head>
<body><pre id="o"></pre><script type="application/json" id="src">__SRC__</script><script>__JS__</script><script>
const items = JSON.parse(document.getElementById('src').textContent);
const out = items.map((it) => { try { return katex.renderToString(it.tex, { displayMode: it.display, throwOnError: true, output: 'html', trust: false, strict: 'ignore' }); } catch (e) { return null; } });
document.getElementById('o').textContent = JSON.stringify(out);
</script></body></html>"""
_js_cache: list[str] = []
_cache: dict[tuple[str, bool], str | None] = {}


def _katex_js() -> str:
    if not _js_cache:
        src = (KATEX_DIR / "katex.js").read_text(encoding="utf-8")
        # the file is an ES module (the chat screen imports it); a plain page needs the same object on window
        _js_cache.append(re.sub(r"\nexport \{[^}]*\};?\s*$", "\nwindow.katex = katex;\n", src))
    return _js_cache[0]


async def render(items: list[dict]) -> list[str | None]:
    """KaTeX HTML for each formula, or None where KaTeX could not read it. Needs Chrome; returns all None without it."""
    todo = [k for k in dict.fromkeys((it["tex"], bool(it["display"])) for it in items) if k not in _cache]
    if todo:
        data = json.dumps([{"tex": t, "display": d} for t, d in todo]).replace("<", "\\u003c")
        page = _PAGE.replace("__SRC__", data).replace("__JS__", _katex_js().replace("</script", "<\\/script"))
        try:
            with tempfile.TemporaryDirectory(prefix="sthanik-math-") as tmp:
                f = Path(tmp) / "m.html"
                f.write_text(page, encoding="utf-8")
                out, _ = await chrome.run(["--dump-dom", "--virtual-time-budget=4000", f.as_uri()], timeout=45)
            m = re.search(r'<pre id="o">(.*?)</pre>', out.decode("utf-8", "replace"), re.S)
            got = json.loads(htmllib.unescape(m.group(1))) if m else []
        except (RuntimeError, ValueError):
            got = []
        for k, v in zip(todo, got if len(got) == len(todo) else [None] * len(todo)):
            _cache[k] = v
        if len(_cache) > 2000:
            _cache.clear()
    return [_cache.get((it["tex"], bool(it["display"]))) for it in items]


_FONT = re.compile(r'@font-face\{[^}]*?font-family:"?(?P<fam>KaTeX_[A-Za-z0-9]+)"?[^}]*?font-style:(?P<style>\w+);font-weight:(?P<weight>\d+);src:url\((?P<url>fonts/[^)]+?\.woff2)\)[^}]*\}')
_NEEDS = {  # fonts that are only fetched when the formulas use them
    "KaTeX_AMS": r"amsrm|\bmathbb\b", "KaTeX_Caligraphic": r"mathcal|mathscr", "KaTeX_Fraktur": r"mathfrak", "KaTeX_SansSerif": r"mathsf|textsf",
    "KaTeX_Script": r"mathscr", "KaTeX_Typewriter": r"mathtt|texttt", "KaTeX_Size1": r"size1|\bdelimsizing\b", "KaTeX_Size2": r"size2", "KaTeX_Size3": r"size3", "KaTeX_Size4": r"size4",
}
_css_cache: dict[str, str] = {}


def css(used_html: str) -> str:
    """KaTeX's stylesheet with the fonts these formulas use embedded as data URIs, so the file needs nothing else."""
    raw = _css_cache.get("raw") or (KATEX_DIR / "katex.css").read_text(encoding="utf-8")
    _css_cache["raw"] = raw

    def font(m):
        need = _NEEDS.get(m["fam"])
        if need and not re.search(need, used_html):
            return ""
        p = KATEX_DIR / m["url"]
        if not p.is_file():
            return ""
        uri = "data:font/woff2;base64," + base64.b64encode(p.read_bytes()).decode()
        return f"@font-face{{font-family:{m['fam']};font-style:{m['style']};font-weight:{m['weight']};src:url({uri}) format(\"woff2\")}}"

    return _FONT.sub(font, raw)


def typeset(html_text: str, items: list[dict], rendered: list[str | None]) -> str:
    """Put each formula's HTML where its @@MATHn@@ marker is. A formula KaTeX could not read (or no browser) shows as readable Unicode text."""
    def put(m):
        i = int(m.group(1))
        if i >= len(items):
            return m.group(0)
        r = rendered[i] if i < len(rendered) else None
        if r:
            return r
        text = htmllib.escape(to_text(items[i]["tex"]))
        return f'<div class="mathfb">{text}</div>' if items[i]["display"] else f'<span class="mathfb">{text}</span>'

    html_text = re.sub(r"<p>\s*(@@MATH\d+@@)\s*</p>", r"\1", html_text)  # a display formula alone in its paragraph is not inside a <p>
    return _PLACE.sub(put, html_text)


# ---------------------------------------------------------------- readable text for the formats that cannot hold typeset math
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_SYMBOLS = {
    "sum": "∑", "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇", "infty": "∞", "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "leq": "≤", "le": "≤",
    "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "sim": "∼", "simeq": "≃", "propto": "∝", "equiv": "≡", "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆",
    "cup": "∪", "cap": "∩", "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←", "leftrightarrow": "↔", "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔",
    "mapsto": "↦", "mid": "|", "vert": "|", "Vert": "‖", "|": "‖", "ldots": "…", "dots": "…", "cdots": "⋯", "forall": "∀", "exists": "∃", "emptyset": "∅", "angle": "∠",
    "langle": "⟨", "rangle": "⟩", "ll": "≪", "gg": "≫", "circ": "∘", "bullet": "•", "star": "⋆", "oplus": "⊕", "otimes": "⊗", "top": "ᵀ", "dagger": "†", "prime": "′",
    "neg": "¬", "land": "∧", "lor": "∨", "wedge": "∧", "vee": "∨", "perp": "⊥", "parallel": "∥", "therefore": "∴", "degree": "°", "quad": "  ", "qquad": "    ", ",": " ", ";": " ",
    ":": " ", "!": "", " ": " ", "{": "{", "}": "}", "%": "%", "&": "&", "_": "_", "#": "#", "$": "$", "ell": "ℓ", "hbar": "ℏ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ",
}
_FUNCS = {"log", "ln", "exp", "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "max", "min", "sup", "inf", "lim", "arg", "det", "dim",
          "gcd", "deg", "ker", "Pr", "mod", "argmax", "argmin", "softmax", "sign"}
_DROP = {"left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr", "displaystyle", "textstyle", "limits", "nolimits", "mathstrut", "relax"}
_WRAP = {"text", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal", "mathscr", "mathfrak", "boldsymbol", "bm", "operatorname", "textbf", "textit", "mbox", "mathnormal", "textrm"}
_BB = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "E": "𝔼"}
_ACCENT = {"hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0304", "tilde": "\u0303", "widetilde": "\u0303", "vec": "\u20d7", "dot": "\u0307", "ddot": "\u0308", "check": "\u030c"}
_SUP = dict(zip("0123456789+-=()nijxyTtk", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱʲˣʸᵀᵗᵏ"))
_SUB = dict(zip("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"))


class _Reader:
    def __init__(self, s: str):
        self.s, self.i = s, 0

    def more(self) -> bool:
        return self.i < len(self.s)

    def group(self) -> str:
        """The next argument: a {...} group, a \\command, or one character."""
        while self.more() and self.s[self.i] == " ":
            self.i += 1
        if not self.more():
            return ""
        if self.s[self.i] == "{":
            depth, j = 0, self.i
            while j < len(self.s):
                depth += self.s[j] == "{"
                depth -= self.s[j] == "}"
                j += 1
                if depth == 0:
                    break
            out = self.s[self.i + 1:j - 1]
            self.i = j
            return out
        if self.s[self.i] == "\\":
            m = _CMD.match(self.s, self.i)
            j = m.end() if m else self.i + 2
            out = self.s[self.i:j]
            self.i = j
            return out
        out = self.s[self.i]
        self.i += 1
        return out


def _script(tex: str, table: dict, mark: str) -> str:
    inner = to_text(tex).replace(" ", "") if len(tex) < 12 else to_text(tex)
    if inner and all(c in table for c in inner):
        return "".join(table[c] for c in inner)
    if len(inner) == 1 or re.fullmatch(r"\w+", inner) or (inner.startswith("(") and inner.endswith(")") and _balanced(inner, 0) == len(inner)):
        return f"{mark}{inner}"
    return f"{mark}({inner})"


def to_text(tex: str) -> str:
    """A formula as readable Unicode text: θ ← θ − η(∇_θ J(θ) + (λ/n)θ)."""
    r = _Reader(tex.replace("\n", " "))
    out: list[str] = []
    while r.more():
        c = r.s[r.i]
        if c == "\\":
            m = _CMD.match(r.s, r.i)
            if not m:  # \, \; \! \| \{ and the like
                name = r.s[r.i + 1:r.i + 2]
                r.i += 2
                out.append(_SYMBOLS.get(name, name))
                continue
            name = m.group()[1:]
            r.i = m.end()
            if name in _GREEK:
                out.append(_GREEK[name])
            elif name in _FUNCS:
                out.append(name + (" " if r.more() and r.s[r.i] not in " _^({\\" else ""))
            elif name in _DROP:
                pass
            elif name in _SYMBOLS:
                out.append(_SYMBOLS[name])
            elif name in _WRAP:
                out.append(to_text(r.group()))
            elif name == "mathbb":
                arg = r.group().strip()
                out.append(_BB.get(arg, arg))
            elif name in ("frac", "dfrac", "tfrac", "cfrac"):
                a, b = to_text(r.group()), to_text(r.group())
                wrap = lambda x: x if re.fullmatch(r"[\w.′’]+|\([^()]*\)", x) else f"({x})"  # noqa: E731
                out.append(f"{wrap(a)}/{wrap(b)}")
            elif name == "binom":
                out.append(f"C({to_text(r.group())}, {to_text(r.group())})")
            elif name == "sqrt":
                out.append("√" + (lambda x: x if len(x) == 1 else f"({x})")(to_text(r.group())))
            elif name in _ACCENT:
                base = to_text(r.group())
                out.append(base[:1] + _ACCENT[name] + base[1:] if base else "")
            elif name in ("begin", "end"):
                r.group()
            elif name in ("label", "tag", "nonumber", "notag"):
                if name in ("label", "tag"):
                    r.group()
            elif name in ("underbrace", "overbrace", "underline", "boxed", "cancel", "substack", "stackrel", "overset", "underset"):
                out.append(to_text(r.group()))
            else:
                out.append(name)  # an unknown command reads as its own name
        elif c in "^_":
            r.i += 1
            arg = r.group()
            out.append(_script(arg, _SUP, "^") if c == "^" else _script(arg, _SUB, "_"))
        elif c == "{" or c == "}":
            r.i += 1
        elif c == "~":
            r.i += 1
            out.append(" ")
        elif c == "&" or c == "$":
            r.i += 1
        else:
            r.i += 1
            out.append("−" if c == "-" else c)
    text = "".join(out)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def to_plain(text: str) -> str:
    """The text with every formula written as readable Unicode (for Word, PowerPoint, plain text, and a PDF with no browser)."""
    if not text:
        return text
    marked, items = extract(text)
    if not items:
        return text
    return _PLACE.sub(lambda m: (f"\n\n    {to_text(items[int(m[1])]['tex'])}\n\n" if items[int(m[1])]["display"] else to_text(items[int(m[1])]["tex"])), marked)
