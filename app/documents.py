"""Turn Markdown into files: PDF, DOCX, HTML, slide decks (PPTX / PDF), XLSX.

Everything lands in data/documents/. Model-written content is untrusted, so the HTML that Chrome renders for
PDFs is built with raw HTML and images disabled and a CSP that forbids scripts and any network access.
"""

import asyncio
import base64
import contextvars
import csv
import html as htmllib
import io
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import paths
import style as look  # the file's palette, fonts and sizes (see style.py)

from markdown_it import MarkdownIt

import chrome
import diagrams
import doclint
import images
import mathtext

ROOT = paths.HOME  # user data, models and binaries; see paths.py
DOCS_DIR = Path(os.environ.get("LFM3_DOCS_DIR") or ROOT / "data" / "documents")

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
    "md": "text/markdown",
    "txt": "text/plain",
}
DOC_FORMATS = ("pdf", "docx", "html", "md", "txt")
SLIDE_FORMATS = ("pptx", "pdf")

# Markdown flavour for rendering model text: tables + strikethrough, no raw HTML. Images are only ever our own stored
# pictures (`img:NAME`), embedded as data URIs; any other image URL is reduced to its alt text, never fetched.
_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])
IMG_MD = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)\)")
# Any fenced block counts as a diagram if it is labelled mermaid OR its first line is a diagram keyword: models often forget the label.
MERMAID_FENCE = re.compile(r"^[ \t]*```([\w-]*)[^\n]*\n(.*?)\n[ \t]*```[ \t]*$", re.S | re.M)
DIAGRAM_START = re.compile(r"^\s*(?:(?:flowchart|graph)\s+(?:TD|TB|BT|RL|LR)\b|sequenceDiagram\b|gantt\b|classDiagram\b|stateDiagram(?:-v2)?\b|erDiagram\b|journey\b|pie\b|mindmap\b|timeline\b|gitGraph\b)", re.I)


def _diagram_blocks(text: str) -> list:
    return [m for m in MERMAID_FENCE.finditer(text) if m.group(1).lower() == "mermaid" or (m.group(1).lower() in ("", "text", "plaintext", "flowchart", "graph", "diagram") and DIAGRAM_START.match(m.group(2)))]


def _img_ref(src: str) -> Path | None:
    return images.resolve(src) if isinstance(src, str) and src.startswith("img:") else None


IMG_LOOSE = re.compile(r"!\[([^\]]*)\]\((img:[^)]*)\)")  # names may contain spaces when a model mangled them


def _render_image(self, tokens, idx, options, env):
    t = tokens[idx]
    path = _img_ref(t.attrGet("src") or "")
    alt = t.content or ""
    if path is None:
        return htmllib.escape(alt)
    mime = images.MIME.get(path.suffix.lstrip("."), "image/png")
    uri = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
    cap = f'<span class="cap">{htmllib.escape(alt)}</span>' if alt else ""
    return f'<span class="fig"><img src="{uri}" alt="{htmllib.escape(alt, quote=True)}">{cap}</span>'


_md.add_render_rule("image", _render_image)


class DocError(Exception):
    """A problem the model/user should see (bad format, no Chrome...)."""


# ------------------------------------------------------------------ helpers


_DIAGRAM_LINE = re.compile(r"-->|---|==>|-\.->|^\s*(subgraph|end|style|class|classDef|click|participant|actor|activate|deactivate|note|title|section|dateFormat|axisFormat|state|direction|%%|[A-Za-z_]\w*\s*[\[({]|[A-Za-z_]\w*\s*:\s)", re.I)


def close_unclosed_fence(text: str) -> tuple[str, bool]:
    """A reply cut off inside a code block (very often a diagram) leaves a fence that never closes, and everything after it is then
    shown as code. Close it where the block plainly ends: before the first heading, or at the first blank line followed by something that
    is not diagram/code-like (for diagrams); at the end of the text otherwise. Returns (text, changed)."""
    lines = text.split("\n")
    open_at = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            open_at = None if open_at is not None else i
    if open_at is None:
        return text, False
    is_diagram = bool(re.match(r"^\s*```\s*mermaid", lines[open_at], re.I)) or bool(DIAGRAM_START.match(lines[open_at + 1] if open_at + 1 < len(lines) else ""))
    close = len(lines)
    for j in range(open_at + 1, len(lines)):
        ln = lines[j]
        if re.match(r"^#{1,6}\s", ln):
            close = j
            break
        if is_diagram and not ln.strip():
            nxt = next((x for x in lines[j + 1:] if x.strip()), "")
            if nxt and not _DIAGRAM_LINE.search(nxt):
                close = j
                break
    lines.insert(close, "```")
    if close + 1 < len(lines) and lines[close + 1].strip():
        lines.insert(close + 1, "")
    return "\n".join(lines), True


def _clean_markdown(text: str, unknown: list | None = None) -> str:
    text = (text or "").strip()
    fence = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", text, re.S)
    if fence:
        text = fence.group(1)
    def keep(m):
        p = _img_ref(m.group(2))
        if p is not None:
            return f"![{m.group(1)}](img:{p.name})"  # canonical name, so mangled references are repaired for good
        if m.group(2).startswith("img:") and unknown is not None:
            unknown.append(m.group(2)[:80])
        return m.group(1)

    text = IMG_LOOSE.sub(keep, text)
    text = IMG_MD.sub(lambda m: m.group(0) if _img_ref(m.group(2)) else m.group(1), text)  # other images keep only their alt text
    if "\n" not in text and "\\n" in text:  # model double-escaped newlines inside the tool call
        text = text.replace("\\n", "\n")
    text, _ = close_unclosed_fence(text)
    return mathtext.normalize(text)  # invented math delimiters become $...$, so every format and the chat screen can show the formula


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    return (s or "document")[:48]


def _target(title: str, ext: str) -> Path:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    p = DOCS_DIR / f"{_slug(title)}-{stamp}.{ext}"
    n = 2
    while p.exists():
        p = DOCS_DIR / f"{_slug(title)}-{stamp}-{n}.{ext}"
        n += 1
    return p


def _check_pictures(unknown: list, strict: bool):
    if unknown and strict:
        have = ", ".join(i["name"] for i in images.list_images() if i["kind"] == "image")[:600] or "(none yet)"
        raise DocError(f"These picture references do not exist: {'; '.join(unknown[:4])}. Use an exact name from list_images (or make the picture first). Existing pictures: {have}")


def _info(p: Path) -> dict:
    st = p.stat()
    return {"name": p.name, "format": p.suffix.lstrip("."), "size": st.st_size, "created": st.st_mtime, "url": f"/api/documents/{p.name}",
            "editable": _is_editable(p)}


find_chrome = chrome.find_chrome


async def _html_to_pdf(page_html: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="sthanik-pdf-") as tmp:
        src, out = Path(tmp) / "in.html", Path(tmp) / "out.pdf"
        src.write_text(page_html, encoding="utf-8")
        try:
            _, err = await chrome.run(["--no-pdf-header-footer", f"--print-to-pdf={out}", src.as_uri()], timeout=90)
        except RuntimeError as e:
            raise DocError(f"PDF export failed: {e} Try DOCX or HTML instead." if "installed" in str(e) else f"PDF export failed: {e}")
        if not out.exists():
            raise DocError("PDF rendering failed: " + err.decode(errors="replace")[-200:])
        return out.read_bytes()


async def _prepare(text: str, strict: bool = True, warnings: list | None = None) -> str:
    """Draw ```mermaid blocks to pictures so every output format can show them (each becomes an `img:` reference).

    strict: a diagram that cannot be drawn is an error (the model gets the message and fixes it). Not strict (saves from the
    editor, plan exports): the block is left as a code listing and a warning is recorded, so one typo never loses a document."""
    blocks = _diagram_blocks(text)
    if not blocks:
        return text
    limit = 12 if strict else 40  # a long plan-made deck may legitimately hold more than a model's single tool call
    if len(blocks) > limit:
        raise DocError(f"Too many diagrams in one document (limit {limit}).")
    out, last = [], 0
    for m in blocks:
        info, err = await diagrams.render(m.group(2))
        if err and strict:
            raise DocError(f"A mermaid diagram could not be drawn: {err} Fix the diagram syntax and try again.")
        if err:
            if warnings is not None:
                warnings.append(f"A diagram could not be drawn and was kept as text: {err[:140]}")
            out += [text[last:m.start()], "```text\n" + m.group(2).strip() + "\n```"]
        else:
            out += [text[last:m.start()], f"![]({'img:' + info['name']})"]
        last = m.end()
    out.append(text[last:])
    return "".join(out)


CSP = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src data:">'

DOC_CSS = """
@page { size: A4; margin: 22mm 20mm 24mm; @bottom-center { content: counter(page) " / " counter(pages); font: 9pt "Noto Sans", "DejaVu Sans", sans-serif; color: #7b8d83; } }
* { box-sizing: border-box; }
body { font-family: "Noto Sans", "DejaVu Sans", Inter, Arial, sans-serif; color: #15201a; font-size: 11pt; line-height: 1.65; margin: 0 auto; max-width: 820px; padding: 24px; }
h1 { font-size: 25pt; line-height: 1.2; margin: 0 0 .55em; letter-spacing: -.01em; padding-top: 16px; border-top: 6px solid #14633a; color: #0b1a11; }
h2 { font-size: 15.5pt; margin: 1.7em 0 .45em; padding-bottom: .22em; border-bottom: 1px solid #cfe0d5; color: #0f3d24; }
h3 { font-size: 12.5pt; margin: 1.35em 0 .3em; color: #14633a; } h4 { font-size: 11.5pt; margin: 1.1em 0 .2em; }
p { margin: 0 0 .85em; } ul, ol { margin: 0 0 .95em; padding-left: 1.5em; } li { margin: .22em 0; } li::marker { color: #14633a; }
code { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: .9em; background: #eaf2ed; padding: .1em .35em; border-radius: 4px; }
pre { background: #07100b; color: #dbe8df; padding: 12px 14px; border-radius: 8px; overflow: auto; white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; }
pre code { background: none; padding: 0; color: inherit; }
blockquote { margin: .9em 0; padding: .35em 0 .35em 16px; border-left: 4px solid #14633a; color: #3c4f44; background: #f1f7f3; border-radius: 0 6px 6px 0; }
table { border-collapse: collapse; margin: .8em 0 1.1em; width: 100%; font-size: 10pt; page-break-inside: avoid; }
th, td { border: 1px solid #d3e0d8; padding: 6px 10px; text-align: left; vertical-align: top; } th { background: #e6f0ea; color: #0f3d24; }
tr:nth-child(even) td { background: #f8fbf9; }
hr { border: 0; border-top: 1px solid #d3e0d8; margin: 1.4em 0; } a { color: #14633a; }
.meta { color: #6b7490; font-size: 9.5pt; margin-bottom: 1.6em; }
.fig { display: block; text-align: center; margin: 1em 0 1.2em; page-break-inside: avoid; } .fig img { max-width: 100%; max-height: 8.5in; border-radius: 6px; }
.fig .cap { display: block; margin-top: .4em; font-size: 9.5pt; color: #5b6b62; font-style: italic; }
"""

# Typeset formulas: a display formula gets room and never overflows the page; one KaTeX cannot read shows as readable text
MATH_CSS = """
.katex { font-size: 1.04em; } .katex-display { margin: .9em 0 1em; overflow-x: auto; overflow-y: hidden; page-break-inside: avoid; }
.katex-display > .katex { white-space: normal; } .mathfb { font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-style: italic; } div.mathfb { margin: .8em 0 1em 1.5em; }
"""


async def _typeset(text: str) -> list:
    """KaTeX HTML for each formula in the text (empty without any, or without a browser: each then falls back to readable Unicode)."""
    items = mathtext.extract(text)[1]
    return await mathtext.render(items) if items else []


def _doc_html(title: str, text: str, rendered: list | None = None) -> str:
    marked, items = mathtext.extract(text)
    body = mathtext.typeset(_md.render(marked), items, rendered or [])
    has_h1 = re.search(r"^#\s", text, re.M) is not None
    head = "" if has_h1 else f"<h1>{htmllib.escape(title)}</h1>"
    math_css = mathtext.css(body) + MATH_CSS if items else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">{CSP}<title>{htmllib.escape(title)}</title>'
            f"<style>{look.css(DOC_CSS)}{math_css}</style></head><body>{head}{body}</body></html>")


# ------------------------------------------------------------------ DOCX


def _img_inches(path: Path) -> float:
    """Natural size at 96 dpi; the 2x-resolution renders we make are halved so text in diagrams stays readable."""
    from PIL import Image
    with Image.open(path) as im:
        return im.width / 192 if path.name.startswith("diagram-") else im.width / 96


def _docx(title: str, text: str) -> bytes:
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    doc.core_properties.title = title
    normal = doc.styles["Normal"]
    st = look.current()
    normal.font.name, normal.font.size = st["office_font"], Pt(st["body_pt"])
    for hn in ("Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        doc.styles[hn].font.color.rgb = RGBColor(*look.rgb(st["accent"]))
        doc.styles[hn].font.name = st["office_font"]
    if not re.search(r"^#\s", text, re.M):
        doc.add_heading(title, 0)

    def inline(par, token):
        bold = ital = strike = False
        href = None
        for c in token.children or []:
            t = c.type
            if t == "strong_open": bold = True
            elif t == "strong_close": bold = False
            elif t == "em_open": ital = True
            elif t == "em_close": ital = False
            elif t == "s_open": strike = True
            elif t == "s_close": strike = False
            elif t == "link_open": href = c.attrGet("href")
            elif t == "link_close":
                if href:
                    r = par.add_run(f" ({href})")
                    r.font.color.rgb = RGBColor(0x55, 0x60, 0x80)
                href = None
            elif t == "image":
                path = _img_ref(c.attrGet("src") or "")
                if path is not None:
                    par.alignment = 1
                    par.add_run().add_picture(str(path), width=Inches(min(6.0, _img_inches(path))))
                    if c.content:
                        cap = doc.add_paragraph()
                        cap.alignment = 1
                        r = cap.add_run(c.content)
                        r.italic, r.font.size, r.font.color.rgb = True, Pt(9.5), RGBColor(0x5B, 0x6B, 0x62)
            elif t in ("softbreak",): par.add_run(" ")
            elif t == "hardbreak": par.add_run().add_break(WD_BREAK.LINE)
            elif t in ("text", "code_inline"):
                r = par.add_run(c.content)
                r.bold, r.italic, r.font.strike = bold, ital, strike
                if t == "code_inline":
                    r.font.name = "Consolas"
                    r.font.size = Pt(10)
                if href:
                    r.font.color.rgb = RGBColor(0x3B, 0x4F, 0xE0)
                    r.underline = True

    toks = _md.parse(text)
    lists: list[str] = []
    quote = 0
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.type == "heading_open":
            doc_par = doc.add_heading("", min(int(t.tag[1]), 4))
            inline(doc_par, toks[i + 1])
            i += 3
            continue
        if t.type in ("bullet_list_open", "ordered_list_open"):
            lists.append("bullet" if t.type == "bullet_list_open" else "number")
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif t.type == "blockquote_open": quote += 1
        elif t.type == "blockquote_close": quote -= 1
        elif t.type == "paragraph_open":
            if lists:
                depth = min(len(lists), 3)
                base = "List Bullet" if lists[-1] == "bullet" else "List Number"
                par = doc.add_paragraph(style=base if depth == 1 else f"{base} {depth}")
            else:
                par = doc.add_paragraph()
                if quote:
                    par.paragraph_format.left_indent = Pt(18)
            inline(par, toks[i + 1])
            if quote:
                for r in par.runs: r.italic = True
            i += 3
            continue
        elif t.type in ("fence", "code_block"):
            par = doc.add_paragraph()
            par.paragraph_format.left_indent = Pt(10)
            run = par.add_run(t.content.rstrip("\n"))
            run.font.name, run.font.size = "Consolas", Pt(9.5)
        elif t.type == "hr":
            doc.add_paragraph("―" * 30)
        elif t.type == "table_open":
            rows, cur, cell_is_head = [], None, False
            j = i + 1
            while toks[j].type != "table_close":
                tt = toks[j]
                if tt.type == "tr_open": cur = []
                elif tt.type == "tr_close": rows.append(cur)
                elif tt.type in ("th_open", "td_open"): cell_is_head = tt.type == "th_open"
                elif tt.type == "inline": cur.append((toks[j], cell_is_head))
                j += 1
            if rows:
                table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                table.style = "Light Grid Accent 3"
                for ri, row in enumerate(rows):
                    for ci, (tok, head) in enumerate(row):
                        cell = table.cell(ri, ci)
                        inline(cell.paragraphs[0], tok)
                        if head:
                            for r in cell.paragraphs[0].runs: r.bold = True
                doc.add_paragraph()
            i = j + 1
            continue
        i += 1
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ slides


def parse_slides(title: str, text: str) -> tuple[str, str, list[dict]]:
    """Markdown → (deck title, subtitle, slides). `## Heading` starts a slide; `# Heading` names the deck."""
    deck, sub = title, ""
    slides: list[dict] = []
    cur = None
    in_code, code = False, []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                if cur is not None: cur["code"] = "\n".join(code)
                in_code, code = False, []
            else:
                in_code = True
            continue
        if in_code:
            code.append(raw)
            continue
        if m := re.match(r"^#\s+(.*)", line):
            deck = m.group(1).strip()
            cur = None
        elif m := re.match(r"^#{2,3}\s+(.*)", line):
            cur = {"title": m.group(1).strip(), "items": [], "table": [], "notes": [], "code": "", "image": None}
            slides.append(cur)
        elif line.strip() in ("---", "***", "___"):
            cur = {"title": "", "items": [], "table": [], "notes": [], "code": "", "image": None}
            slides.append(cur)
        elif not line.strip():
            continue
        elif cur is None:
            sub = (sub + " " + line.strip()).strip()
        elif (m := IMG_MD.fullmatch(line.strip())) and _img_ref(m.group(2)):
            cur["image"] = {"path": _img_ref(m.group(2)), "caption": m.group(1).strip()}
        elif line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                cur["table"].append(cells)
        elif re.match(r"^(notes?|speaker notes?)\s*:", line.strip(), re.I):
            cur["notes"].append(line.split(":", 1)[1].strip())
        elif m := re.match(r"^(\s*)([-*+•]|\d+[.)])\s+(.*)", line):
            level = min(len(m.group(1).replace("\t", "    ")) // 2, 2)
            cur["items"].append({"text": re.sub(r"[*_`]+", "", m.group(3)).strip(), "level": level})
        else:
            cur["items"].append({"text": re.sub(r"[*_`]+", "", line.strip()), "level": 0})
    return deck, sub, [s for s in slides if s["title"] or s["items"] or s["table"] or s["code"] or s["image"]]


def _pptx(title: str, text: str) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR
    from pptx.util import Emu, Inches, Pt

    deck, sub, slides = parse_slides(title, text)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W, H = prs.slide_width, prs.slide_height
    LK = look.current()  # the deck's palette and fonts (st below is the subtitle shape)
    # NAVY is the dark title slide, INK the body text (a touch darker than the web page's), TEAL the bright accent
    NAVY, INK, ACCENT, TEAL, MUTED, PAPER = (RGBColor(*look.rgb(LK["dark"])), RGBColor(0x12, 0x20, 0x1A) if LK["ink"] == look.DEFAULT["ink"] else RGBColor(*look.rgb(LK["ink"])),
                                              RGBColor(*look.rgb(LK["accent"])), RGBColor(*look.rgb(LK["accent2"])), RGBColor(0x5B, 0x6B, 0x62), RGBColor(*look.rgb(LK["paper"])))

    def bg(slide, color):
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = color

    def bar(slide, x, y, w, h, color):
        s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
        s.fill.solid(); s.fill.fore_color.rgb = color; s.line.fill.background()
        return s

    def style(par, size, color, bold=False):
        for r in par.runs:
            r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(size), bold, color, LK["office_font"]

    # title slide
    s = prs.slides.add_slide(prs.slide_layouts[0])
    bg(s, NAVY)
    bar(s, Inches(0.9), Inches(2.55), Inches(1.4), Inches(0.09), TEAL)
    t, st = s.shapes.title, s.placeholders[1]
    t.left, t.top, t.width, t.height = Inches(0.9), Inches(2.75), W - Inches(1.8), Inches(1.7)
    t.text_frame.text = deck
    t.text_frame.paragraphs[0].alignment = 1
    t.text_frame.word_wrap = True
    style(t.text_frame.paragraphs[0], 44, RGBColor(255, 255, 255), True)
    st.left, st.top, st.width, st.height = Inches(0.9), Inches(4.5), W - Inches(1.8), Inches(1.2)
    st.text_frame.text = sub or time.strftime("%d %B %Y")
    st.text_frame.paragraphs[0].alignment = 1
    style(st.text_frame.paragraphs[0], 22, RGBColor(0xA8, 0xD8, 0xBC))

    for sl in slides:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        bg(s, PAPER)
        bar(s, 0, 0, W, Inches(0.12), ACCENT)
        bar(s, Inches(0.55), Inches(0.66), Inches(0.09), Inches(0.72), TEAL)
        title_shape, body = s.shapes.title, s.placeholders[1]
        title_shape.left, title_shape.top, title_shape.width, title_shape.height = Inches(0.8), Inches(0.5), W - Inches(1.6), Inches(1.1)
        title_shape.text_frame.text = sl["title"] or " "
        title_shape.text_frame.paragraphs[0].alignment = 1
        title_shape.text_frame.word_wrap = True
        style(title_shape.text_frame.paragraphs[0], LK["slide_title_pt"] - 2, INK, True)
        body.left, body.top, body.width, body.height = Inches(0.9), Inches(1.8), W - Inches(1.8), H - Inches(2.4)
        tf = body.text_frame
        tf.word_wrap = True
        if sl["image"] and not sl["table"]:
            from PIL import Image
            has_text = bool(sl["items"] or sl["code"])
            area_l, area_t, area_h = (Inches(7.5) if has_text else Inches(0.9)), Inches(1.8), H - Inches(2.6)
            area_w = (W - Inches(0.9) - area_l)
            if has_text:
                body.width = Inches(6.3)
            with Image.open(sl["image"]["path"]) as im:
                iw, ih = im.size
            scale = min(area_w / iw, (area_h - (Inches(0.4) if sl["image"]["caption"] else 0)) / ih)
            pw, ph = int(iw * scale), int(ih * scale)
            px, py = area_l + (area_w - pw) // 2, area_t + ((area_h - (Inches(0.4) if sl["image"]["caption"] else 0)) - ph) // 2
            s.shapes.add_picture(str(sl["image"]["path"]), px, py, pw, ph)
            if sl["image"]["caption"]:
                cb = s.shapes.add_textbox(area_l, py + ph + Inches(0.05), area_w, Inches(0.35))
                cb.text_frame.text = sl["image"]["caption"]
                cb.text_frame.paragraphs[0].alignment = 2
                style(cb.text_frame.paragraphs[0], 12, MUTED)
            if not has_text:
                body._element.getparent().remove(body._element)
        if sl["table"]:
            rows, cols = len(sl["table"]), max(len(r) for r in sl["table"])
            gt = s.shapes.add_table(rows, cols, Inches(0.9), Inches(1.9), W - Inches(1.8), Inches(0.5) * rows)
            body._element.getparent().remove(body._element)
            for ri, row in enumerate(sl["table"]):
                for ci in range(cols):
                    cell = gt.table.cell(ri, ci)
                    cell.text = row[ci] if ci < len(row) else ""
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = INK if ri == 0 else (RGBColor(255, 255, 255) if ri % 2 else RGBColor(*look.rgb(LK["tint"])))
                    for p in cell.text_frame.paragraphs:
                        for r in p.runs:
                            r.font.size, r.font.bold = Pt(20), ri == 0
                            r.font.color.rgb = RGBColor(255, 255, 255) if ri == 0 else INK
        elif sl["code"] and not sl["items"]:
            tf.text = sl["code"]
            for p in tf.paragraphs:
                ppr = p._p.get_or_add_pPr()
                ppr.set("marL", "0")
                ppr.set("indent", "0")
                ppr.insert(0, p._p.makeelement("{http://schemas.openxmlformats.org/drawingml/2006/main}buNone", {}))
                for r in p.runs:
                    r.font.name, r.font.size, r.font.color.rgb = "Consolas", Pt(16), INK
        else:
            items = sl["items"] or ([{"text": sl["code"], "level": 0}] if sl["code"] else [])
            n, chars = max(len(items), 1), sum(len(i["text"]) for i in items)
            size = 36 if n <= 3 and chars < 170 else 32 if n <= 4 and chars < 260 else 26 if n <= 6 and chars < 480 else 22 if n <= 9 else 18
            if sl["image"]:
                size = min(size, 26)
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE if n <= 4 else MSO_ANCHOR.TOP
            for k, it in enumerate(items):
                p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
                p.text, p.level = it["text"], it["level"]
                p.space_after = Pt(10)
                style(p, size - 3 * it["level"], INK if it["level"] == 0 else MUTED)
        if sl["notes"]:
            s.notes_slide.notes_text_frame.text = "\n".join(sl["notes"])
        # footer
        foot = s.shapes.add_textbox(Inches(0.9), H - Inches(0.6), Inches(8), Inches(0.4))
        foot.text_frame.text = deck
        style(foot.text_frame.paragraphs[0], 12, MUTED)
        num = s.shapes.add_textbox(W - Inches(1.4), H - Inches(0.6), Inches(0.9), Inches(0.4))
        num.text_frame.text = str(prs.slides.index(s) + 1)
        num.text_frame.paragraphs[0].alignment = 3
        style(num.text_frame.paragraphs[0], 12, MUTED)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


SLIDE_CSS = """
@page { size: 13.333in 7.5in; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; font-family: "Noto Sans", "DejaVu Sans", Inter, Arial, sans-serif; }
.slide { width: 13.333in; height: 7.5in; page-break-after: always; break-after: page; position: relative; overflow: hidden; padding: .6in .9in .8in; background: #f5f8f6; color: #12201a; display: flex; flex-direction: column; }
.slide::before { content: ""; position: absolute; left: 0; top: 0; right: 0; height: .12in; background: #14633a; }
.slide h2 { font-size: 36pt; margin: .1in 0 .3in .22in; letter-spacing: -.01em; position: relative; }
.slide h2::before { content: ""; position: absolute; left: -.22in; top: .08in; bottom: .08in; width: .09in; background: #3ddc84; border-radius: 2px; }
.slide .body { flex: 1; display: flex; align-items: center; } .slide .body.top { align-items: flex-start; }
.slide ul { margin: 0; padding-left: .35in; } .slide li { margin: 0 0 .14in; line-height: 1.35; } .slide li::marker { color: #14633a; } .slide li.l1 { margin-left: .35in; color: #5b6b62; } .slide li.l2 { margin-left: .7in; color: #5b6b62; }
.slide table { border-collapse: collapse; font-size: 20pt; width: 100%; } .slide th, .slide td { border: 1px solid #cfe0d5; padding: 9px 14px; } .slide th { background: #12201a; color: #fff; } .slide tr:nth-child(even) td { background: #e6f0ea; } .slide td { background: #fff; }
.slide pre { background: #07100b; color: #dbe8df; padding: .2in .25in; border-radius: 10px; font-size: 15pt; white-space: pre-wrap; width: 100%; }
.slide .num { position: absolute; right: .5in; bottom: .3in; font-size: 12pt; color: #5b6b62; } .slide .deck { position: absolute; left: .9in; bottom: .3in; font-size: 12pt; color: #5b6b62; }
.slide .split { display: flex; gap: .5in; align-items: center; width: 100%; } .slide .split > ul { flex: 1 1 52%; }
.slide .pic { flex: 1 1 48%; text-align: center; width: 100%; } .slide .pic img { max-width: 100%; max-height: 4.6in; border-radius: 8px; } .slide .pic.solo img { width: 100%; height: 4.6in; object-fit: contain; } .slide .pic .cap { display: block; font-size: 12pt; color: #5b6b62; margin-top: .08in; }
.slide.title { background: #07100b; color: #fff; padding-top: 2.6in; display: block; } .slide.title::before { background: none; }
.slide.title .rule { width: 1.4in; height: .09in; background: #3ddc84; margin-bottom: .25in; }
.slide.title h1 { font-size: 44pt; margin: 0 0 .25in; line-height: 1.15; } .slide.title p { font-size: 22pt; color: #a8d8bc; margin: 0; }
"""


def _slides_html(title: str, text: str) -> str:
    deck, sub, slides = parse_slides(title, text)
    e = htmllib.escape
    out = [f'<section class="slide title"><div class="rule"></div><h1>{e(deck)}</h1><p>{e(sub or time.strftime("%d %B %Y"))}</p></section>']
    for n, sl in enumerate(slides, 2):
        body, top = "", ""
        pic = ""
        if sl["image"] and not sl["table"]:
            ip = sl["image"]["path"]
            uri = f"data:{images.MIME.get(ip.suffix.lstrip('.'), 'image/png')};base64,{base64.b64encode(ip.read_bytes()).decode()}"
            cap = f'<span class="cap">{e(sl["image"]["caption"])}</span>' if sl["image"]["caption"] else ""
            pic = f'<div class="pic"><img src="{uri}" alt="">{cap}</div>'
        if sl["table"]:
            rows = "".join("<tr>" + "".join(f"<{'th' if ri == 0 else 'td'}>{e(c)}</{'th' if ri == 0 else 'td'}>" for c in row) + "</tr>" for ri, row in enumerate(sl["table"]))
            body = f"<table>{rows}</table>"
        elif sl["code"] and not sl["items"]:
            body = f"<pre>{e(sl['code'])}</pre>"
        else:
            items = sl["items"] or ([{"text": sl["code"], "level": 0}] if sl["code"] else [])
            chars = sum(len(i["text"]) for i in items)
            size = 36 if len(items) <= 3 and chars < 170 else 32 if len(items) <= 4 and chars < 260 else 26 if len(items) <= 6 and chars < 480 else 22 if len(items) <= 9 else 18
            top = " top" if len(items) > 4 else ""
            if pic:
                size = min(size, 26)
            body = f'<ul style="font-size:{size}pt">' + "".join(f'<li class="l{i["level"]}">{e(i["text"])}</li>' for i in items) + "</ul>" if items else ""
        if pic:
            body = f'<div class="split">{body}{pic}</div>' if body else pic.replace('class="pic"', 'class="pic solo"')
        out.append(f'<section class="slide"><h2>{e(sl["title"])}</h2><div class="body{top}">{body}</div><div class="deck">{e(deck)}</div><div class="num">{n}</div></section>')
    return f'<!doctype html><html><head><meta charset="utf-8">{CSP}<title>{e(deck)}</title><style>{look.css(SLIDE_CSS)}</style></head><body>{"".join(out)}</body></html>'


# ------------------------------------------------------------------ XLSX


def _xlsx(title: str, csv_text: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    rows = list(csv.reader(io.StringIO(csv_text.strip())))
    if not rows:
        raise DocError("The CSV content is empty.")
    wb = Workbook()
    ws = wb.active
    ws.title = (re.sub(r"[\[\]:*?/\\]", " ", title).strip() or "Sheet1")[:31]

    def cast(v: str):
        v = v.strip()
        if re.fullmatch(r"-?\d+", v) and len(v) < 16:
            return int(v)
        if re.fullmatch(r"-?\d+\.\d+", v):
            return float(v)
        return v

    for r, row in enumerate(rows, 1):
        for c, v in enumerate(row, 1):
            val = v if r == 1 else cast(v)
            cell = ws.cell(r, c, val)
            if isinstance(val, str) and val[:1] in "=+-@":
                cell.data_type = "s"  # never let model/web text become a live formula (spreadsheet injection)
            if r == 1:
                cell.font = Font(bold=True, color="FFFFFF", name=look.current()["office_font"])
                cell.fill = PatternFill("solid", fgColor=look.current()["accent"].lstrip("#").upper())
                cell.alignment = Alignment(vertical="center")
    for c in range(1, max(len(r) for r in rows) + 1):
        width = max((len(str(r[c - 1])) for r in rows if len(r) >= c), default=8)
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 3, 10), 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    thin = Side(style="thin", color=look.mix(look.current()["accent"], .80).lstrip("#").upper() if look.current()["accent"] != look.DEFAULT["accent"] else "D3E0D8")
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
            if cell.row > 1:
                if cell.row % 2 == 1:
                    cell.fill = PatternFill("solid", fgColor="F3F8F5")
                if isinstance(cell.value, float):
                    cell.number_format = "#,##0.00"
                elif isinstance(cell.value, int):
                    cell.number_format = "#,##0"
                if isinstance(cell.value, (int, float)):
                    cell.alignment = Alignment(horizontal="right")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ public API


# ------------------------------------------------------------------ build + sources
#
# Every document the app makes keeps its Markdown (or CSV) source in a sidecar, `NAME.ext.src.json`, so it can be edited
# later: the file is re-rendered from the edited source. Pictures and diagrams stay in the source as `img:NAME` / mermaid blocks.

KIND_FORMATS = {"document": DOC_FORMATS, "slides": SLIDE_FORMATS, "sheet": ("xlsx",)}
HISTORY_LIMIT = 12
# The chat turn a change belongs to: several edits made for one request are undone together.
TURN: contextvars.ContextVar[str | None] = contextvars.ContextVar("turn", default=None)


def _sidecar(p: Path) -> Path:
    return p.with_name(p.name + ".src.json")


def _read_sidecar(p: Path) -> dict | None:
    sc = _sidecar(p)
    if not sc.is_file():
        return None
    try:
        return json.loads(sc.read_text())
    except (OSError, ValueError):
        return None


def _write_sidecar(p: Path, data: dict):
    sc = _sidecar(p)
    tmp = sc.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(sc)


def _is_editable(p: Path) -> bool:
    return _sidecar(p).is_file() or p.suffix in (".md", ".txt")


async def _slides_source(title: str, text: str, strict: bool, warnings: list) -> tuple[str, str]:
    """(source to keep, text with diagrams drawn). A reply written as a document ('# ' headings only) becomes one slide per heading."""
    prepared = await _prepare(text, strict, warnings)
    if not parse_slides(title, prepared)[2]:
        if len(re.findall(r"^# ", text, re.M)) >= 2:
            text = re.sub(r"^# ", "## ", text, flags=re.M)
            prepared = await _prepare(text, strict, warnings)
        else:
            raise DocError("No slides found: start each slide with a '## Slide title' heading followed by bullet points.")
    return text, prepared


async def _build(kind: str, title: str, text: str, fmt: str, strict: bool = True, warnings: list | None = None) -> tuple[bytes, str]:
    """Render a source into file bytes. Returns (bytes, source to store)."""
    warnings = warnings if warnings is not None else []
    # the look comes from the skill for this kind of file (see style.py); to_thread copies it into the rendering thread
    look.use("xlsx" if kind == "sheet" else "pptx" if kind == "slides" else {"docx": "docx", "html": "html"}.get(fmt, "pdf"))
    if kind == "sheet":
        return await asyncio.to_thread(_xlsx, title, text), text
    if kind == "slides":
        text, prepared = await _slides_source(title, text, strict, warnings)
        prepared = mathtext.to_plain(prepared)  # a slide holds bullets, not typeset formulas: they read as Unicode
        return (await asyncio.to_thread(_pptx, title, prepared) if fmt == "pptx" else await _html_to_pdf(_slides_html(title, prepared))), text
    prepared = await _prepare(text, strict, warnings) if fmt in ("pdf", "docx", "html") else text
    if fmt == "pdf": return await _html_to_pdf(_doc_html(title, prepared, await _typeset(prepared))), text
    if fmt == "docx": return await asyncio.to_thread(_docx, title, mathtext.to_plain(prepared)), text
    if fmt == "html": return _doc_html(title, prepared, await _typeset(prepared)).encode(), text
    if fmt == "txt": return (mathtext.to_plain(text) + "\n").encode(), text
    return (text + "\n").encode(), text


async def _create(kind: str, title: str, text: str, fmt: str, strict: bool = True) -> dict:
    warnings: list[str] = []
    data, source = await _build(kind, title, text, fmt, strict, warnings)
    p = _target(title, fmt)
    p.write_bytes(data)
    _write_sidecar(p, {"kind": kind, "title": title, "source": source, "updated": time.time(), "history": []})
    return {**_info(p), "warnings": warnings}


async def create_document(title: str, content: str, fmt: str = "pdf", strict: bool = True) -> dict:
    fmt = (fmt or "pdf").lower().lstrip(".")
    if fmt not in DOC_FORMATS:
        raise DocError(f"Unsupported format '{fmt}'. Choose one of: {', '.join(DOC_FORMATS)}.")
    given = (title or "Document").strip()[:120]
    title = doclint.clean_title(given)  # "Document Outline for PDF on X" names the job, not the subject
    unknown: list[str] = []
    text = _clean_markdown(content, unknown)
    if title != given and text.startswith(f"# {given}"):
        text = f"# {title}" + text[len(f"# {given}"):]
    if not text:
        raise DocError("The content is empty.")
    _check_pictures(unknown, strict)
    text = doclint.tidy(text, topic=title)  # instructions the model echoed into the text, empty headings, bare source addresses
    return await _create("document", title, text, fmt, strict)


async def create_slides(title: str, content: str, fmt: str = "pptx", strict: bool = True) -> dict:
    fmt = (fmt or "pptx").lower().lstrip(".")
    if fmt not in SLIDE_FORMATS:
        raise DocError(f"Unsupported slide format '{fmt}'. Choose pptx or pdf.")
    unknown: list[str] = []
    text = _clean_markdown(content, unknown)
    _check_pictures(unknown, strict)
    return await _create("slides", (title or "Presentation").strip()[:120], text, fmt, strict)


async def create_spreadsheet(title: str, csv_text: str) -> dict:
    return await _create("sheet", (title or "Spreadsheet").strip()[:120], _clean_markdown(csv_text), "xlsx")


async def export(title: str, markdown: str, fmt: str) -> dict:
    """UI 'Save as…': pptx/slides come from the same markdown (## headings become slides)."""
    if fmt == "xlsx":  # a table written as CSV
        return await create_spreadsheet(title, markdown)
    if fmt in ("pptx", "slides-pdf"):
        return await create_slides(title, markdown, "pptx" if fmt == "pptx" else "pdf", strict=False)
    return await create_document(title, markdown, fmt, strict=False)


# ------------------------------------------------------------------ editing


def get_source(name: str) -> dict:
    p = doc_path(name)
    sc = _read_sidecar(p)
    if sc is None:
        if p.suffix in (".md", ".txt"):
            sc = {"kind": "document", "title": p.stem, "source": p.read_text(encoding="utf-8", errors="replace"), "history": []}
        else:
            raise DocError("This file was made before editing was available, so its source is not stored. Ask for a new version instead.")
    return {"name": p.name, "format": p.suffix.lstrip("."), "kind": sc["kind"], "title": sc.get("title") or p.stem, "source": sc["source"],
            "history": len(sc.get("history", [])), "formats": list(KIND_FORMATS[sc["kind"]]), "outline": outline(sc["source"]),
            "facts": facts(sc["source"], sc["kind"]), "updated": sc.get("updated")}


async def update_source(name: str, source: str, title: str | None = None, by: str = "user") -> dict:
    """Replace a document's source and re-render the same file. The previous source is kept so the change can be undone."""
    cur = get_source(name)
    p = doc_path(name)
    kind, fmt = cur["kind"], p.suffix.lstrip(".")
    unknown: list[str] = []
    text = _clean_markdown(source, unknown)
    if not text:
        raise DocError("The content is empty.")
    _check_pictures(unknown, by == "model")
    title = (title or cur["title"]).strip()[:120] or cur["title"]
    warnings: list[str] = []
    data, stored = await _build(kind, title, text, fmt, strict=(by == "model"), warnings=warnings)
    sc = _read_sidecar(p) or {"kind": kind, "history": []}
    if stored != sc.get("source"):
        sc["history"] = (sc.get("history", []) + [{"t": sc.get("updated") or time.time(), "title": sc.get("title"), "source": sc.get("source", cur["source"]), "by": sc.get("by", "app"), "turn": TURN.get()}])[-HISTORY_LIMIT:]
    sc.update({"kind": kind, "title": title, "source": stored, "updated": time.time(), "by": by})
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    _write_sidecar(p, sc)
    return {**_info(p), "warnings": warnings}


async def revert(name: str) -> dict:
    p = doc_path(name)
    sc = _read_sidecar(p)
    if not sc or not sc.get("history"):
        raise DocError("There is nothing to undo.")
    hist = sc["history"]
    last_turn = hist[-1].get("turn")
    j = len(hist) - 1
    while last_turn and j > 0 and hist[j - 1].get("turn") == last_turn:  # every change of the same request goes back together
        j -= 1
    prev = hist[j]
    del hist[j:]
    data, stored = await _build(sc["kind"], prev.get("title") or sc["title"], prev["source"], p.suffix.lstrip("."), strict=False)
    sc.update({"title": prev.get("title") or sc["title"], "source": stored, "updated": time.time(), "by": "undo"})
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    _write_sidecar(p, sc)
    return _info(p)


_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _headings(source: str) -> list[tuple[int, int, str]]:
    """(line index, level, title) of every Markdown heading outside code fences."""
    out, fenced = [], False
    for i, line in enumerate(source.split("\n")):
        if line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced and (m := _HEAD.match(line)):
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def outline(source: str) -> list[dict]:
    return [{"level": lv, "title": t} for _, lv, t in _headings(source)]


def _find_section(source: str, heading: str, first: bool = False) -> tuple[int, int, int]:
    """(start line, end line, level) of the section called `heading` (exact, then prefix, then substring; case-insensitive)."""
    want = re.sub(r"^#+\s*", "", heading or "").strip().lower()
    heads = _headings(source)
    if not want:
        raise DocError("Say which section: give its heading text.")
    for test in (lambda t: t == want, lambda t: t.startswith(want), lambda t: want in t):
        hits = [h for h in heads if test(h[2].lower())]
        if len(hits) == 1 or (hits and first):  # first=True: a repeated heading means its first occurrence
            i, lv, _ = hits[0]
            end = next((j for j, l2, _ in heads if j > i and l2 <= lv), len(source.split("\n")))
            return i, end, lv
        if len(hits) > 1:
            raise DocError(f"'{heading}' matches several sections ({', '.join(repr(h[2]) for h in hits[:4])}). Use the full heading text.")
    names = ", ".join(repr(h[2]) for h in heads[:12]) or "(none)"
    raise DocError(f"No section called '{heading}'. Sections: {names}")


def replace_section_text(source: str, heading: str, content: str, first: bool = False) -> str:
    """Swap one section's body (keeping its heading), or the whole section if `content` starts with its own heading."""
    i, end, lv = _find_section(source, heading, first)
    lines = source.split("\n")
    new = _clean_markdown(content).strip("\n")
    if not _HEAD.match(new.split("\n", 1)[0] if new else ""):
        new = lines[i] + "\n\n" + new
    return "\n".join(lines[:i] + new.split("\n") + [""] + lines[end:]).rstrip("\n") + "\n"


def add_section_text(source: str, content: str, after: str | None = None, first: bool = False) -> str:
    new = _clean_markdown(content).strip("\n")
    if not new:
        raise DocError("The new content is empty.")
    lines = source.rstrip("\n").split("\n")
    if not after:
        return "\n".join(lines + ["", new]) + "\n"
    _, end, _ = _find_section(source, after, first)
    end = min(end, len(lines))
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:end] + ["", new, ""] + lines[end:]).rstrip("\n") + "\n"


def insert_section_start(source: str, content: str) -> str:
    """Put a new section before the first section (after the document title and any lead paragraph)."""
    new = _clean_markdown(content).strip("\n")
    lines = source.rstrip("\n").split("\n")
    first = next((i for i, lv, _t in _headings(source) if lv >= 2), None)
    if first is None:
        return "\n".join(lines + ["", new]) + "\n"
    return "\n".join(lines[:first] + [new, ""] + lines[first:]).rstrip("\n") + "\n"


def remove_section_text(source: str, heading: str, first: bool = False) -> str:
    i, end, _ = _find_section(source, heading, first)
    lines = source.split("\n")
    out = "\n".join(lines[:i] + lines[end:])
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n") + "\n"


def insert_image_text(source: str, heading: str | None, name: str, caption: str, where: str = "start", first: bool = False) -> str:
    """Put `![caption](img:name)` in a section: right after its heading ('start') or at the end of it ('end')."""
    caption = re.sub(r"[\[\]\n]", " ", caption).strip()[:100]
    ref = f"![{caption}](img:{name})"
    lines = source.rstrip("\n").split("\n")
    if not heading:
        return "\n".join([ref, ""] + lines) + "\n"
    i, end, _ = _find_section(source, heading, first)
    at = i + 1 if where == "start" else end
    while where != "start" and at > i + 1 and not lines[at - 1].strip():
        at -= 1
    return "\n".join(lines[:at] + ["", ref, ""] + lines[at:]).replace("\n\n\n", "\n\n").rstrip("\n") + "\n"


def insert_image_lines(source: str, items: list[dict]) -> str:
    """Place several pictures by LINE position, so repeated headings cannot confuse it. Each item: start (the heading's line, -1 for the top),
    end (the section's end line), name, caption, where ('start' | 'end'). Positions refer to `source`; insertion runs bottom-up so they stay valid."""
    lines = source.rstrip("\n").split("\n")
    ops = []
    for k, it in enumerate(items):
        cap = re.sub(r"[\[\]\n]", " ", it["caption"]).strip()[:100]
        at = it["start"] + 1 if it["where"] == "start" else it["end"]
        while it["where"] != "start" and at > it["start"] + 1 and not lines[at - 1].strip():
            at -= 1
        ops.append((at, k, f"![{cap}](img:{it['name']})"))
    for at, _k, ref in sorted(ops, key=lambda o: (-o[0], -o[1])):
        lines[at:at] = ["", ref, ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip("\n") + "\n"


_PIC_LINE = re.compile(r"^[ \t]*!\[[^\]]*\]\((img:[^)]*)\)[ \t]*$")


def remove_pictures_text(source: str, section: str | None = None, image: str | None = None) -> tuple[str, list[str]]:
    """Take pictures out of a document: all of them, those in one section, or one named picture. Returns (new source, names removed)."""
    lines = source.split("\n")
    lo, hi = 0, len(lines)
    if section:
        lo, hi, _ = _find_section(source, section, True)
    target = images.resolve(image) if image else None
    out, removed, fenced = [], [], False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            fenced = not fenced
        m = None if fenced else _PIC_LINE.match(line)
        if m and lo <= i < hi:
            p = images.resolve(m.group(1))
            if target is None or (p is not None and p == target) or (p is None and image and image in m.group(1)):
                removed.append(p.name if p else m.group(1)[4:])
                continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip("\n") + "\n", removed


def facts(source: str, kind: str = "document") -> dict:
    """Countable properties of a source, for checking requests like '10 images' or '5 sections'."""
    heads = _headings(source)
    return {
        "sections": sum(1 for _, lv, _t in heads if lv == 2) or (1 if heads else 0),
        "images": len(re.findall(r"!\[[^\]]*\]\(img:[^)]+\)", source)),
        "diagrams": len(re.findall(r"^[ \t]*```mermaid", source, re.M)),
        "words": len(re.findall(r"\w+", re.sub(r"```.*?```", " ", source, flags=re.S))),
        "slides": sum(1 for _, lv, _t in heads if lv == 2) if kind == "slides" else None,
        "tables": len(re.findall(r"^\|[-: |]+\|\s*$", source, re.M)),
    }


def replace_in_text(source: str, find: str, replace: str) -> str:
    if not find:
        raise DocError("Give the exact text to find.")
    n = source.count(find)
    if n == 0:
        raise DocError("That text was not found. Read the document again and copy the text exactly.")
    if n > 1:
        raise DocError(f"That text appears {n} times; include more surrounding words so it is unique.")
    return source.replace(find, replace, 1)


def list_documents() -> list[dict]:
    if not DOCS_DIR.exists():
        return []
    files = [p for p in DOCS_DIR.iterdir() if p.is_file() and p.suffix.lstrip(".") in MIME]
    out = []
    for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
        info = _info(p)
        sc = _read_sidecar(p)
        info["title"] = (sc or {}).get("title") or p.stem
        info["kind"] = (sc or {}).get("kind") or ("slides" if p.suffix == ".pptx" else "sheet" if p.suffix == ".xlsx" else "document")
        out.append(info)
    return out


def doc_path(name: str) -> Path:
    p = (DOCS_DIR / name).resolve()
    if p.parent != DOCS_DIR.resolve() or not p.is_file() or p.suffix.lstrip(".") not in MIME:  # sidecars and temp files are not documents
        raise DocError("No such document.")
    return p


def delete_document(name: str):
    p = doc_path(name)
    p.unlink()
    _sidecar(p).unlink(missing_ok=True)
