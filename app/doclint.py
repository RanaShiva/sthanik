"""What a small model leaves in the text of a document that the reader should never see.

Found in a real study guide: "This section produces a PDF that covers…", a section called "PDF Production" explaining that the text is written
in Markdown so it can be copied into a word processor, "(No specific site names are required.)", "(Avoid naming a particular forum.)", "without
specifying exact values", a heading with nothing under it, and a Sources list of bare addresses, one of them cut at its closing bracket. All of
it is the model echoing the instructions it was given, or the plumbing of the job, into the finished text.

Deterministic on purpose, like docaudit.py: it removes only what matches a pattern written for exactly this, and never rewrites what is left.
`topic` (the title and the request) switches off the rules about PDFs and Markdown when the document really is about them.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_FENCE = re.compile(r"(```[\s\S]*?(?:```|$))")
# a sentence about producing the document itself
_PRODUCING = re.compile(
    r"\b(?:this|the|each) (?:section|document|text|part|guide|chapter) (?:will |is meant to |aims to |is designed to |should )?(?:produces?|generates?|creates?|writes?|builds?|makes?|outputs?|renders?) "
    r"(?:a |an |the )?(?:finished |final |concrete )?(?:pdf|document|file|docx|word file|report)\b"
    r"|\b(?:goal|aim|purpose) (?:is|here is) to (?:give|provide|hand) (?:the )?(?:learners?|readers?|students?|you) (?:with )?an? (?:concrete )?(?:pdf|document)\b"
    r"|\b(?:written|formatted|saved) (?:in|as) (?:standard |plain )?markdown\b[^.]*\b(?:copied|pasted|word processor|latex|generate)\b"
    r"|\bcopied (?:directly )?(?:into|to) (?:any )?(?:word processor|document|latex)\b"
    r"|\b(?:each|every) (?:paragraph|section|bullet|item) (?:stays|is kept|should stay|must stay|remains) (?:within|under|below) (?:\w+ )?(?:sentences?|words?|lines?)\b"
    r"|\bno external (?:figures|images|pictures)\b[^.]*\b(?:required|needed)\b"
    r"|\b(?:includes?|contains?) the essential (?:mathematical )?(?:expressions?|formulas?) above\b", re.I)
# the checking step's own wording: a figure it could not match to a source was cut, and it says so in the text
_PROCESS = re.compile(
    r"\b(?:is|are|was|were) (?:omitted|left out|removed|not included) (?:as|because|since) (?:it|they|this) (?:is|are|was|were) not (?:sourced|in the sources|verified|supported)\b"
    r"|\bnot sourced\b|\bunverified specifics\b|\bcannot be verified from the (?:given|provided|available|supplied) (?:information|sources|text)\b"
    r"|\bthe sources? (?:did|do) not (?:give|contain|state|provide|mention|support) (?:this|an? exact|the exact)\b", re.I)
_ABOUT_FILES = re.compile(r"\b(?:pdf|docx|word processor|markdown|latex|file format)s?\b", re.I)
# "(No specific site names are required.)", "(Avoid naming a particular forum.)": an instruction that slipped into a parenthesis
_ASIDE = re.compile(r"\s*\(\s*(?:no|avoid|do not|don't|never|please|note:|keep|use only|without|there is no need)\b[^()]{0,140}\)", re.I)
_PHRASE = re.compile(r"\s+without (?:specifying|naming|giving|stating|mentioning) (?:any )?(?:exact|specific|particular) [a-z ]{3,40}?(?=[;,.)]|$)", re.I)
# a section that only describes how the file was made
_META_HEADING = re.compile(r"^\s*(?:pdf|document|file|output|docx|word)\s+(?:production|generation|creation|export|format|output)\s*$|^\s*(?:how|about)\s+this\s+(?:document|pdf|file|guide)\s+(?:was\s+)?(?:made|produced|generated|created)?\s*$", re.I)
_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# A title that names the job instead of the subject. Only these shapes: "Create a PDF about X", "Document Outline for PDF on X",
# "Outline of a document on X". "Report on Q3 Sales" and "Plan for a Weekend in Lisbon" are real titles and stay as they are.
_TITLE_ASK = re.compile(r"^\s*(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:create|make|write|generate|prepare|build|produce)\s+(?:me\s+)?(?:(?:an?|the)\s+)?(?:(?:pdf|document|doc|report|file|word file)\s+)?(?:(?:about|on|of|for|covering)\s+)?", re.I)
_TITLE_META = re.compile(r"^\s*(?:(?:document|doc|pdf|word|file)\s+(?:outline|plan|draft|structure|template|skeleton)|(?:outline|plan|draft|structure|template|skeleton)\s+(?:for|of)\s+(?:an?\s+|the\s+)?(?:pdf|document|doc|file|word file))\s*(?:for|of|on|about)?\s*", re.I)
_TITLE_FORMAT = re.compile(r"^\s*(?:(?:an?|the)\s+)?(?:pdf|document|doc|word file|file)\s+(?:on|about|of|for|covering)\s+", re.I)


def _outside_code(text: str, fn) -> str:
    parts = _FENCE.split(text)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


def _sentences(par: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(\[*_])", par)


def strip_echo(text: str, topic: str = "") -> str:
    """Remove sentences and parentheses that talk about the instructions or about producing the file."""
    about_files = bool(_ABOUT_FILES.search(topic or ""))

    def clean(seg: str) -> str:
        out = []
        for ln in seg.split("\n"):
            if not ln.strip() or _HEAD.match(ln) or re.match(r"^\s*(?:[-*+]|\d+[.)])\s*$", ln):
                out.append(ln)
                continue
            if ln.lstrip().startswith(("|", "![")):  # table rows and pictures
                out.append(ln)
                continue
            lead = re.match(r"^(\s*(?:[-*+]\s+|\d+[.)]\s+)?)", ln)[0]
            body = ln[len(lead):]
            changed = _PHRASE.sub("", _ASIDE.sub("", body))
            changed = re.sub(r"\s*\([^()]{0,160}\)", lambda m: "" if _PROCESS.search(m.group(0)) else m.group(0), changed)  # "(… is omitted as it is not sourced.)"
            sentences = _sentences(changed)
            kept = [s for s in sentences if (about_files or not _PRODUCING.search(s)) and not _PROCESS.search(s)]
            if changed == body and len(kept) == len(sentences):
                out.append(ln)  # untouched, including the two trailing spaces that mean a line break in Markdown
                continue
            new = " ".join(kept).strip()
            if body.strip() and not new:
                continue  # the whole line was such a sentence
            out.append(lead + new)
        return "\n".join(out)

    return _outside_code(text, clean)


def drop_meta_sections(text: str, topic: str = "") -> str:
    """Remove a section whose only subject is how the file was made ("PDF Production")."""
    if _ABOUT_FILES.search(topic or ""):
        return text
    lines = text.split("\n")
    out, skip_level, in_code = [], 0, False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEAD.match(ln)
        if m:
            level = len(m.group(1))
            if skip_level and level <= skip_level:
                skip_level = 0
            if not skip_level and _META_HEADING.match(m.group(2)):
                skip_level = level
                continue
        if not skip_level:
            out.append(ln)
    return "\n".join(out)


def drop_empty_headings(text: str) -> str:
    """Remove a heading with nothing under it (no text and no deeper heading) before the next heading of its own level or higher, or the end."""
    lines = text.split("\n")
    heads = [(i, len(m.group(1))) for i, ln in enumerate(lines) if (m := _HEAD.match(ln)) and not ln.startswith("# ")]
    drop: set[int] = set()
    in_code = False
    fenced = set()
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_code = not in_code
        if in_code:
            fenced.add(i)
    for k, (i, level) in enumerate(heads):
        if i in fenced:
            continue
        end = next((j for j, lv in heads[k + 1:] if lv <= level), len(lines))
        body = [x for x in lines[i + 1:end] if x.strip() and not _HEAD.match(x)]
        deeper = any(i < j < end for j, lv in heads if lv > level)
        if not body and not deeper and not any(ln.strip().startswith(("![", "```", "|")) for ln in lines[i + 1:end]):
            drop.add(i)
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


_BOLD_TITLE = re.compile(r"^\s*\*\*[^*\n]{2,80}\*\*\s*:?\s*$")


def drop_empty_bold_titles(text: str) -> str:
    """A line that is only **bold words**, used as a heading, with another heading or the end right after it, is removed."""
    lines = text.split("\n")
    drop, in_code = set(), False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_code = not in_code
        if in_code or not _BOLD_TITLE.match(ln):
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip()), None)
        if nxt is None or _HEAD.match(nxt) or _BOLD_TITLE.match(nxt):
            drop.add(i)
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


def _label(url: str) -> str:
    u = urlparse(url)
    host = (u.hostname or "").removeprefix("www.")
    last = unquote(u.path.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ").replace("-", " ").strip()
    if host.endswith("wikipedia.org") and last:
        return f"{last[:1].upper()}{last[1:]} (Wikipedia)"
    if last and not re.fullmatch(r"[\d.]+|index\.\w+", last):
        return f"{last[:1].upper()}{last[1:60]} ({host})"
    return host or url


def link_sources(text: str) -> str:
    """In a Sources section, a bare address becomes a link with a readable name; a bracket in an address is encoded so it cannot end the link early."""
    def fix(m):
        url = m.group(2).strip()
        if not re.match(r"https?://\S+$", url):
            return m.group(0)
        safe = url.replace("(", "%28").replace(")", "%29")
        return f"{m.group(1)}[{_label(url).replace('[', '(').replace(']', ')')}]({safe})"

    out, in_sources = [], False
    for ln in text.split("\n"):
        h = _HEAD.match(ln)
        if h:
            in_sources = h.group(2).strip().lower() in ("sources", "references", "source list")
        out.append(re.sub(r"^(\s*[-*+]\s+)(\S+)\s*$", fix, ln) if in_sources else ln)
    return "\n".join(out)


def tidy(text: str, topic: str = "") -> str:
    """All of the above, in order, with a blank-line clean-up at the end."""
    text = strip_echo(text, topic)
    text = drop_meta_sections(text, topic)
    text = drop_empty_headings(text)
    text = drop_empty_bold_titles(text)
    text = link_sources(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def clean_title(title: str) -> str:
    """The subject of a document, without the job words in front of it: "Document Outline for PDF on ML Topics" becomes "ML Topics"."""
    t = " ".join((title or "").split())
    cut = t
    if t.endswith("?"):
        return t  # a question is not a title that names its job
    if _TITLE_ASK.match(t):
        cut = _TITLE_ASK.sub("", t, count=1)
    elif _TITLE_META.match(t):
        cut = _TITLE_FORMAT.sub("", _TITLE_META.sub("", t, count=1), count=1)
    cut = cut.strip(" :-–—")
    if len(cut) < 5 or len(cut.split()) < 2:
        return t
    return cut[:1].upper() + cut[1:]
