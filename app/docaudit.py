"""Finding and fixing the damage that repeated model edits leave in a document.

Deterministic on purpose: a small model asked to "clean this up" tends to rewrite (and lose) content. These rules only remove what is
clearly junk (placeholders, leftover instructions, empty or stub sections, repeated sections/paragraphs/pictures, broken picture
references) and only *report* what needs new content (numbered sections with gaps).
"""

from __future__ import annotations

import re

import documents as docs
import images

STUB_TITLE = re.compile(r"^\s*(new step|untitled( section)?|section|heading|placeholder|todo|tbd|image|images|relevant images?|picture|pictures|figure|illustrations?)\s*:?\s*$", re.I)
PLACEHOLDER = re.compile(r"\[\s*(to be added|to be written|tbd|todo|placeholder|insert[^\]]*|add [^\]]*here|lorem ipsum[^\]]*)\s*\]|\blorem ipsum\b|^\s*(tbd|todo)\s*[:.]?\s*$|\bto be (added|written|completed)\b\s*[.\]*]*\s*$", re.I)
# Commentary a rewrite leaves about itself ("*Note: The document 'x.pdf' has been updated to reflect ...*"): never part of a document.
EDIT_NOTE = re.compile(
    r"^\s*[*_>\s]*(?:note|update|edit)\s*:.*\b(?:document|section|file|text|report)\b.*\b(?:updated|revised|edited|rewritten|reflect(?:s|ed)?|changed|modified)\b"
    r"|^\s*[*_>\s]*(?:the|this)\s+(?:document|section)\b[^.\n]{0,160}\b(?:has|have) been (?:updated|revised|edited|rewritten|changed)\b",
    re.I)
META = re.compile(
    r"generated from publicly available|\bbelow are (some )?(representative|relevant|illustrative) (images|pictures)\b|\bimage prompts?\b|\bprompt tracking\b"
    r"|\bas an ai( language model)?\b|^\s*(sure|certainly|of course)[,!. ]|\bi hope this helps\b|\blet me know if\b|^\s*note to self\b|\bhere is (the|a|your) (updated|revised|new) (document|section|version)\b",
    re.I)
NUMBERED = re.compile(r"^\s*(?P<word>[A-Za-z][\w ]{1,20}?)\s+(?P<n>\d{1,3})\s*[:.)\-–—]")
_PIC = re.compile(r"^[ \t]*!\[[^\]]*\]\((img:[^)]*)\)[ \t]*$")


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", " ", t.lower()).strip()


def _sections(source: str, level: int = 2) -> list[dict]:
    lines = source.split("\n")
    heads = docs._headings(source)
    out = []
    for k, (i, lv, t) in enumerate(heads):
        if lv != level:
            continue
        end = next((j for j, l2, _ in heads[k + 1:] if l2 <= lv), len(lines))
        out.append({"start": i, "end": end, "title": t, "words": len(re.findall(r"\w+", "\n".join(lines[i + 1:end])))})
    return out


def _is_echo(title: str, section: str, others: set[str]) -> bool:
    """A section that is the document title, or "<document title> - X" where X is another section of the document (a leftover copy)."""
    s = _norm(section)
    if not title:
        return False
    if s == title:
        return True
    if s.startswith(title + " "):
        return _norm_rest(s[len(title):]) in others
    return False


def _norm_rest(t: str) -> str:
    return re.sub(r"^\s*[-–—:]\s*", "", t).strip()


def _doc_title(source: str) -> str:
    return next((t for _, lv, t in docs._headings(source) if lv == 1), "")


def audit(source: str) -> list[dict]:
    """Issues in `source`: [{type, detail, fixable}]. `fixable` ones are removed by tidy()."""
    issues = []
    if docs.close_unclosed_fence(source)[1]:
        issues.append({"type": "unclosed_fence", "detail": "a code or diagram block is never closed, so everything after it shows as code", "fixable": True})
    source, _ = docs.close_unclosed_fence(source)
    lines = source.split("\n")
    secs = _sections(source)
    title = _norm(_doc_title(source))
    seen: dict[str, list[dict]] = {}
    for s in secs:
        seen.setdefault(_norm(s["title"]), []).append(s)
    for key, group in seen.items():
        if len(group) > 1:
            issues.append({"type": "duplicate_section", "detail": f"“{group[0]['title']}” appears {len(group)} times", "fixable": True})
    for s in secs:
        if STUB_TITLE.match(s["title"]):
            issues.append({"type": "stub_section", "detail": f"a leftover “{s['title']}” section", "fixable": True})
        elif _is_echo(title, s["title"], {_norm(x["title"]) for x in secs}):
            issues.append({"type": "echo_title", "detail": f"a section repeating the document title (“{s['title']}”)", "fixable": True})
        body = [ln for ln in lines[s["start"] + 1:s["end"]] if ln.strip()]
        if not body:
            issues.append({"type": "empty_section", "detail": f"“{s['title']}” has no content", "fixable": True})
    n_ph = sum(1 for ln in lines if PLACEHOLDER.search(ln))
    if n_ph:
        issues.append({"type": "placeholder", "detail": f"{n_ph} placeholder line{'s' if n_ph != 1 else ''} (such as “[To be added]”)", "fixable": True})
    n_meta = sum(1 for ln in lines if (META.search(ln) or EDIT_NOTE.search(ln)) and not _PIC.match(ln))
    if n_meta:
        issues.append({"type": "meta_text", "detail": f"{n_meta} line{'s' if n_meta != 1 else ''} of leftover instructions or commentary", "fixable": True})
    broken = [m.group(1) for ln in lines if (m := _PIC.match(ln)) and images.resolve(m.group(1)) is None]
    if broken:
        issues.append({"type": "broken_picture", "detail": f"{len(broken)} picture reference{'s' if len(broken) != 1 else ''} that match no file", "fixable": True})
    for s in secs:
        names = [p.name for ln in lines[s["start"] + 1:s["end"]] if (m := _PIC.match(ln)) and (p := images.resolve(m.group(1)))]
        if len(names) != len(set(names)):
            issues.append({"type": "duplicate_picture", "detail": f"“{s['title']}” shows the same picture more than once", "fixable": True})
    paras: dict[str, int] = {}
    for blk in re.split(r"\n\s*\n", source):
        t = _norm(blk)
        if len(t) >= 60 and not blk.lstrip().startswith(("#", "```", "|")):
            paras[t] = paras.get(t, 0) + 1
    if any(c > 1 for c in paras.values()):
        issues.append({"type": "duplicate_paragraph", "detail": f"{sum(c - 1 for c in paras.values() if c > 1)} repeated paragraph(s)", "fixable": True})
    # numbered sections ("Place 1: …", "Day 3 …") with a gap or a repeat: content is missing, which only new text can fix
    groups: dict[str, list[int]] = {}
    for s in secs:
        m = NUMBERED.match(s["title"])
        if m:
            groups.setdefault(m.group("word").strip().lower(), []).append(int(m.group("n")))
    for word, nums in groups.items():
        if len(nums) >= 3:
            missing = [n for n in range(min(nums), max(nums) + 1) if n not in nums]
            if missing:
                issues.append({"type": "numbering_gap", "detail": f"“{word.title()}” sections skip {', '.join(map(str, missing))}: those sections are missing", "fixable": False})
    return issues


def _drop_lines(lines: list[str], drop: set[int]) -> list[str]:
    return [ln for i, ln in enumerate(lines) if i not in drop]


def _clean_blank(lines: list[str]) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n") + "\n"


def tidy(source: str) -> tuple[str, list[str]]:
    """Remove the clear junk. Returns (new source, what was fixed). Content is never rewritten."""
    fixed: list[str] = []
    cur, closed = docs.close_unclosed_fence(source)
    if closed:
        fixed.append("closed a diagram/code block that never ended (everything after it was showing as code)")

    # 1. stub sections and a section that just repeats the document title (a leftover copy of the whole document)
    lines = cur.split("\n")
    title = _norm(_doc_title(cur))
    drop: set[int] = set()
    for s in _sections(cur):
        if STUB_TITLE.match(s["title"]) or _is_echo(title, s["title"], {_norm(x["title"]) for x in _sections(cur)}):
            drop.update(range(s["start"], s["end"]))
            fixed.append(f"removed the leftover section “{s['title']}”")
    for i, lv, t in docs._headings(cur):  # a stub sub-heading ("### Relevant Images") loses only its heading line
        if lv >= 3 and STUB_TITLE.match(t):
            drop.add(i)
            fixed.append(f"removed the empty heading “{t}”")
    cur = _clean_blank(_drop_lines(lines, drop))

    # 2. placeholders and leftover commentary
    lines = cur.split("\n")
    fenced, drop = False, set()
    n_ph = n_meta = 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            fenced = not fenced
        if fenced or _PIC.match(ln) or ln.lstrip().startswith("#"):
            continue
        if PLACEHOLDER.search(ln):
            drop.add(i); n_ph += 1
        elif META.search(ln) or EDIT_NOTE.search(ln):
            drop.add(i); n_meta += 1
    if n_ph:
        fixed.append(f"removed {n_ph} placeholder line{'s' if n_ph != 1 else ''}")
    if n_meta:
        fixed.append(f"removed {n_meta} line{'s' if n_meta != 1 else ''} of leftover instructions")
    cur = _clean_blank(_drop_lines(lines, drop))

    # 3. repeated sections: keep the first position, with the fullest text
    secs = _sections(cur)
    groups: dict[str, list[dict]] = {}
    for s in secs:
        groups.setdefault(_norm(s["title"]), []).append(s)
    lines = cur.split("\n")
    drop, replace = set(), {}
    for group in groups.values():
        if len(group) < 2:
            continue
        best = max(group, key=lambda s: s["words"])
        first = group[0]
        if best is not first:
            replace[first["start"]] = (first["end"], lines[best["start"]:best["end"]])
        for s in group[1:]:
            drop.update(range(s["start"], s["end"]))
        fixed.append(f"merged the {len(group)} copies of “{first['title']}” into one")
    out, i = [], 0
    while i < len(lines):
        if i in replace:
            end, body = replace[i]
            out.extend(body)
            i = end
        elif i in drop:
            i += 1
        else:
            out.append(lines[i])
            i += 1
    cur = _clean_blank(out)

    # 4. pictures: broken references and the same picture twice in one section
    lines = cur.split("\n")
    drop, n_broken, n_dup = set(), 0, 0
    for s in (_sections(cur) or [{"start": -1, "end": len(lines)}]):
        seen = set()
        for i in range(s["start"] + 1, s["end"]):
            m = _PIC.match(lines[i])
            if not m:
                continue
            p = images.resolve(m.group(1))
            if p is None:
                drop.add(i); n_broken += 1
            elif p.name in seen:
                drop.add(i); n_dup += 1
            else:
                seen.add(p.name)
    if n_broken:
        fixed.append(f"removed {n_broken} broken picture reference{'s' if n_broken != 1 else ''}")
    if n_dup:
        fixed.append(f"removed {n_dup} repeated picture{'s' if n_dup != 1 else ''}")
    cur = _clean_blank(_drop_lines(lines, drop))

    # 5. repeated paragraphs (later copies go)
    blocks = re.split(r"(\n\s*\n)", cur)
    seen_p: set[str] = set()
    out, n_par = [], 0
    for blk in blocks:
        t = _norm(blk)
        if len(t) >= 60 and not blk.lstrip().startswith(("#", "```", "|", "!")):
            if t in seen_p:
                n_par += 1
                if out and re.fullmatch(r"\n\s*\n", out[-1]):
                    out.pop()
                continue
            seen_p.add(t)
        out.append(blk)
    if n_par:
        fixed.append(f"removed {n_par} repeated paragraph{'s' if n_par != 1 else ''}")
    cur = _clean_blank("".join(out).split("\n"))

    # 6. sections left empty by the above
    for _ in range(2):
        lines = cur.split("\n")
        drop = set()
        heads = docs._headings(cur)
        for k, (i, lv, t) in enumerate(heads):
            if lv < 2:
                continue
            end = next((j for j, _l, _t in heads[k + 1:] if _l <= lv), len(lines))
            nxt = heads[k + 1] if k + 1 < len(heads) else None
            has_sub = nxt is not None and nxt[1] > lv and nxt[0] < end
            if not any(ln.strip() for ln in lines[i + 1:end]) and not has_sub:
                drop.add(i)
                fixed.append(f"removed the empty section “{t}”")
        if not drop:
            break
        cur = _clean_blank(_drop_lines(lines, drop))
    return cur, fixed


def summary(issues: list[dict]) -> str:
    if not issues:
        return "No problems found."
    return "\n".join(f"- {i['detail']}" + ("" if i["fixable"] else " (needs new content, not just cleaning)") for i in issues)
