"""Whole-job document operations that a small model can ask for in one call.

A small model is unreliable at chaining many tool calls (it does the first change and reports the job done, or adds a
section in the wrong place). So the model calls one tool with an instruction, and the work happens here in structured steps:

- edit_document: the model is asked for a JSON list of operations that covers EVERY change requested (schema-constrained, so
  it is always well-formed); the operations are applied in order and checked against the result.
- illustrate_document: pictures are placed in sections by plan (not by the model's tool-call stamina), their prompts are
  written in one JSON call, and they are drawn one after another.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Awaitable, Callable

import docaudit
import documents as docs
import imagegen
import images

LlmJson = Callable[[list, dict, int], Awaitable[dict]]

CLASSIFY_SYSTEM = """You decide what ONE editing instruction does to a document. Reply with JSON:
- action "rewrite": change an EXISTING section (shorten, expand, translate, fix, reformat, reword, rename its heading, add a bullet to it...). "section" = that section.
- action "rewrite_all": the instruction applies to every section (for example "make it more formal", "translate everything"). Use "" for section.
- action "add": write a NEW section. "after" = the existing section it follows. Use "(end)" unless the instruction says top, start or beginning, then "(start)".
- action "remove": delete an existing section. "section" = that section.
Use only headings from the list. Fields that do not apply must be "" (for "after": "(end)")."""

REWRITE_SYSTEM = """You edit ONE section of a document. Apply the requested change and return ONLY the full revised section in Markdown, starting with its heading line (keep the heading unless the change is about renaming it). Keep everything the change does not concern. No explanations, no code fence around the answer."""

ADD_SYSTEM = """You write ONE new section for a document. Return ONLY the new section in Markdown, starting with a "## Heading" line. Match the document's language, tone and formatting (lists, tables). Do not repeat what other sections say. No explanations, no code fence around the answer."""


class AgentError(Exception):
    """Something the model or user can act on."""


def _classify_schema(headings: list[str]) -> dict:
    return {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["rewrite", "rewrite_all", "add", "remove"]},
        "section": {"type": "string", "enum": headings + [""]},
        "after": {"type": "string", "enum": headings + ["(end)", "(start)"]}},
        "required": ["action", "section", "after"]}


_VERBS = ("make", "add", "remove", "delete", "rewrite", "translate", "shorten", "expand", "change", "rename", "fix", "move", "replace", "insert", "update", "edit",
          "summarize", "summarise", "simplify", "reword", "convert", "combine", "split", "reorder", "include", "drop", "put", "write", "turn", "correct", "extend",
          "trim", "cut", "polish", "improve", "format", "capitalize", "swap", "merge", "also", "then")


def split_clauses(instruction: str) -> list[str]:
    """Split "do A and do B, then C" into separate instructions. A piece that does not start with a verb ("…Risks sections")
    belongs to the previous one, so "translate the Benefits and Risks sections" stays whole."""
    parts = [p.strip(" ,;.") for p in re.split(r"\s*(?:;|\.\s+|,?\s+and then\s+|,?\s+then\s+|,?\s+and\s+|,\s+also\s+|,\s+)", instruction.strip()) if p and p.strip(" ,;.")]
    out: list[str] = []
    for p in parts:
        first = re.sub(r"^(please|also)\s+", "", p, flags=re.I).split(" ", 1)[0].lower()
        if out and first not in _VERBS:
            out[-1] += " and " + p
        else:
            out.append(p)
    return out or [instruction.strip()]


def _editable_headings(source: str) -> list[str]:
    """Headings the planner may point at (exact texts). The document title (level 1) is left out when there are sections."""
    heads = docs._headings(source)
    keep = [t for _, lv, t in heads if lv >= 2] or [t for _, _lv, t in heads]
    out: list[str] = []
    for t in keep:
        if t not in out:
            out.append(t)
    return out


def _one_section(text: str) -> str:
    """A section rewrite must stay one section: cut at the next heading of the same or a higher level."""
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if re.match(r"^#{1,6}\s", ln)), None)
    if first is None:
        return text
    level = len(re.match(r"^#+", lines[first]).group(0))
    fenced = False
    for i in range(first + 1, len(lines)):
        if re.match(r"^\s*```", lines[i]):
            fenced = not fenced
        elif not fenced and (m := re.match(r"^(#{1,6})\s", lines[i])) and len(m.group(1)) <= level:
            return "\n".join(lines[:i]).strip()
    return text


_SMALL_THINGS = re.compile(r"\b(bullet|bullets|point|points|item|items|sentence|sentences|paragraph|row|line|word|phrase|note|example|quote|link|caption)\b", re.I)
_PICTURE_CLAUSE = re.compile(r"\b(images?|pictures?|photos?|illustrations?|graphics?)\b", re.I)
_PICTURE_VERB = re.compile(r"\b(generate|add|insert|include|create|make|put|draw|illustrate|give|attach|find|get|fetch|download|search|replace|swap|remove|delete|change|show|use)\b", re.I)
_NEW_SECTION = re.compile(r"\b(section|slide|chapter|part|heading|page)\b", re.I)


def _mentioned(clause: str, headings: list[str]) -> str | None:
    """The heading a clause names, if any (longest match first, case-insensitive)."""
    low = clause.lower()
    for h in sorted(headings, key=len, reverse=True):
        if h.lower() in low:
            return h
    return None


def _position(clause: str, headings: list[str]) -> str:
    """Where a new section goes, from the wording alone: 'after X', 'before X', the top, else the end."""
    m = re.search(r"\b(after|following|below)\s+(?:the\s+)?(.+?)(?:\s+(?:section|slide|part|chapter))?$", clause, re.I)
    if m and (h := _mentioned(m.group(2), headings)):
        return h
    m = re.search(r"\bbefore\s+(?:the\s+)?(.+?)(?:\s+(?:section|slide|part|chapter))?$", clause, re.I)
    if m and (h := _mentioned(m.group(1), headings)) and h in headings:
        k = headings.index(h)
        return headings[k - 1] if k > 0 else "(start)"
    if re.search(r"\b(at the )?(top|start|beginning)\b|\bfirst section\b|\bup front\b", clause, re.I):
        return "(start)"
    return "(end)"


def _clean_section(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^```(?:markdown|md)?[ \t]*\n([\s\S]*?)\n?```\s*$", t)
    if m:
        t = m.group(1).strip()
    t = re.sub(r"^(here is|here's|sure|certainly)[^\n]*\n+", "", t, flags=re.I)  # chatty lead-in
    # the model sometimes reports its own edit inside the text ("*Note: the document ... has been updated ...*"): that is not content
    t = "\n".join(ln for ln in t.split("\n") if ln.lstrip().startswith("#") or not docaudit.EDIT_NOTE.search(ln))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _outline_with_excerpts(source: str) -> str:
    lines = source.split("\n")
    heads = docs._headings(source)
    out = []
    for k, (i, lv, t) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = re.sub(r"\s+", " ", re.sub(r"[#>*_`|-]+", " ", " ".join(lines[i + 1:end]))).strip()
        out.append(f"{'  ' * (lv - 1)}- {t}" + (f": {body[:110]}…" if body else ""))
    return "\n".join(out) or source[:1500]


async def _text(llm_text: Callable, system: str, user: str, max_tokens: int) -> str:
    return _clean_section(await llm_text([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens))


_PIC_LINE_RE = re.compile(r"^[ \t]*!\[[^\]]*\]\(img:[^)]*\)[ \t]*$")
_FENCE_BLOCK = re.compile(r"^[ \t]*```mermaid[^\n]*\n.*?\n[ \t]*```[ \t]*$", re.S | re.M)
_RENAME = re.compile(r"\b(rename|retitle|re-title|title|heading|headline|call it|called)\b", re.I)
_MILD = re.compile(r"\b(a bit|a little|slightly|somewhat|a touch|marginally)\b", re.I)
_NUM_TITLE = re.compile(r"\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)\s+(\d{1,3})\s*[:.\-–—]\s*(.+?)(?=\s*(?:,|;|\band\b\s+[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?\s+\d{1,3}\s*[:.\-–—])|$)")


def _words(t: str) -> int:
    return len(re.findall(r"\w+", re.sub(r"```.*?```", " ", t, flags=re.S)))


def explicit_titles(clause: str) -> list[str]:
    """New sections a clause names exactly: "add Place 1: Anuradha and Place 5: Rameswaram" -> ["Place 1: Anuradha", "Place 5: Rameswaram"]."""
    out = []
    quoted = [q.strip() for q in re.findall(r"(?<!\w)['\"“‘]([^'\"”’\n]{3,80}?)['\"”’](?!\w)", clause) if _NUM_TITLE.match(q.strip())]
    if quoted:  # the model put the exact titles in quotes: take them as they are
        return [f"{m.group(1).strip()} {int(m.group(2))}: {m.group(3).strip()}" for q in quoted if (m := _NUM_TITLE.match(q))]
    clause = re.sub(r"(?<!\w)['\"“”‘’]|['\"“”‘’](?!\w)", " ", clause)  # quotes around titles are the model's, not part of them
    for m in _NUM_TITLE.finditer(clause):
        # The model that calls the tool paraphrases: cut at a quote or a filler phrase ("… with appropriate content").
        title = re.split(r"[\"“”]|\s+(?:with|containing|that|which|including|featuring|about|describing|covering|having|where|using|before|after|between|ahead|following|near|next|at the|in the|from|as|to)\b|\s+and\s+(?:an?|the)\b", m.group(3))[0]
        title = re.sub(r"[.\s'’]+$", "", title).strip(" '’")
        if title and len(title) <= 80:
            label = re.sub(r"^(?:Add|Insert|Include|Create|Write|Missing|New)\s+", "", m.group(1).strip())
            out.append(f"{label} {int(m.group(2))}: {title}")
    return out


def numbered_anchor(headings: list[str], title: str) -> str:
    """Where a numbered section ("Place 5: …") belongs among its siblings: after the nearest lower number, else before the nearest higher."""
    m = re.match(r"^\s*([A-Za-z][\w ]{1,18}?)\s+(\d{1,3})\b", title)
    if not m:
        return "(end)"
    word, n = m.group(1).strip().lower(), int(m.group(2))
    sib = []
    for h in headings:
        mm = re.match(r"^\s*([A-Za-z][\w ]{1,18}?)\s+(\d{1,3})\b", h)
        if mm and mm.group(1).strip().lower() == word:
            sib.append((int(mm.group(2)), h))
    if not sib:
        return "(end)"
    lower = [x for x in sib if x[0] < n]
    if lower:
        return max(lower)[1]
    higher = min(sib)[1]
    k = headings.index(higher)
    return headings[k - 1] if k > 0 else "(start)"


def _force_heading(text: str, title: str, level: int = 2) -> str:
    """The section's first heading line is exactly `title` at `level` (replacing whatever the model wrote, or adding one)."""
    lines = text.strip().split("\n")
    i = next((k for k, ln in enumerate(lines) if ln.strip()), None)
    head = f"{'#' * level} {title}"
    if i is not None and re.match(r"^#{1,6}\s", lines[i]):
        lines[i] = head
        return "\n".join(lines[i:]).strip()
    return head + "\n\n" + text.strip()


def _restore_assets(original: str, text: str) -> str:
    """A rewrite is about words: pictures and diagrams in the original section must survive it."""
    out = text
    pics = [ln.strip() for ln in original.split("\n") if _PIC_LINE_RE.match(ln)]
    missing = [p for p in pics if p not in out]
    if missing:
        lines = out.split("\n")
        at = next((k + 1 for k, ln in enumerate(lines) if re.match(r"^#{1,6}\s", ln)), 0)
        lines[at:at] = ["", *missing, ""]
        out = "\n".join(lines)
    for blk in _FENCE_BLOCK.findall(original) if False else [m.group(0) for m in _FENCE_BLOCK.finditer(original)]:
        if blk.strip() not in out:
            out = out.rstrip() + "\n\n" + blk.strip()
    return re.sub(r"\n{3,}", "\n\n", out).strip()


async def edit_document(name: str, instruction: str, llm: LlmJson, llm_text: Callable | None = None) -> dict:
    """Carry out `instruction` on the document, one clause at a time. Returns {summary, applied, failed, info}."""
    instruction = " ".join((instruction or "").split())
    if not instruction:
        raise AgentError("Say what to change in the document.")
    if llm_text is None:
        raise AgentError("The editor is not available.")
    cur = docs.get_source(name)
    source = new = cur["source"]
    applied: list[str] = []
    failed: list[str] = []
    deferred: list[str] = []  # picture requests: the picture tools do those
    last_target = ""

    async def rewrite(section: str, change: str):
        nonlocal new
        i, end, lv = docs._find_section(new, section, True)
        original = "\n".join(new.split("\n")[i:end]).strip()
        orig_heading = new.split("\n")[i]
        w = _words(original)
        shorter = bool(_SHORTER.search(change))
        floor = (0.6 if _MILD.search(change) else 0.35) if shorter else 0.25
        hint = ""
        if shorter and w > 40:
            hint = f"\nTarget length: about {int(w * (0.8 if _MILD.search(change) else 0.55))} words (it is {w} words now). Keep every fact that matters; do not delete whole paragraphs."
        text = _one_section(await _text(llm_text, REWRITE_SYSTEM, f"Section:\n---\n{original}\n---\n\nChange to make: {change}{hint}", 3000))
        if not text or text.strip() == original:  # handed back unchanged: one firmer try
            text = _one_section(await _text(llm_text, REWRITE_SYSTEM, f"Section:\n---\n{original}\n---\n\nYou MUST visibly change this section as follows: {change}{hint}", 3000))
        if text and w > 40 and _words(text) < floor * w:  # it cut far too much: ask once for a gentler version
            text = _one_section(await _text(llm_text, REWRITE_SYSTEM, f"Section:\n---\n{original}\n---\n\nChange to make: {change}\nYou removed too much last time. Keep at least {int(floor * 1.4 * w)} words and every key fact.", 3000)) or text
        if not text or text.strip() == original:
            failed.append(f"“{section}”: the model did not change it ({change})")
            return None
        if w > 40 and _words(text) < floor * w * 0.8:
            failed.append(f"“{section}”: the rewrite dropped most of the text, so it was not applied ({change})")
            return None
        if not _RENAME.search(change):  # the section keeps its own heading unless the request is about renaming
            text = _force_heading(text, re.sub(r"^#+\s*", "", orig_heading).strip(), lv)
        text = _restore_assets(original, text)
        new = docs.replace_section_text(new, section, text, first=True)
        applied.append(f"rewrote “{section}” ({change})")
        m = re.match(r"^#{1,6}\s+(.*)", text)
        return m.group(1).strip() if m else section

    async def add_section(clause: str, title: str | None, after: str):
        """Write one new section. With an exact title the heading is forced to it and numbered sections go in numeric order."""
        nonlocal new, last_target
        outline = _outline_with_excerpts(new)
        doc_title = _norm_t(next((t for _, lv2, t in docs._headings(new) if lv2 == 1), ""))
        existing = {_norm_t(t) for _, _lv, t in docs._headings(new)}
        ask = f"Document outline:\n{outline}\n\nWrite this new section: {clause}"
        if title:
            ask += f"\nIts heading must be exactly: ## {title}"
        text = _one_section(await _text(llm_text, ADD_SYSTEM, ask, 3000))
        head = re.match(r"^#{1,6}\s+(.*)", text or "")
        echoes = lambda h: _norm_t(h) in (existing | {doc_title}) or (doc_title and _norm_t(h).startswith(doc_title + " "))  # noqa: E731
        if text and not title and head and echoes(head.group(1)):  # it repeated an existing heading or the title: once more
            text = _one_section(await _text(llm_text, ADD_SYSTEM, ask + "\nThe heading must be NEW: not the document title and not an existing section.", 3000))
        if not text:
            failed.append(f"new section ({title or clause}): the model wrote nothing")
            return
        head = re.match(r"^#{1,6}\s+(.*)", text)
        if not title and head and echoes(head.group(1)):
            failed.append(f"new section ({clause}): the model only repeated an existing heading, so nothing was added")
            return
        if title:
            text = _force_heading(text, title)
            anchor = numbered_anchor(_editable_headings(new), title)
            after = anchor if anchor != "(end)" else after  # numbered sections go in numeric order, wherever the wording points
        new = docs.insert_section_start(new, text) if after == "(start)" else docs.add_section_text(new, text, None if after == "(end)" else after, first=True)
        head = re.match(r"^#{1,6}\s+(.*)", text)
        last_target = head.group(1).strip() if head else last_target
        applied.append(f"added “{last_target}”" + ("" if after == "(end)" else (" at the start" if after == "(start)" else f" after “{after}”")))

    for clause in split_clauses(instruction):
        if _PICTURE_CLAUSE.search(clause) and _PICTURE_VERB.search(clause) and not _NEW_SECTION.search(clause):
            deferred.append(clause)  # pictures are not text: the picture tools (which respect the sections named) do these
            continue
        headings = _editable_headings(new)
        user = (f"Sections: {json.dumps(headings, ensure_ascii=False)}\n"
                f"Document outline:\n{_outline_with_excerpts(new)}\n"
                + (f"The previous instruction concerned the section “{last_target}”.\n" if last_target else "")
                + f"\nInstruction: {clause}")
        try:
            titles = explicit_titles(clause) if re.search(r"\b(add|insert|include|write|create|missing)\b", clause, re.I) else []
            if titles:  # "add the missing sections Place 1: Anuradha and Place 5: Rameswaram": one section per named title
                for t in titles:
                    if _norm_t(t) in {_norm_t(h) for h in _editable_headings(new)}:
                        failed.append(f"“{t}” already exists")
                        continue
                    await add_section(clause, t, _position(clause, _editable_headings(new)))
                continue
            t = await llm([{"role": "system", "content": CLASSIFY_SYSTEM}, {"role": "user", "content": user}], _classify_schema(headings), 200)
            action, sec = t.get("action"), t.get("section", "")
            after = _position(clause, headings)  # decided from the wording: the 1.7B model put things in odd places
            if action == "add" and _SMALL_THINGS.search(clause) and not _NEW_SECTION.search(clause):
                # "add a bullet 'Better focus'" changes an existing section, it does not create one
                action, sec = "rewrite", (_mentioned(clause, headings) or last_target or (headings[0] if headings else ""))
            if action == "remove":
                new = docs.remove_section_text(new, sec, first=True)
                applied.append(f"removed “{sec}”")
            elif action == "rewrite":
                last_target = await rewrite(sec, clause) or last_target
            elif action == "rewrite_all":
                for h in headings:
                    last_target = await rewrite(h, clause) or last_target
            elif action == "add":
                await add_section(clause, None, after)
            else:
                failed.append(f"{clause}: not understood")
        except docs.DocError as e:
            failed.append(f"{clause}: {e}")
    if new == source:
        if deferred and not failed:
            raise AgentError("This request is about pictures, which edit_document does not do. Use illustrate_document (source web for real photos, replace true to swap pictures), remove_pictures_from_document, or add_picture_to_document.")
        raise AgentError("Nothing could be changed: " + ("; ".join(failed) or "no changes were produced") + ". Try describing the change more precisely.")
    info = await docs.update_source(name, new, by="model")
    return {"summary": "; ".join(applied), "applied": applied, "failed": failed, "deferred": deferred, "info": info}


def _norm_t(t: str) -> str:
    return re.sub(r"[\W_]+", " ", t.lower()).strip()


# ---------------------------------------------------------------------------------------------------- verification

JUDGE_SCHEMA = {"type": "object", "properties": {"done": {"type": "boolean"}, "why": {"type": "string"}}, "required": ["done", "why"]}
_SHORTER = re.compile(r"\b(shorter|shorten|trim|cut|reduce|condense|brief|concise|tighten)\b", re.I)
_LONGER = re.compile(r"\b(longer|expand|extend|lengthen|elaborate|more detail)\b", re.I)
_REMOVE = re.compile(r"\b(remove|delete|drop|get rid of)\b", re.I)
_ADD_SECTION = re.compile(r"\b(add|insert|include|write|create)\b.*\b(section|slide|chapter|summary|conclusion|introduction|overview)\b", re.I)


def _section_words(source: str, heading: str) -> int | None:
    try:
        i, end, _ = docs._find_section(source, heading)
    except docs.DocError:
        return None
    return len(re.findall(r"\w+", "\n".join(source.split("\n")[i + 1:end])))


async def clause_done(before: str, after: str, clause: str, llm: LlmJson) -> tuple[bool, str]:
    """Did the change between `before` and `after` carry out this one instruction? Measurable things are measured; the rest goes to a judge looking at the diff."""
    if before == after:
        return False, "the document did not change"
    hb, ha = [t for _, _l, t in docs._headings(before)], [t for _, _l, t in docs._headings(after)]
    named = _mentioned(clause, hb) or _mentioned(clause, ha)
    if _REMOVE.search(clause) and named and not _PICTURE_CLAUSE.search(clause):
        gone = named not in ha or ha.count(named) < hb.count(named)
        return gone, "the section is gone" if gone else f"“{named}” is still there"
    if _ADD_SECTION.search(clause) and not _SMALL_THINGS.search(clause):
        more = len(ha) > len(hb) or (set(ha) - set(hb))
        return bool(more), "a new section exists" if more else "no new section was added"
    if named and (_SHORTER.search(clause) or _LONGER.search(clause)):
        wb, wa = _section_words(before, named), _section_words(after, named)
        if wb is not None and wa is not None:
            ok = wa < wb if _SHORTER.search(clause) else wa > wb
            return ok, f"“{named}” went from {wb} to {wa} words"
    diff = "\n".join(list(difflib.unified_diff(before.split("\n"), after.split("\n"), "before", "after", n=1, lineterm=""))[:90])[:3500]
    try:
        out = await llm([{"role": "system", "content": "You check an edit. You get one instruction and the diff (before to after) of a document. Answer JSON {\"done\": true/false, \"why\": one short sentence}. done is true only if the diff clearly shows THIS instruction was carried out."},
                         {"role": "user", "content": f"Instruction: {clause}\n\nDiff:\n{diff}"}], JUDGE_SCHEMA, 120)
        return bool(out.get("done")), str(out.get("why", ""))[:160]
    except Exception:  # noqa: BLE001 - if the judge fails, assume it was not done: applying again is safer than claiming success
        return False, "could not be confirmed"


async def finish_edit(name: str, instruction: str, before: str, llm: LlmJson, llm_text: Callable) -> dict:
    """Check each part of `instruction` against what actually changed since `before`; carry out the parts that did not happen."""
    after = docs.get_source(name)["source"]
    clauses = [c for c in split_clauses(instruction) if not (_PICTURE_CLAUSE.search(c) and _PICTURE_VERB.search(c) and not _NEW_SECTION.search(c))]
    checked = []
    undone = []
    for c in clauses:
        ok, why = await clause_done(before, after, c, llm)
        checked.append({"clause": c, "done": ok, "why": why, "fixed": None})
        if not ok:
            undone.append(c)
    applied: list[str] = []
    failed: list[str] = []
    if undone:
        try:
            r = await edit_document(name, "; ".join(undone), llm, llm_text)
            applied, failed = r["applied"], r["failed"]
            for c in checked:
                if not c["done"]:
                    c["fixed"] = not failed
        except AgentError as e:
            failed = [str(e)]
            for c in checked:
                if not c["done"]:
                    c["fixed"] = False
    return {"checked": checked, "applied": applied, "failed": failed}


# ---------------------------------------------------------------------------------------------------- pictures

PROMPT_SCHEMA = {
    "type": "object",
    "properties": {
        "style": {"type": "string"},
        "pictures": {"type": "array", "maxItems": 30, "items": {"type": "object", "properties": {
            "index": {"type": "integer"}, "source": {"type": "string", "enum": ["web", "generate"]}, "query": {"type": "string"},
            "prompt": {"type": "string"}, "caption": {"type": "string"}}, "required": ["index", "source", "query", "prompt", "caption"]}},
    },
    "required": ["style", "pictures"],
}
PICTURE_SYSTEM = """You choose and describe the picture for each numbered section of a document. For EVERY number decide where the picture comes from:
- "web": a real photograph is better: a real named place, landmark, building, person, animal species, plant, object, map, flag, historic artefact or event. "query" = a short, precise web image search (the name plus the place or what to show, 3-8 words). "prompt" = "".
- "generate": an imagined, abstract or stylised picture is better: ideas, concepts, processes, moods, scenes that do not exist or need a consistent look. "prompt" = a vivid English description (25-45 words): subject, setting, composition, lighting. "query" = "".
Also give "caption" = a short caption (under 10 words). First choose ONE visual style for the generated pictures of the whole document (for example "clean flat vector illustration, soft green palette" or "photorealistic, natural light") and put it in "style". No text, letters, numbers or logos in generated pictures."""

progress: dict = {"busy": False}


def unused_pictures(source: str, count: int) -> list[str]:
    """Names of the newest pictures that the document does not use yet (oldest of them first, so they keep their order)."""
    used = {p.name for m in re.finditer(r"\]\((img:[^)]*)\)", source) if (p := images.resolve(m.group(1)))}
    fresh = [i for i in images.list_images() if i["kind"] == "image" and i["name"] not in used][:max(1, count)]
    return [i["name"] for i in reversed(fresh)]


async def attach_pictures(name: str, names: list[str], section: str | None = None, count: int = 0, only_section: str | None = None) -> dict:
    """Put existing pictures into a document. With a section they all go there; otherwise one per section, sections without a picture first.
    With no names, the `count` newest pictures the document does not use yet."""
    cur = docs.get_source(name)
    kind, source = cur["kind"], cur["source"]
    if kind == "sheet":
        raise AgentError("Spreadsheets cannot hold pictures.")
    paths = []
    for n in names:
        p = images.resolve(n)
        if p is None:
            raise AgentError(f"There is no picture called {n!r}. Use list_images to see the names.")
        paths.append(p)
    if not paths:
        paths = [images.image_path(n) for n in unused_pictures(source, count or 1)]
        if not paths:
            raise AgentError("There are no pictures that this document does not already use.")
    new, added = source, []
    if not section and only_section:
        section = only_section  # the request named one section: that is where it goes
    if section:
        try:
            docs._find_section(source, section, True)
        except docs.DocError:
            section = None  # a section that does not exist (the model guessed): place them automatically instead of failing
    if section:
        for k, p in enumerate(paths):
            new = docs.insert_image_text(new, section, p.name, _credited(section, images.info(p)), "start" if k == 0 else "end", first=True)
            added.append({"name": p.name, "section": section})
    else:
        slots = _plan_slots(source, kind, len(paths))
        items = [{"start": sl["start"], "end": sl["end"], "where": sl["where"], "name": p.name, "caption": _credited(sl["heading"] or cur["title"], images.info(p))} for p, sl in zip(paths, slots)]
        new = docs.insert_image_lines(source, items)
        added = [{"name": p.name, "section": sl["heading"]} for p, sl in zip(paths, slots)]
        if len(added) < len(paths):
            raise AgentError(f"Only {len(added)} of {len(paths)} pictures fit: the document has too few sections.") if not added else None
    if new == source:
        raise AgentError("Nothing was placed.")
    info = await docs.update_source(name, new, by="model")
    return {"added": added, "skipped": len(paths) - len(added), "info": info}


_BOILER = re.compile(r"^\s*(introduction|overview|conclusion|summary|abstract|references?|sources?|bibliography|appendix|next steps|table of contents|contents|acknowledg\w*)\b", re.I)


def uncovered_sections(source: str, kind: str = "document", only_starts: set[int] | None = None) -> list[str]:
    """Content sections (or slides) that have no picture yet. Introduction/summary/references style sections only count when nothing else is left."""
    lines = source.split("\n")
    heads = _level2(source, only_starts)
    out, boiler = [], []
    for i, end, t in heads:
        if re.search(r"!\[[^\]]*\]\(img:", "\n".join(lines[i + 1:end])):
            continue
        (boiler if _BOILER.match(t) else out).append(t)
    return out or boiler


def _level2(source: str, only_starts: set[int] | None = None) -> list[tuple[int, int, str]]:
    """(start line, end line, title) of the level-2 sections. A section ends at the next heading of ANY named-or-not section, so a
    scope (only some sections) never stretches one section over its neighbours."""
    n = len(source.split("\n"))
    allh = [(i, lv, t) for i, lv, t in docs._headings(source)]
    out = []
    for k, (i, lv, t) in enumerate(allh):
        if lv != 2 or (only_starts is not None and i not in only_starts):
            continue
        end = next((j for j, l2, _t in allh[k + 1:] if l2 <= 2), n)
        out.append((i, end, t))
    return out


def _starts_for(source: str, only: list[str] | None) -> set[int] | None:
    """Line numbers of the level-2 sections called in `only` (None = the whole document)."""
    if not only:
        return None
    want = {_n(t) for t in only}
    return {i for i, lv, t in docs._headings(source) if lv == 2 and _n(t) in want} or None


def _n(t: str) -> str:
    return re.sub(r"[\W_]+", " ", t.lower()).strip()


def _plan_slots(source: str, kind: str, count: int, only_starts: set[int] | None = None) -> list[dict]:
    """Where `count` pictures go: sections without a picture first, then spread over all sections (max 3 each; 1 per slide).
    Sections are identified by their LINE, never by heading text: documents often repeat a heading."""
    lines = source.split("\n")
    heads = _level2(source, only_starts)
    if not heads:
        return [] if only_starts else [{"heading": None, "start": -1, "end": 0, "where": "start", "excerpt": source[:400]}]
    spans = []
    for i, end, t in heads:
        body = "\n".join(lines[i + 1:end])
        spans.append({"heading": t, "start": i, "end": end, "body": body, "have": len(re.findall(r"!\[[^\]]*\]\(img:", body))})
    # One picture per section unless MORE pictures than sections were asked for (then spread evenly). Repeating the call must not pile up.
    cap = 1 if kind == "slides" else max(1, -(-count // max(1, len(spans))))
    slots: list[dict] = []
    given = {s["start"]: s["have"] for s in spans}
    order = ([s for s in spans if s["have"] == 0 and not _BOILER.match(s["heading"])] + [s for s in spans if s["have"] == 0 and _BOILER.match(s["heading"])]
             + [s for s in spans if s["have"] > 0])
    while len(slots) < count:
        progressed = False
        for s in order:
            if len(slots) >= count:
                break
            if given[s["start"]] < cap:
                where = "start" if given[s["start"]] == 0 else "end"
                slots.append({"heading": s["heading"], "start": s["start"], "end": s["end"], "where": where, "excerpt": re.sub(r"[#>*_`|-]+", " ", s["body"]).strip()[:420]})
                given[s["start"]] += 1
                progressed = True
        if not progressed:
            break
    return slots


def _credited(caption: str, info: dict) -> str:
    """Captions of pictures from the web name where they came from."""
    return f"{caption} (source: {info['credit']})" if info.get("origin") == "web" and info.get("credit") else caption


async def illustrate_document(name: str, count: int | None, llm: LlmJson, gen: "imagegen.ImageGen", source_mode: str = "auto", replace: bool = False, only: list[str] | None = None) -> dict:
    """Find or draw `count` pictures for a document and place them in its sections. `source_mode`: "auto" lets the model choose per
    section between a real picture from the web and a generated one, "web" or "generate" forces one. Returns {requested, added, failed, info}."""
    import webimages

    cur = docs.get_source(name)
    kind, source = cur["kind"], cur["source"]
    if kind == "sheet":
        raise AgentError("Spreadsheets cannot hold pictures.")
    removed: list[str] = []
    scope = _starts_for(source, only)  # the sections the request named (None = the whole document)
    if replace:  # the old pictures go first (only in the named sections, if any); the new ones are then placed
        if scope:
            for t in only or []:
                try:
                    source, gone = docs.remove_pictures_text(source, t)
                    removed += gone
                except docs.DocError:
                    pass
            scope = _starts_for(source, only)
        else:
            source, removed = docs.remove_pictures_text(source)
    # No number given means the whole job: a picture for every content section that has none yet (within the named sections, if any).
    count = max(1, min(int(count) if count else len(uncovered_sections(source, kind, scope)), 30))
    if scope:  # the request named sections: each of them gets its picture, whatever number the model passed
        count = max(1, min(len(uncovered_sections(source, kind, scope)) or count, 30))
    source_mode = source_mode if source_mode in ("web", "generate") else "auto"
    can_gen = gen.available()
    if source_mode == "generate" and not can_gen:
        raise AgentError("Image generation is not set up: missing " + ", ".join(gen.missing()) + ".")
    slots = _plan_slots(source, kind, count, scope)
    if not slots:  # every section already has its picture: say so instead of adding more
        return {"requested": count, "added": [], "failed": [], "removed": removed, "note": "every section already has a picture", "info": docs._info(docs.doc_path(name))}
    progress.update({"busy": True, "doc": name, "done": 0, "total": len(slots), "stage": "Choosing pictures…"})
    try:
        listing = "\n".join(f"{i + 1}. {s['heading'] or 'Whole document'}: {s['excerpt']}" for i, s in enumerate(slots))
        style, picks = "", {}
        try:
            out = await llm([{"role": "system", "content": PICTURE_SYSTEM},
                             {"role": "user", "content": f"Document title: {cur['title']}\n\nSections needing a picture:\n{listing}"}], PROMPT_SCHEMA, 3500)
            style = out.get("style", "").strip()
            picks = {int(p["index"]): p for p in out.get("pictures", []) if isinstance(p, dict) and str(p.get("index", "")).lstrip("-").isdigit()}
        except Exception:  # noqa: BLE001 - fall back to choices built from the section text below
            pass
        plan = []
        for i, sl in enumerate(slots):
            p = picks.get(i + 1) or {}
            heading = sl["heading"] or cur["title"]
            choice = source_mode if source_mode != "auto" else (p.get("source") or ("generate" if can_gen else "web"))
            if choice == "generate" and not can_gen:
                choice = "web"
            plan.append({"slot": sl, "choice": choice, "caption": (p.get("caption") or heading).strip(),
                         "query": (p.get("query") or f"{heading} {cur['title']}").strip(),
                         "prompt": (p.get("prompt") or f"An illustration for “{heading}”: {sl['excerpt'][:160]}").strip()})
        results: dict[int, dict] = {}
        failed: list[str] = []
        done = 0
        # Pictures from the web first (no GPU needed), then the drawn ones, so the language model is swapped out only once.
        for i, it in enumerate(plan):
            if it["choice"] != "web":
                continue
            progress.update({"done": done, "stage": f"Finding a picture for “{it['slot']['heading'] or cur['title']}”…"})
            try:
                saved, problems = await webimages.find(it["query"], 1)
            except webimages.WebImageError as e:
                saved, problems = [], [str(e)]
            if saved:
                results[i] = saved[0]
                done += 1
            elif can_gen and source_mode != "web":
                it["choice"] = "generate"  # nothing usable on the web: draw it instead
            else:
                failed.append(f"“{it['slot']['heading'] or 'document'}”: no picture found on the web ({problems[0] if problems else 'no results'})")
        misses = 0
        for i, it in enumerate(plan):
            if it["choice"] != "generate" or i in results:
                continue
            progress.update({"done": done, "stage": f"Drawing picture {done + 1} of {len(plan)}…"})
            prompt = it["prompt"] if not style or style.lower() in it["prompt"].lower() else f"{it['prompt']}, {style}"
            try:
                results[i] = await gen.generate(prompt, "landscape")
                done += 1
                misses = 0
            except imagegen.ImageError as e:
                if str(e) == "Cancelled.":
                    failed.append("cancelled by you")
                    break
                failed.append(f"picture {i + 1}: {e}")
                misses += 1
                if misses >= 2:
                    failed.append("stopped after two failures in a row")
                    break
        added: list[dict] = []
        items = []
        for i in sorted(results):
            it, img = plan[i], results[i]
            cap = _credited(it["caption"], img)
            items.append({"start": it["slot"]["start"], "end": it["slot"]["end"], "where": it["slot"]["where"], "name": img["name"], "caption": cap})
            added.append({"name": img["name"], "section": it["slot"]["heading"], "caption": cap, "source": "web" if img.get("origin") == "web" else "generated"})
        if not added:
            raise AgentError("No pictures could be made: " + ("; ".join(failed) or "unknown error"))
        new = docs.insert_image_lines(source, items)
        info = await docs.update_source(name, new, by="model")
        return {"requested": count, "added": added, "failed": failed, "removed": removed, "info": info}
    finally:
        progress.clear()
        progress["busy"] = False


async def remove_pictures(name: str, section: str | None = None, image: str | None = None, only: list[str] | None = None) -> dict:
    """Take pictures out of a document: all, those in one section, or one named picture. `only` (the sections the request named) narrows
    an otherwise whole-document removal."""
    cur = docs.get_source(name)
    if not section and not image and only:
        new, removed = cur["source"], []
        for t in only:
            try:
                new, gone = docs.remove_pictures_text(new, t)
                removed += gone
            except docs.DocError:
                pass
    else:
        new, removed = docs.remove_pictures_text(cur["source"], section, image)
    if not removed:
        raise AgentError("There was no picture to remove" + (f" in “{section}”" if section else "") + ".")
    info = await docs.update_source(name, new, by="model")
    return {"removed": removed, "info": info}
