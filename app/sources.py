"""Search sources that exist for programs to use, and the record of which sources are working.

Bing and Yahoo are read as web pages, which their owners may change or block at any time. The sources here have public interfaces made for
programs (Wikipedia, Stack Exchange, Marginalia) or are an address the user chose (their own SearXNG), so they keep answering when a scraped
engine does not. Each is asked politely: a short timeout, an identifying User-Agent and no more than a few results.

`HEALTH` remembers, per source, whether it answered. A source that fails twice in a row is left alone for a while (30 seconds, doubling up
to ten minutes) and then tried once more, so one blocked engine costs nothing instead of slowing every search. A scraped engine that keeps
answering with unrelated pages is moved to the back of the line the same way.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from urllib.parse import quote, urlparse

import httpx

import outbound
import paths
from version import VERSION

UA = f"Sthanik/{VERSION} (a local desktop app; https://github.com/RanaShiva/sthanik)"

# How much a source's ranking counts when answers are merged. The general engines are the baseline.
WEIGHT = {"yahoo": 1.0, "bing": 1.0, "searxng": 1.0, "stackexchange": 0.9, "wikipedia": 0.8, "marginalia": 0.6}


# ---------------------------------------------------------------- which sources work
class Health:
    OPEN_AFTER = 2  # consecutive failures before a source is left alone
    COOLDOWN = 30.0
    MAX_COOLDOWN = 600.0
    JUNK_AFTER = 4  # consecutive unrelated answers before a source is moved to the back
    JUNK_FOR = 300.0

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.state: dict[str, dict] = {}

    def _s(self, name: str) -> dict:
        return self.state.setdefault(name, {"fails": 0, "until": 0.0, "junk": 0, "junk_at": 0.0, "ok": 0, "last_error": "", "last_ms": None, "total_fail": 0})

    def allowed(self, name: str) -> bool:
        with self.lock:
            return self._s(name)["until"] <= self.clock()

    def demoted(self, name: str) -> bool:
        with self.lock:
            s = self._s(name)
            return s["junk"] >= self.JUNK_AFTER and self.clock() - s["junk_at"] < self.JUNK_FOR

    def success(self, name: str, seconds: float):
        with self.lock:
            s = self._s(name)
            s.update(fails=0, until=0.0, junk=0, last_error="", last_ms=int(seconds * 1000))
            s["ok"] += 1

    def junk(self, name: str, seconds: float):
        """It answered, but with pages unrelated to the query: the connection is fine, the answer was not."""
        with self.lock:
            s = self._s(name)
            s.update(fails=0, until=0.0, last_ms=int(seconds * 1000))
            s["junk"] += 1
            s["junk_at"] = self.clock()

    def failure(self, name: str, why: str):
        with self.lock:
            s = self._s(name)
            s["fails"] += 1
            s["total_fail"] += 1
            s["last_error"] = why[:120]
            if s["fails"] >= self.OPEN_AFTER:
                s["until"] = self.clock() + min(self.COOLDOWN * 2 ** (s["fails"] - self.OPEN_AFTER), self.MAX_COOLDOWN)

    def order(self, names: list[str]) -> list[str]:
        """Working sources first, those answering with junk last; unavailable ones are left out."""
        usable = [n for n in names if self.allowed(n)]
        return sorted(usable, key=lambda n: (self.demoted(n), self._s(n)["fails"]))

    def snapshot(self) -> dict:
        now = self.clock()
        with self.lock:
            return {n: {"working": s["until"] <= now, "retry_in": max(0, int(s["until"] - now)), "failures_in_a_row": s["fails"], "unrelated_in_a_row": s["junk"],
                        "answered": s["ok"], "last_error": s["last_error"], "last_ms": s["last_ms"]} for n, s in sorted(self.state.items())}

    def reset(self):
        with self.lock:
            self.state.clear()


HEALTH = Health()


# ---------------------------------------------------------------- the sources
def _get(url: str, params: dict | None = None, timeout: float = 8) -> httpx.Response:
    r = httpx.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout, follow_redirects=True, proxy=outbound.proxy())
    r.raise_for_status()
    return r


def _text(fragment: str) -> str:
    """HTML in a snippet to plain words."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def wikipedia(query: str, n: int) -> list[dict]:
    pages = _get("https://en.wikipedia.org/w/rest.php/v1/search/page", {"q": query, "limit": max(1, min(n, 5))}).json().get("pages", [])
    out = []
    for p in pages:
        key = p.get("key") or p.get("title", "").replace(" ", "_")
        if not key:
            continue
        body = ". ".join(x for x in (_text(p.get("description") or ""), _text(p.get("excerpt") or "")) if x)
        out.append({"title": f"{p.get('title', key)} - Wikipedia", "href": f"https://en.wikipedia.org/wiki/{quote(key, safe=':_()')}", "body": body})
    return out


_WORDS = frozenset("how to fix the a an and or of in on for with without when why what which is are was be can do does did get got into from by it its this that my your i me you we they them".split())


def _key_terms(query: str, count: int = 4) -> str:
    """The few most telling words of a question, in their order: Stack Overflow's search wants every word to occur in a question's text."""
    words = [w for w in re.findall(r"[A-Za-z0-9_.+#-]+", query) if w.lower() not in _WORDS and len(w) > 2]
    keep = set(sorted(range(len(words)), key=lambda i: -len(words[i]))[:count])
    return " ".join(w for i, w in enumerate(words) if i in keep)


def stackexchange(query: str, n: int) -> list[dict]:
    def ask(q: str) -> list[dict]:
        return _get("https://api.stackexchange.com/2.3/search/advanced", {"q": q, "site": "stackoverflow", "pagesize": max(1, min(n, 5)), "order": "desc", "sort": "relevance"}).json().get("items", [])

    items = ask(query)
    if not items and _key_terms(query) and _key_terms(query) != query:
        items = ask(_key_terms(query))  # a long question often matches nothing word for word
    out = []
    for it in items:
        answers = it.get("answer_count", 0)
        note = ("accepted answer, " if it.get("accepted_answer_id") else "") + f"{answers} answer{'s' if answers != 1 else ''}, score {it.get('score', 0)}"
        tags = ", ".join(it.get("tags", [])[:5])
        out.append({"title": f"{_text(it.get('title', ''))} - Stack Overflow", "href": it.get("link", ""), "body": f"{note}. Tags: {tags}"})
    return [r for r in out if r["href"]]


def marginalia(query: str, n: int) -> list[dict]:
    """An independent index of the small web (articles, blogs, documentation): useful when the big engines return nothing."""
    results = _get(f"https://api.marginalia.nu/public/search/{quote(query, safe='')}", {"count": max(1, min(n, 8))}, timeout=5).json().get("results", [])
    return [{"title": _text(r.get("title", "")), "href": r.get("url", ""), "body": _text(r.get("description", ""))} for r in results if r.get("url")]


def _setting(name: str):
    try:
        return json.loads((paths.HOME / "config" / "search.json").read_text()).get(name)
    except (OSError, ValueError, AttributeError):
        return None


def extras_enabled() -> bool:
    """Wikipedia, Stack Overflow and Marginalia are asked alongside Bing and Yahoo unless the user turned them off:
    LFM3_SEARCH_EXTRAS=0, or {"extras": false} in config/search.json. Then a query goes to Bing and Yahoo only (and to the user's own SearXNG)."""
    env = (os.environ.get("LFM3_SEARCH_EXTRAS") or "").strip().lower()
    if env:
        return env not in ("0", "false", "no", "off")
    return _setting("extras") is not False


def reading_enabled() -> bool:
    """A web search opens its top pages itself (evidence.py) unless the user turned that off: LFM3_SEARCH_READ=0, or {"read_pages": false} in
    config/search.json. Then a search returns snippets only, as before, and a page is opened only when the model asks for it."""
    env = (os.environ.get("LFM3_SEARCH_READ") or "").strip().lower()
    if env:
        return env not in ("0", "false", "no", "off")
    return _setting("read_pages") is not False


def searxng_address() -> str:
    """The user's own SearXNG, from LFM3_SEARXNG or config/search.json ({"searxng": "https://..."}). Empty when none was set."""
    url = (os.environ.get("LFM3_SEARXNG") or "").strip() or str(_setting("searxng") or "").strip()
    p = urlparse(url)
    return url.rstrip("/") if p.scheme in ("http", "https") and p.hostname else ""


def searxng(query: str, n: int, strict: bool) -> list[dict]:
    base = searxng_address()
    if not base:
        return []
    rows = _get(f"{base}/search", {"q": query, "format": "json", "safesearch": 2 if strict else 0}, timeout=12).json().get("results", [])
    return [{"title": _text(r.get("title", "")), "href": r.get("url", ""), "body": _text(r.get("content", ""))} for r in rows[:n] if r.get("url")]


# ---------------------------------------------------------------- deciding when to ask
_TECH = re.compile(
    r"\b(error|exception|traceback|stack ?trace|bug|segfault|compile|compiler|install|pip|npm|cargo|docker|kubernetes|git|regex|sql|api|json|yaml|"
    r"python|javascript|typescript|java|kotlin|rust|golang|c\+\+|c#|php|ruby|bash|linux|ubuntu|fedora|windows|macos|html|css|react|django|flask|fastapi|"
    r"pyinstaller|function|module|import|library|framework|package|dependency|undefined|null|segmentation|permission denied|command)\b", re.I)


def looks_technical(query: str) -> bool:
    return bool(_TECH.search(query))


def snippet_is_adult(row: dict, pattern: re.Pattern) -> bool:
    return bool(pattern.search(f"{urlparse(row.get('href', '')).netloc} {row.get('title', '')}"))
