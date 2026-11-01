"""The private-session switch: one flag (never saved to disk) that decides how strict web search is. (Internally "safe" = not private.)

Off (the default): web, news and image search use Bing's strict adult-content filter and everything is saved as usual.
On: a private session (see private.py) and that filter is switched off. The same models run in both modes.

Searching goes through ddgs's Bing engines with the filter (`adlt`) added to the request, not through `DDGS().text()` and friends: ddgs
picks engines at random per call and most of them (Bing included) ignore its `safesearch` argument, so that setting changed nothing.
"""

from __future__ import annotations

import dataclasses
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import urlparse

import evidence
import outbound
import sources

# Held in memory only. Writing the switch to a file would itself be a trace, so every start of the app begins with it off.
_safe = True
_cache: dict[tuple, tuple[float, list[dict]]] = {}  # (kind, query, level) -> (when, rows); in memory only
_stale: dict[tuple, tuple[float, list[dict]]] = {}  # the last good answer to each search, kept a day to show when every source fails
_CACHE_SECONDS = 300
_STALE_SECONDS = 86400


def is_safe() -> bool:
    return _safe


def set_safe(safe: bool):
    global _safe
    _safe = bool(safe)
    _cache.clear()  # results for one mode must never be served in the other
    _stale.clear()
    evidence.clear()


def search_level() -> str:
    """The `safesearch` argument for ddgs."""
    return "on" if is_safe() else "off"


def weights_slot(key: str) -> str:
    """The slot whose files actually get loaded when `key` is asked for."""
    return key  # the same weights in both modes


def slots() -> dict[str, str]:
    """For the UI: which slot's weights each model is using right now."""
    return {}


_ENGINES = {"text": ("bing", "Bing"), "images": ("bing_images", "BingImages"), "news": ("bing_news", "BingNews")}
# A fixed browser identity: with ddgs's random impersonation Bing ignored the filter on roughly one image search in four.
_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

_STOP = frozenset("a an and are as at be by can could did do does explain for from give how i in is it list me of on or please should show tell that the this to was what when where which who why will with won would".split())
_FILLER = frozenset("history latest recent most newest current information info details overview about facts guide results result top best".split())


def _keywords(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 3 and w not in _STOP]


def _coverage(rows: list[dict], query: str) -> tuple[int, int, int]:
    """(rows that mention a query word, distinct query words mentioned across the rows, words needed) for the query's meaningful words."""
    core = [k for k in _keywords(query) if k not in _FILLER]
    hits = [{k for k in core if k[:6] in f"{r.get('title', '')} {r.get('body', '')} {r.get('href', '')}".lower()} for r in rows]
    # Two-word queries need both words somewhere ("who is the CEO of Microsoft" must not be satisfied by CEO pages that never say Microsoft).
    return sum(1 for h in hits if h), len(set().union(*hits)) if hits else 0, (2 if len(core) == 2 else (len(core) + 1) // 2)


def _on_topic(rows: list[dict], query: str) -> bool:
    """Do the results, taken together, cover a fair share of the query's meaningful words? Bing answers some queries with unrelated
    pages (a dictionary entry for "most", a login page for "abu", support articles, forum threads), different on every call; feeding those
    to a small model makes it search in circles. Real results mention different words of the query on different pages (and Bing's
    snippets are often missing, so a page may match on its title alone), whereas junk repeats one accidental word. Half the results must
    match something, and between them they must cover half the query's words."""
    if not rows or not [k for k in _keywords(query) if k not in _FILLER]:
        return bool(rows)
    ok, covered, need = _coverage(rows, query)
    return ok * 2 >= len(rows) and covered >= need


def _adult_rows(rows: list[dict]) -> int:
    return sum(1 for r in rows if _ADULT_SITE.search(f"{urlparse(r.get('href', '')).netloc} {r.get('title', '')}"))


# Adult sites rarely repeat the words searched for in their titles ("erotic stories" finds tube sites with unrelated titles), so for a query that
# is itself adult-themed, "no page mentions the words" proves nothing.
_ADULT_SITE = re.compile(r"porn|xxx|\bsex|xvideos|xhamster|xnxx|nude|hentai|erotic|onlyfans|escort|redtube|pussy|brazzers|milf|fap", re.I)
_ADULT_QUERY = re.compile(r"porn|sex|xxx|nude|naked|erotic|hentai|onlyfans|nsfw|escort|fetish|adult|milf|bdsm|camgirl|lingerie|strip", re.I)


def _variants(kind: str, query: str) -> list[tuple[str, dict, str]]:
    """The requests to try, in order: the query as asked under three market settings, then quoted, then simplified (generic words dropped)."""
    if kind != "text":
        return [(query, {}, "en-us")] * 3
    core = " ".join(w for w in query.split() if w.lower() not in _STOP | _FILLER) or query
    rest = " ".join(w for w in query.split() if w.lower() in _FILLER)
    quoted = f'"{core}"' + (f" {rest}" if rest else "")  # a quoted phrase makes Bing match every word instead of latching onto one
    out: list[tuple[str, dict, str]] = []
    for item in ((query, {}, ""), (query, {}, "en-us"), (query, {}, "en-in"), (quoted, {}, ""), (core, {}, "en-us")):
        if item not in out:
            out.append(item)
    return out


def _bing(kind: str, query: str, level: str, extra: dict, market: str = "en-us") -> list[dict]:
    import importlib

    import httpx

    module, name = _ENGINES[kind]
    engine = getattr(importlib.import_module(f"ddgs.engines.{module}"), name)()  # ddgs builds the request and parses the page
    payload = engine.build_payload(query=query, region="us-en", safesearch="on" if level == "strict" else "off", timelimit=None, page=1, max_results=10)
    payload.pop("cc", None)  # ddgs sends cc=en, which is not a country code
    payload.update(extra, adlt=level)
    # Bing's answer depends on the market it assumes, and no single setting is right for every query from every connection: a market that
    # disagrees with where the request comes from sometimes gets unrelated pages. "" lets Bing decide from the address; the others pin it.
    cookie = f"SRCHHPGUSR=ADLT={level.upper()}"  # the adult filter
    if market:
        cookie = f"_EDGE_CD=m={market}&u={market}; _EDGE_S=mkt={market}&ui={market}; " + cookie
    headers = {"User-Agent": _UA, "Accept-Language": f"{market or 'en-US'},en;q=0.9", "Cookie": cookie}
    r = httpx.get(engine.search_url, params=payload, follow_redirects=True, timeout=15, proxy=outbound.proxy(), headers=headers)
    r.raise_for_status()
    rows = engine.post_extract_results(engine.extract_results(r.text)) if r.text.strip() else []  # strict filtering can leave an empty page
    return [dataclasses.asdict(x) for x in rows]


def _yahoo(query: str, level: str) -> list[dict]:
    """Yahoo's web search through ddgs's parser. Its filter is the `vm` parameter: r strict, p off (ddgs itself never sets it)."""
    import importlib

    import httpx

    module = importlib.import_module("ddgs.engines.yahoo")
    engine = next(getattr(module, c) for c in dir(module) if c.lower() == "yahoo")()
    payload = engine.build_payload(query=query, region="us-en", safesearch="on" if level == "strict" else "off", timelimit=None, page=1)
    payload["vm"] = "r" if level == "strict" else "p"
    r = httpx.get(engine.search_url, params=payload, follow_redirects=True, timeout=12, proxy=outbound.proxy(),
                  headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    r.raise_for_status()
    rows = engine.post_extract_results(engine.extract_results(r.text)) if r.text.strip() else []
    return [dataclasses.asdict(x) for x in rows]


def _url_key(url: str) -> tuple[str, str]:
    p = urlparse(url or "")
    return p.netloc.lower().removeprefix("www."), p.path.rstrip("/")


def _merge(lists: list[list[dict]], n: int, weights: list[float] | None = None) -> list[dict]:
    """Reciprocal-rank fusion: a page several sources return outranks one only a single source returns; duplicates collapse to one entry.
    `weights` says how much each list's ranking counts (the general engines 1.0, a supplementary source less)."""
    score: dict[tuple[str, str], float] = {}
    first: dict[tuple[str, str], dict] = {}
    for i, rows in enumerate(lists):
        w = weights[i] if weights else 1.0
        for rank, r in enumerate(rows):
            k = _url_key(r.get("href", ""))
            score[k] = score.get(k, 0.0) + w / (60 + rank)
            if k not in first or len(r.get("body", "")) > len(first[k].get("body", "")):
                first[k] = r
    return [first[k] for k in sorted(score, key=score.__getitem__, reverse=True)][:n]


_GRACE = 2.0  # seconds the others get once a general engine has answered on topic
_GENERAL = ("yahoo", "bing", "searxng")  # sources that search the whole web; the others add to them or step in when they fail


def _why(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"[:120]


def _search_text(query: str, level: str, n: int) -> list[dict]:
    """Yahoo and Bing are asked at the same time, with Wikipedia (and Stack Overflow for a programming question, and the user's own SearXNG if
    they set one), and the answers are merged. Each is checked on its own: an engine that answers with unrelated pages (Bing does, for about a
    third of queries, whatever the market setting) is left out and the others carry the result. A source that keeps failing is left alone for a
    while (see sources.Health). Further rounds (other market settings, a quoted phrase, a simplified query, Marginalia) only run if no general
    engine was on topic."""
    strict = level == "strict"
    theme = bool(_ADULT_QUERY.search(query))
    adult_q = not strict and theme
    core = " ".join(w for w in query.split() if w.lower() not in _STOP | _FILLER) or query
    rest = " ".join(w for w in query.split() if w.lower() in _FILLER)
    quoted = f'"{core}"' + (f" {rest}" if rest else "")  # a quoted phrase makes Bing match every word instead of latching onto one
    # An adult-themed query never goes to a source that cannot filter it: in strict mode only Bing and Yahoo, which can, are asked.
    more = sources.extras_enabled() and not (strict and theme)
    extras = (["wikipedia"] + (["stackexchange"] if sources.looks_technical(query) else []) if more else []) + (["searxng"] if sources.searxng_address() and not (strict and theme) else [])
    rescue = ["marginalia"] if more else []
    rounds = [[("yahoo", query, ""), ("bing", query, "")] + [(x, query, "") for x in extras],
              [("bing", query, "en-us"), ("yahoo", core, "")] + [(x, core, "") for x in rescue],
              [("bing", query, "en-in")], [("bing", quoted, ""), ("bing", core, "en-us")]]
    # an engine that has been answering with unrelated pages waits for the second round
    late = [j for j in rounds[0] if j[0] in ("yahoo", "bing") and sources.HEALTH.demoted(j[0])]
    if late and len(late) < sum(1 for j in rounds[0] if j[0] in ("yahoo", "bing")):
        rounds[0] = [j for j in rounds[0] if j not in late]
        rounds[1] = rounds[1] + late

    def run(job):
        engine, q, market = job
        t = time.time()
        try:
            if engine == "yahoo":
                rows = _yahoo(q, level)
            elif engine == "bing":
                rows = _bing("text", q, level, {}, market)
            elif engine == "wikipedia":
                rows = sources.wikipedia(q, 4)
            elif engine == "stackexchange":
                rows = sources.stackexchange(q, 4)
            elif engine == "marginalia":
                rows = sources.marginalia(q, 6)
            else:
                rows = sources.searxng(q, 8, strict)
        except Exception as e:  # noqa: BLE001 - ddgs and httpx each have their own exception hierarchy
            sources.HEALTH.failure(engine, _why(e))
            return [], e, engine, time.time() - t
        if strict and engine not in ("yahoo", "bing"):
            rows = [r for r in rows if not sources.snippet_is_adult(r, _ADULT_SITE)]
        return rows, None, engine, time.time() - t

    def on_topic(rows):
        top = rows[:5]
        return bool(top) and (_on_topic(top, query) or (adult_q and _adult_rows(top) * 2 >= len(top)))

    good: list[tuple[str, list[dict]]] = []
    best, best_score = [], (0, 0, 0)
    errors: list[Exception] = []
    attempted = 0
    # a source that keeps failing sits out, unless that would leave nothing to ask: then everything is tried, so recovery is noticed at once
    every = {j[0] for r in rounds for j in r}
    sits_out = (lambda name: not sources.HEALTH.allowed(name)) if any(sources.HEALTH.allowed(n) for n in every) else (lambda name: False)
    for i, jobs in enumerate(rounds):
        enough = sum(len(r) for _, r in good) >= 3  # the backup index is only for when little else came back
        jobs = [j for j in dict.fromkeys(jobs) if not sits_out(j[0]) and not (enough and j[0] == "marginalia")]
        if not jobs:
            continue
        if i:
            time.sleep(0.3)
        pool = ThreadPoolExecutor(len(jobs))
        pending = {pool.submit(run, j) for j in jobs}
        cutoff = None
        while pending:
            done, pending = wait(pending, timeout=None if cutoff is None else max(0.0, cutoff - time.time()), return_when=FIRST_COMPLETED)
            if not done:
                break  # a good answer is in and the rest have had their say: a slow source must not hold the search up
            for fut in done:
                rows, err, engine, took = fut.result()
                attempted += 1
                if err:
                    errors.append(err)
                elif on_topic(rows):
                    sources.HEALTH.success(engine, took)
                    good.append((engine, rows))
                elif rows:
                    sources.HEALTH.junk(engine, took)
                    ok, covered, _need = _coverage(rows[:5], query)
                    score = (covered, ok, _adult_rows(rows[:5]) if adult_q else 0)
                    if score > best_score:
                        best, best_score = rows, score
                else:
                    sources.HEALTH.success(engine, took)  # it answered; there was just nothing to find
            if cutoff is None and any(e in _GENERAL for e, _ in good):
                cutoff = time.time() + _GRACE
        pool.shutdown(wait=False, cancel_futures=True)
        if any(e in _GENERAL for e, _ in good):
            break
    if good:
        return _merge([r for _, r in good], n, [sources.WEIGHT.get(e, 1.0) for e, _ in good])
    if best and adult_q:
        return [{**r, "loose": True} for r in best[:n]]
    if errors and len(errors) == attempted:
        raise errors[0]  # every request failed: that is an error, not "no results"
    return []


def search(kind: str, query: str, n: int) -> list[dict]:
    """Blocking search ("text", "images" or "news") honouring the switch. Safe mode never falls back to an engine that cannot filter.

    Bing's answers vary from call to call and with the market it assumes, so the first answer that is actually about the query wins; if none
    is, the best attempt is returned marked "loose" (the tool tells the model so) rather than nothing, unless it shares no word with the
    query at all."""
    level = "strict" if is_safe() else "off"
    key = (kind, query, level)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_SECONDS:
        return hit[1][:n]
    try:
        if kind == "text":
            rows = _search_text(query, level, n)
            if rows:
                _cache[key] = _stale[key] = (time.time(), rows)
            return rows[:n]
        for attempt, (q, extra, market) in enumerate(_variants(kind, query)):
            if attempt:
                time.sleep(0.3)
            rows = _bing(kind, q, level, extra, market)
            if rows:
                _cache[key] = _stale[key] = (time.time(), rows)
                return rows[:n]
        return []
    except Exception:  # noqa: BLE001 - ddgs and httpx each have their own exception hierarchy
        old = _stale.get(key)
        if old and time.time() - old[0] < _STALE_SECONDS:  # every source failed: the last answer to this very search is better than an error
            return [{**r, "stale": True} for r in old[1][:n]]
        if is_safe():
            raise
        from ddgs import DDGS

        try:
            rows = getattr(DDGS(), kind)(query, max_results=n, safesearch="off")
        except Exception:  # noqa: BLE001 - nothing found is an answer, not an error
            return []
        return rows if kind != "text" or _on_topic(rows, query) else []
