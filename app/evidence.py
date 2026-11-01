"""Evidence for a search: read the top pages and keep the passages that answer the question.

A search result is a title, an address and a snippet of a couple of hundred characters, and a small model that has to decide for itself which
page to open, and then read the first few thousand characters of it, often answers from the snippet alone. `excerpts()` does the reading:
it opens the best pages at the same time (a few seconds in all), cuts each into passages, ranks the passages of all of them against the
question and returns the best one or two per page. The model gets "From the page: …" under each result, which is where the answer usually is.

Ranking is BM25 over the passages (a word that few passages contain counts for more), with a bonus for passages that hold the question's
words next to each other and, for a question about a number, for passages that contain a number, and a penalty for page furniture (cookie
notices, menus). Nothing here guesses at the meaning of the question: a passage is kept only if it contains enough of its words.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import Counter
from typing import Awaitable, Callable
from urllib.parse import urlparse

_STOP = frozenset("a an and are as at be been by can could did do does for from had has have how i in is it its me my of on or please should so than that the their there these this "
                  "those to was we were what when where which who whom why will with would you your tell give show explain about".split())
# a question that wants a figure, a date or a version
_NUMERIC = re.compile(r"\b(how (many|much|long|old|far|big|tall)|when|what year|which year|date|year|population|price|cost|version|release|released|latest|current|number of|percent|percentage|"
                      r"age|height|weight|size|score|rank|ranking|temperature|speed)\b", re.I)
_FURNITURE = re.compile(r"cookie|privacy policy|terms of (use|service)|subscribe|newsletter|sign (in|up)|log in|all rights reserved|advertis|accept all|javascript is|enable javascript|"
                        r"follow us|share this|skip to|read more|related (articles|posts)|click here", re.I)
_SKIP_EXTENSIONS = (".pdf", ".zip", ".gz", ".tar", ".exe", ".dmg", ".apk", ".mp4", ".mp3", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".docx", ".xlsx", ".pptx")

_BLOCKED: dict[str, float] = {}  # host -> when it refused a read; left alone for ten minutes
_BLOCK_SECONDS = 600


_PAGES: dict[str, tuple[float, tuple[str, str, str]]] = {}  # address -> (when, (title, text, final address)): pages read in the last few minutes, in memory only
_PAGE_SECONDS = 300
_PAGE_LIMIT = 40


def clear():
    """Forget everything remembered about pages: it follows the private-session switch, like the search cache."""
    _BLOCKED.clear()
    _PAGES.clear()


def cached_page(url: str):
    hit = _PAGES.get(url)
    return hit[1] if hit and time.time() - hit[0] < _PAGE_SECONDS else None


def remember_page(url: str, page: tuple[str, str, str]):
    if len(_PAGES) >= _PAGE_LIMIT:
        _PAGES.pop(min(_PAGES, key=lambda k: _PAGES[k][0]))
    _PAGES[url] = (time.time(), page)


def mark_blocked(host: str):
    _BLOCKED[host.lower()] = time.time()


def is_blocked(host: str) -> bool:
    t = _BLOCKED.get(host.lower())
    return bool(t and time.time() - t < _BLOCK_SECONDS)


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if (len(w) > 1 or w.isdigit()) and w not in _STOP]


def split_passages(text: str, size: int = 480) -> list[str]:
    """Cut a page's text into passages of about `size` characters, along paragraph and sentence boundaries."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n|\n", text):
        block = " ".join(block.split())
        if len(block) < 40:
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(\[])", block)
        cur = ""
        for s in sentences:
            while len(s) > size * 1.5:  # a very long run with no full stop
                cut = s.rfind(" ", 0, size)
                cut = cut if cut > 0 else size
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(s[:cut])
                s = s[cut:].lstrip()
            if cur and len(cur) + len(s) + 1 > size:
                out.append(cur)
                cur = s
            else:
                cur = f"{cur} {s}".strip()
        if cur:
            out.append(cur)
    return [p for p in out if len(p) >= 40]


def _looks_like_a_menu(passage: str) -> bool:
    """A table of contents or a row of links: capitalised words run together with no sentence in them."""
    words = passage.split()
    return len(words) >= 5 and not re.search(r"[.;,]", passage) and sum(1 for w in words if w[:1].isupper()) / len(words) > 0.6


def _score_passages(query: str, passages: list[str]) -> list[tuple[float, float]]:
    """(score, share of the question's words the passage contains) for each passage: BM25 over this set of passages plus the bonuses described above."""
    q = list(dict.fromkeys(tokens(query)))
    if not q or not passages:
        return [(0.0, 0.0)] * len(passages)
    docs = [tokens(p) for p in passages]
    n = len(docs)
    avg = (sum(len(d) for d in docs) / n) or 1.0
    df = Counter(w for d in docs for w in set(d))
    idf = {w: math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) for w in q}
    numeric = bool(_NUMERIC.search(query))
    pairs = [f"{a} {b}" for a, b in zip(q, q[1:])]
    out = []
    for p, d in zip(passages, docs):
        tf = Counter(d)
        s = sum(idf[w] * tf[w] * 2.5 / (tf[w] + 1.5 * (0.25 + 0.75 * len(d) / avg)) for w in q if tf[w])
        have = sum(1 for w in q if tf[w]) / len(q)
        low = " ".join(d)
        s += 0.4 * sum(1 for pr in pairs if pr in low)
        if numeric and re.search(r"\d", p):
            s *= 1.25
        if _FURNITURE.search(p) or _looks_like_a_menu(p):
            s *= 0.4
        out.append((s, have))
    return out


def best_passages(query: str, pages: dict[int, str], per_page: int = 2, size: int = 480) -> dict[int, list[str]]:
    """{page: its best passages in reading order}. The passages of all the pages are scored together, so a word found on every page counts for little."""
    flat: list[tuple[int, int, str]] = []
    for key, text in pages.items():
        for j, p in enumerate(split_passages(text, size)):
            flat.append((key, j, p))
    scores = _score_passages(query, [p for _, _, p in flat])
    need = 1.0 if len(set(tokens(query))) <= 2 else 0.34  # a short question must be matched whole, a long one by a third of its words (often "fix", "build"... are not in the answer)
    chosen: dict[int, list[tuple[float, int, str]]] = {}
    for (key, j, p), (s, have) in sorted(zip(flat, scores), key=lambda t: -t[1][0]):
        if s <= 0 or have < need:
            continue
        got = chosen.setdefault(key, [])
        if len(got) < per_page and all(abs(j - k) > 1 for _, k, _ in got):  # not two neighbours: they say the same thing
            got.append((s, j, p))
    return {k: [p for _, _, p in sorted(v, key=lambda t: t[1])] for k, v in chosen.items()}


def readable(url: str) -> bool:
    u = urlparse(url or "")
    return u.scheme in ("http", "https") and bool(u.hostname) and not u.path.lower().endswith(_SKIP_EXTENSIONS) and not is_blocked(u.hostname)


async def excerpts(query: str, rows: list[dict], load: Callable[[str], Awaitable[tuple[str, str, str]]], pages: int = 3, per_page: int = 2,
                   timeout: float = 7.0, size: int = 480) -> dict[int, list[str]]:
    """{index of a row: its best passages}. Reads the first `pages` readable rows at once and gives up on slow ones after `timeout` seconds in all.
    `load(url)` returns (title, text, final address) or raises; a page that refuses is remembered and not asked again for a while."""
    wanted = [i for i, r in enumerate(rows) if readable(r.get("href", ""))][:pages]
    if not wanted:
        return {}

    async def one(i: int):
        url = rows[i]["href"]
        try:
            return i, (await load(url))[1]
        except Exception as e:  # noqa: BLE001 - a page that cannot be read just gets no excerpt
            if any(w in str(e) for w in ("does not let programs", "is limiting requests")):
                mark_blocked(urlparse(url).hostname or "")
            return i, ""

    tasks = [asyncio.create_task(one(i)) for i in wanted]
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for t in pending:
        t.cancel()
    texts = {}
    for t in done:
        i, text = t.result()
        if text:
            texts[i] = text
    return await asyncio.to_thread(best_passages, query, texts, per_page, size) if texts else {}
