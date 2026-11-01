#!/usr/bin/env python3
"""Measure the local SearXNG. Standard library only.

  python3 searxng/test.py engines   each engine alone: does it answer, and are the answers on topic?
  python3 searxng/test.py burst     agent-style bursts (several searches in a row), repeated: how often is the answer usable?
  python3 searxng/test.py filter    safe-search strict vs off for an adult term (counts and domains only)
Options: --url http://127.0.0.1:8888  --engines "bing,mojeek"  --pause 1.0
"""
import argparse, json, re, sys, time, urllib.parse, urllib.request
from collections import Counter

STOP = set("a an and are as at be by for from how i in is it of on or that the this to was what when where which who why will with won".split())
FILLER = set("history latest recent most newest current information info details overview about facts guide results result top best".split())
QUERIES = ["Abu Dhabi Grand Prix 2026", "Konark Sun Temple history", "who won the most recent Formula 1 Grand Prix",
           "Azerbaijan Grand Prix 2026 weather Baku", "red panda habitat and diet", "python asyncio gather tutorial"]
ADULT = re.compile(r"porn|xhamster|xnxx|xvideos|xxx|sex|pussy|jav|hentai|erotic|redtube|youporn|brazzers|onlyfans|escort")


def search(url, q, **params):
    qs = urllib.parse.urlencode({"q": q, "format": "json", **params})
    with urllib.request.urlopen(f"{url}/search?{qs}", timeout=30) as r:
        return json.load(r)


def on_topic(rows, query):
    """Half of the results mention a good share of the query's meaningful words (same rule as the app's search helper)."""
    core = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 3 and w not in STOP and w not in FILLER]
    if not rows or not core:
        return bool(rows)
    need = min(3, (len(core) + 1) // 2)
    ok = sum(1 for r in rows if sum(k[:6] in f"{r.get('title', '')} {r.get('content', '')} {r.get('url', '')}".lower() for k in core) >= need)
    return ok * 2 >= len(rows)


def reasons(d):
    return sorted({f"{e[0]}: {e[1]}" for e in d.get("unresponsive_engines", [])})


def general_engines(url, include_disabled):
    """Every loaded general-web engine (SearXNG leaves out the ones marked inactive upstream, e.g. startpage and mojeek)."""
    with urllib.request.urlopen(f"{url}/config", timeout=30) as r:
        E = json.load(r)["engines"]
    skip = {"currency", "dictzone", "lingva", "mymemory translated", "wikidata"}
    return sorted(e["name"] for e in E if ("general" in e["categories"]) and e["name"] not in skip and (include_disabled or e["enabled"]))


def cmd_engines(a):
    """Each engine alone. Only results the engine itself produced are counted, so a fallback to other engines cannot flatter it."""
    names = a.engines.split(",") if a.engines else general_engines(a.url, a.all)
    loaded = set(general_engines(a.url, True))
    for name in names:
        if name not in loaded:
            print(f"{name:14} NOT LOADED (inactive or unknown in this SearXNG)"); continue
        good = empty = 0; why = Counter()
        for q in QUERIES:
            try:
                d = search(a.url, q, engines=name, safesearch=0)
            except Exception as e:
                empty += 1; why[type(e).__name__] += 1; time.sleep(a.pause); continue
            rows = [r for r in d.get("results", []) if name in r.get("engines", [])]
            good += bool(rows and on_topic(rows[:5], q)); empty += not rows
            for r in reasons(d): why[r.split(": ", 1)[-1]] += 1
            time.sleep(a.pause)
        print(f"{name:14} on-topic {good}/{len(QUERIES)}  empty {empty}  {dict(why) or ''}", flush=True)


def cmd_burst(a):
    """A chat turn can make up to 8 searches in a row. Do that several times with a pause between turns."""
    engines = a.engines or "bing,mojeek,wikipedia"
    qs = QUERIES + ["Formula 1 race results", "Formula 1 winner", "Konark Sun Temple", "open weight AI models news"]
    ok = bad = 0; why = Counter(); t0 = time.time()
    for turn in range(a.turns):
        for i in range(8):
            q = qs[(turn * 8 + i) % len(qs)]
            try:
                d = search(a.url, q, engines=engines, safesearch=0)
                rows = d.get("results", [])
                if rows and on_topic(rows[:5], q): ok += 1
                else: bad += 1; why["empty" if not rows else "off-topic"] += 1; [why.update([r]) for r in reasons(d)]
            except Exception as e:
                bad += 1; why[type(e).__name__] += 1
            time.sleep(a.pause)
        print(f"turn {turn + 1}/{a.turns}: usable so far {ok}/{ok + bad}", flush=True)
        time.sleep(a.rest)
    print(f"\nUSABLE {ok}/{ok + bad} ({100 * ok // max(1, ok + bad)}%) in {time.time() - t0:.0f}s with engines={engines}\nfailures: {dict(why) or 'none'}")


def cmd_filter(a):
    engines = a.engines or "bing,mojeek,wikipedia"
    for q in ("porn", "xxx videos"):
        for level, name in ((2, "strict"), (0, "off")):
            hits = []
            for _ in range(2):
                try: rows = search(a.url, q, engines=engines, safesearch=level).get("results", [])
                except Exception: rows = []
                hits.append(f"{sum(1 for r in rows if ADULT.search(urllib.parse.urlparse(r.get('url', '')).netloc))}/{len(rows)}")
                time.sleep(a.pause)
            print(f"{q:11} {name:6} adult-looking domains: {hits}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["engines", "burst", "filter"])
    p.add_argument("--url", default="http://127.0.0.1:8888"); p.add_argument("--engines", default="")
    p.add_argument("--all", action="store_true", help="include engines that are disabled by default"); p.add_argument("--pause", type=float, default=1.2); p.add_argument("--turns", type=int, default=5); p.add_argument("--rest", type=float, default=15)
    a = p.parse_args(); {"engines": cmd_engines, "burst": cmd_burst, "filter": cmd_filter}[a.cmd](a)
