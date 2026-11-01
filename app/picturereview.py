"""Looking at a document's pictures with the vision model, and swapping the poor ones for better ones.

The review runs with a vision-capable model resident (the server swaps it in and restores the previous model afterwards). For every picture
it asks "does this show what the section is about?", and for each weak one it fetches several candidates from the web, has the vision model
score them too, and puts in the best one, but only when it is clearly better than what was there.
"""

from __future__ import annotations

import base64
import io
import re
from collections.abc import Awaitable, Callable

from PIL import Image

import docagent
import documents as docs
import images
import webimages

VisionJson = Callable[[list, dict, int], Awaitable[dict]]
KEEP_SCORE = 4          # at or above this a picture stays
MAX_CANDIDATES = 4      # downloads judged per weak picture
_PIC = re.compile(r"^[ \t]*!\[([^\]]*)\]\(img:([^)\s]*)\)[ \t]*$")
_GENERIC = re.compile(r"^\s*(introduction|overview|summary|conclusion|background|about)\b", re.I)

JUDGE_SYSTEM = (
    "You check whether a picture suits a section of a document. Look at the picture and answer in JSON.\n"
    "`shows`: what the picture actually shows, in at most 12 words.\n"
    "`score`: 5 = clearly shows the section's subject, sharp and clean; 4 = relevant and fine to use; 3 = only loosely related or mediocre; "
    "2 = mostly unrelated or poor quality; 1 = wrong subject, or an advert, banner, slide, logo, screenshot, collage or mostly text.\n"
    "`problem`: none | wrong_subject | advert_or_text | low_quality | unrelated | collage.\n"
    "Judge only what you can see. A watermark or a big text banner lowers the score. Do not give a high score because the caption sounds right.")
JUDGE_SCHEMA = {"type": "object", "properties": {"shows": {"type": "string"}, "score": {"type": "integer", "minimum": 1, "maximum": 5},
                                                  "problem": {"type": "string", "enum": ["none", "wrong_subject", "advert_or_text", "low_quality", "unrelated", "collage"]}},
                "required": ["shows", "score", "problem"]}


def _data_url(path) -> str:
    im = Image.open(path)
    im.load()
    im = im.convert("RGB")
    im.thumbnail((640, 640))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


async def judge(vjson: VisionJson, name: str, subject: str, context: str) -> dict:
    """{score, shows, problem} for picture `name` as an illustration of `subject`."""
    p = images.image_path(name) or images.resolve(name)
    if p is None:
        return {"score": 1, "shows": "(file missing)", "problem": "unrelated"}
    user = [{"type": "text", "text": f"Section subject: {subject}\nWhat the section says: {context[:300]}\n\nDoes this picture suit the section?"},
            {"type": "image_url", "image_url": {"url": _data_url(p)}}]
    try:
        d = await vjson([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}], JUDGE_SCHEMA, 160)
    except Exception as e:  # noqa: BLE001 - an unreadable picture counts as unusable, a model failure must surface
        if isinstance(e, (OSError, ValueError)) and not isinstance(e, ConnectionError):
            return {"score": 1, "shows": "(unreadable picture)", "problem": "low_quality"}
        raise
    return {"score": int(d.get("score") or 1), "shows": str(d.get("shows") or "")[:120], "problem": str(d.get("problem") or "none")}


def _subject(heading: str, doc_title: str) -> str:
    """What a section is about: "Place 3: Taxila" -> Taxila; "Taxila: Learning Hub of Ancient India" -> Taxila (the name, not the tagline)."""
    numbered = re.match(r"^\s*[A-Za-z]+\s+\d{1,3}\s*[:.)\-–—]\s*(.+)$", heading)
    h = numbered.group(1).strip() if numbered else heading.strip()
    if not numbered and ":" in h:
        head = h.split(":", 1)[0].strip()
        h = head if head and len(head.split()) <= 5 else h
    return doc_title if _GENERIC.match(h) else h


def pictures_in(source: str, only: list[str] | None = None) -> list[dict]:
    """Each picture line with its section: [{line, section, subject, name, caption, context}]."""
    lines = source.split("\n")
    title = next((t for _i, lv, t in docs._headings(source) if lv == 1), "")
    starts = docagent._starts_for(source, only)
    secs = docagent._level2(source, starts)
    if not secs and not only:
        secs = [(-1, len(lines), title or "Document")]
    out = []
    for start, end, heading in secs:
        body, fenced = [], False
        for i in range(start + 1, end):
            ln = lines[i]
            if ln.strip().startswith("```"):
                fenced = not fenced
            m = None if fenced else _PIC.match(ln)
            if m:
                out.append({"line": i, "section": heading, "subject": _subject(heading, title), "name": m.group(2), "caption": m.group(1)})
            elif not fenced and ln.strip() and not ln.lstrip().startswith("#"):
                body.append(ln.strip())
        ctx = " ".join(body)[:400]
        for p in out:
            if p["section"] == heading and "context" not in p:
                p["context"] = ctx
    return out


def _queries(subject: str, doc_title: str, problem: str) -> list[str]:
    base = re.sub(r"\s+", " ", subject).strip()
    topic = re.sub(r"\(.*?\)", "", doc_title).strip()
    out = [base]
    if topic and topic.lower() not in base.lower() and len(topic.split()) <= 6:
        out.append(f"{base} {topic}")
    out.append(f"{base} photo")
    return list(dict.fromkeys(out))


async def review(name: str, vjson: VisionJson, fix: bool = True, only: list[str] | None = None, keep_score: int = KEEP_SCORE) -> dict:
    """Judge every picture; with `fix`, replace the weak ones. Returns {checked: [...], replaced, kept, unchanged, info}."""
    cur = docs.get_source(name)
    source = cur["source"]
    title = next((t for _i, lv, t in docs._headings(source) if lv == 1), "")
    pics = pictures_in(source, only)
    if not pics:
        raise docagent.AgentError("The document has no pictures to review." if not only else f"There are no pictures in {', '.join(only)}.")
    lines = source.split("\n")
    used = {p["name"] for p in pics}
    used_urls = set()
    for n in used:
        pth = images.resolve(n)
        if pth is not None:
            inf = images.info(pth)
            used_urls.update(filter(None, [inf.get("source_url"), inf.get("image_url")]))
    report, kept_new, saved_unused = [], [], []
    for pic in pics:
        j = await judge(vjson, pic["name"], pic["subject"], pic.get("context", ""))
        row = {"section": pic["section"], "name": pic["name"], "score": j["score"], "shows": j["shows"], "problem": j["problem"], "action": "kept"}
        if j["score"] < keep_score:
            if not fix:
                row["action"] = "weak"
            else:
                best, best_j, tried = None, None, 0
                for q in _queries(pic["subject"], title, j["problem"]):
                    try:
                        found = await webimages.search(q, 8)
                    except webimages.WebImageError:
                        continue
                    for c in found:
                        if tried >= MAX_CANDIDATES or (best_j and best_j["score"] >= 5):
                            break
                        if c["page"] in used_urls:
                            continue
                        try:
                            info = await webimages.save(c["id"])
                        except webimages.WebImageError:
                            continue
                        tried += 1
                        used_urls.add(c["page"])
                        cj = await judge(vjson, info["name"], pic["subject"], pic.get("context", ""))
                        if best_j is None or cj["score"] > best_j["score"]:
                            if best:
                                saved_unused.append(best["name"])
                            best, best_j = info, cj
                        else:
                            saved_unused.append(info["name"])
                    if best_j and best_j["score"] >= keep_score:
                        break
                if best and best_j["score"] > j["score"] and best_j["score"] >= max(3, keep_score - 1):
                    cap = re.sub(r"[\[\]\n]", " ", (best.get("prompt") or pic["subject"]))[:80].strip()
                    lines[pic["line"]] = f"![{cap}{f' (source: {best['credit']})' if best.get('credit') else ''}](img:{best['name']})"
                    kept_new.append(best["name"])
                    row.update(action="replaced", new=best["name"], new_score=best_j["score"], new_shows=best_j["shows"])
                else:
                    if best:
                        saved_unused.append(best["name"])
                    row.update(action="no_better", tried=tried)
        report.append(row)
    for n in saved_unused:  # candidates that were downloaded to compare and not used
        if n not in kept_new:
            try:
                images.delete_image(n)
            except (FileNotFoundError, OSError):
                pass
    replaced = [r for r in report if r["action"] == "replaced"]
    info = docs._info(docs.doc_path(name))
    if replaced:
        info = await docs.update_source(name, "\n".join(lines), by="model")
    return {"checked": report, "replaced": len(replaced), "kept": sum(1 for r in report if r["action"] == "kept"),
            "unchanged": sum(1 for r in report if r["action"] in ("no_better", "weak")), "info": info}


def summary(r: dict) -> str:
    rows = r["checked"]
    out = [f"Looked at {len(rows)} picture{'s' if len(rows) != 1 else ''} with the vision model: {r['kept']} good, {r['replaced']} replaced, {r['unchanged']} not good but no better one found."]
    for x in rows:
        if x["action"] == "replaced":
            out.append(f"- {x['section']}: replaced (was {x['score']}/5: {x['shows']}; now {x['new_score']}/5: {x['new_shows']})")
        elif x["action"] in ("no_better", "weak"):
            out.append(f"- {x['section']}: still weak ({x['score']}/5: {x['shows']})")
    return "\n".join(out)
