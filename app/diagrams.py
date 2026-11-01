"""Draw Mermaid diagrams to PNG with headless Chrome, so they can go into PDFs, Word files and slides.

The diagram source is untrusted (model or web text): it is passed as inert JSON (never as HTML), Mermaid runs in its strict
security mode, and the page carries a CSP that forbids network access. Results are cached by content hash in data/images/.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
from pathlib import Path

import chrome
import images
import paths

MERMAID_JS = paths.STATIC / "vendor" / "mermaid" / "mermaid.min.js"
STYLE_VERSION = "v1"  # bump to invalidate cached renders when the look changes

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src file: 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:">
<style>html,body{margin:0;background:#fff}#out{display:inline-block;padding:24px;font-family:"Noto Sans","DejaVu Sans",sans-serif}#out svg{max-width:none!important}</style></head>
<body><div id="out"></div>
<script type="application/json" id="src">__SRC__</script>
<script src="__JS__"></script>
<script>
(async () => {
  const out = document.getElementById('out');
  try {
    const src = JSON.parse(document.getElementById('src').textContent);
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base', fontFamily: '"Noto Sans","DejaVu Sans",sans-serif',
      themeVariables: { primaryColor: '#e6f0ea', primaryBorderColor: '#14633a', primaryTextColor: '#12201a', lineColor: '#14633a',
        secondaryColor: '#f3f8f5', tertiaryColor: '#ffffff', noteBkgColor: '#fff8dc', noteTextColor: '#12201a', clusterBkg: '#f3f8f5' },
      flowchart: { useMaxWidth: false, htmlLabels: true }, sequence: { useMaxWidth: false }, gantt: { useMaxWidth: false },
      er: { useMaxWidth: false }, class: { useMaxWidth: false }, state: { useMaxWidth: false }, journey: { useMaxWidth: false },
      pie: { useMaxWidth: false }, timeline: { useMaxWidth: false }, mindmap: { useMaxWidth: false } });
    const { svg } = await mermaid.render('d1', src);
    out.innerHTML = svg;
    document.title = 'ok';
  } catch (e) {
    out.textContent = 'ERROR: ' + (e && e.message ? e.message : String(e));
    document.title = 'error';
  }
})();
</script></body></html>"""


_LABEL = re.compile(r'(?<![\w"])([A-Za-z_][\w-]*)\[([^\[\]"\n]*)\]')
_DECISION = re.compile(r'(?<![\w"])([A-Za-z_][\w-]*)\{([^{}"\n]*)\}')
_RISKY = re.compile(r'[()<>&:;,#%]')


def autoquote(code: str) -> str:
    """Flowchart labels with punctuation (A[Step (draft)]) are a syntax error unless quoted; small models forget. Quote them."""
    first = next((ln.strip().lower() for ln in code.splitlines() if ln.strip()), "")
    if not first.startswith(("flowchart", "graph")):
        return code

    def fix(m, o, c):
        label = m.group(2)
        if not label.strip() or label[0] in "(/\\" or not _RISKY.search(label):
            return m.group(0)  # a different node shape, or nothing risky in it
        return f'{m.group(1)}{o}"{label.strip()}"{c}'

    out = []
    for line in code.splitlines():
        if line.lstrip().startswith(("%%", "click ", "style ", "classDef ", "linkStyle ")):
            out.append(line)
            continue
        line = _LABEL.sub(lambda m: fix(m, "[", "]"), line)
        out.append(_DECISION.sub(lambda m: fix(m, "{", "}"), line))
    return "\n".join(out)


def _autocrop(png: Path, pad: int = 28) -> bytes:
    """Trim the blank margin around the drawing, then add an even border back."""
    import io

    from PIL import Image, ImageChops

    im = Image.open(png).convert("RGB")
    box = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    if box:
        im = im.crop((max(box[0] - pad, 0), max(box[1] - pad, 0), min(box[2] + pad, im.width), min(box[3] + pad, im.height)))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def render(code: str) -> tuple[dict | None, str | None]:
    """Render a Mermaid diagram. Returns (image info, None) or (None, short error message)."""
    code = autoquote((code or "").strip())
    if not code:
        return None, "The diagram is empty."
    if len(code) > 20000:
        return None, "The diagram is too large (over 20,000 characters)."
    if not MERMAID_JS.exists():
        return None, "Mermaid is not installed."
    key = hashlib.sha1(f"{STYLE_VERSION}\n{code}".encode()).hexdigest()[:16]
    name = f"diagram-{key}.png"
    cached = images.image_path(name)
    if cached:
        return images.info(cached), None
    src = json.dumps(code).replace("<", "\\u003c")  # inert JSON: nothing in the source can close the <script> tag
    with tempfile.TemporaryDirectory(prefix="sthanik-mmd-") as tmp:
        page = Path(tmp) / "d.html"
        page.write_text(PAGE.replace("__SRC__", src).replace("__JS__", MERMAID_JS.as_uri()), encoding="utf-8")
        try:
            dom, _ = await chrome.run(["--virtual-time-budget=12000", "--dump-dom", page.as_uri()], timeout=45)
        except RuntimeError as e:
            return None, str(e)
        dom = dom.decode("utf-8", "replace")
        if "<title>ok</title>" not in dom:
            m = re.search(r'id="out"[^>]*>ERROR: (.*?)</div>', dom, re.S)
            msg = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))) if m else "Mermaid could not draw this diagram."
            return None, " ".join(msg.split())[:300]
        vb = re.search(r'viewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"', dom)
        w, h = (float(vb.group(1)), float(vb.group(2))) if vb else (900.0, 600.0)
        W, H = int(min(max(w + 48, 200), 3600)), int(min(max(h + 48, 120), 3600))
        shot = Path(tmp) / "d.png"
        try:
            await chrome.run([f"--window-size={W},{H}", "--force-device-scale-factor=2", "--default-background-color=ffffffff",
                              "--virtual-time-budget=12000", f"--screenshot={shot}", page.as_uri()], timeout=45)
        except RuntimeError as e:
            return None, str(e)
        if not shot.exists():
            return None, "The diagram could not be captured."
        data = _autocrop(shot)
    images.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = images.IMAGES_DIR / name
    path.write_bytes(data)
    path.with_name(name + ".json").write_text(json.dumps({"kind": "diagram", "prompt": code.splitlines()[0][:120]}))
    return images.info(path), None
