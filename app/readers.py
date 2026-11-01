"""Reading pages that turn away ordinary page requests, through the sites' own public interfaces.

Wikipedia and Stack Overflow are the two most useful sources and the two most likely to answer a program with "403 Forbidden". Both publish
interfaces for programs (the MediaWiki API and the Stack Exchange API, no key needed for light use), which return the same text without
guessing at the page's layout. `read()` returns (title, text) for an address it knows how to read this way, and None for anything else, so
the caller falls back to an ordinary page request.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

import outbound
from sources import UA

_SE_SITES = {"stackoverflow.com": "stackoverflow", "superuser.com": "superuser", "serverfault.com": "serverfault", "askubuntu.com": "askubuntu",
             "mathoverflow.net": "mathoverflow.net"}


def _stack_site(host: str) -> str | None:
    host = host.lower().removeprefix("www.")
    if host in _SE_SITES:
        return _SE_SITES[host]
    if host.endswith(".stackexchange.com") and host.count(".") == 2:
        return host.split(".")[0]
    return None


def _body_text(fragment: str) -> str:
    """The HTML of a question or answer as plain text, code kept as indented lines."""
    soup = BeautifulSoup(fragment or "", "html.parser")
    for pre in soup.find_all("pre"):
        code = pre.get_text()
        pre.replace_with("\n" + "\n".join("    " + ln for ln in code.strip("\n").splitlines()) + "\n")
    for t in soup.find_all(["p", "li", "br", "h1", "h2", "h3", "blockquote"]):
        t.append("\n")
    text = soup.get_text(" ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"(?<=\S)[ \t]{2,}", " ", text).strip()  # runs of spaces inside a line, never the indent of code


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    r = await client.get(url, params=params)
    r.raise_for_status()
    return r.json()


async def _wikipedia(client: httpx.AsyncClient, host: str, title: str) -> tuple[str, str] | None:
    data = await _get(client, f"https://{host}/w/api.php", {"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki", "redirects": 1,
                                                          "titles": title, "format": "json", "formatversion": 2})
    page = (data.get("query", {}).get("pages") or [{}])[0]
    text = (page.get("extract") or "").strip()
    return (page.get("title", title), text) if text and not page.get("missing") else None


async def _stack(client: httpx.AsyncClient, site: str, qid: str) -> tuple[str, str] | None:
    base = "https://api.stackexchange.com/2.3"
    q = (await _get(client, f"{base}/questions/{qid}", {"site": site, "filter": "withbody"})).get("items") or []
    if not q:
        return None
    q = q[0]
    answers = (await _get(client, f"{base}/questions/{qid}/answers", {"site": site, "filter": "withbody", "sort": "votes", "order": "desc", "pagesize": 6})).get("items") or []
    answers.sort(key=lambda a: not a.get("is_accepted"))  # the accepted answer first, the rest by votes
    title = _body_text(q.get("title", ""))
    parts = [f"Question (score {q.get('score', 0)}; tags: {', '.join(q.get('tags', []))}):\n{_body_text(q.get('body', ''))}"]
    for i, a in enumerate(answers, 1):
        parts.append(f"Answer {i}{' (accepted)' if a.get('is_accepted') else ''}, score {a.get('score', 0)}:\n{_body_text(a.get('body', ''))}")
    if not answers:
        parts.append("No answers yet.")
    return title, "\n\n".join(parts)


async def read(url: str) -> tuple[str, str] | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=6), headers={"User-Agent": UA, "Accept": "application/json"}, proxy=outbound.proxy(), follow_redirects=True) as client:
        if host.endswith(".wikipedia.org") and u.path.startswith("/wiki/") and len(u.path) > 6:
            return await _wikipedia(client, host, unquote(u.path[6:]).replace("_", " "))
        site = _stack_site(host)
        m = re.match(r"^/(?:questions|q)/(\d+)", u.path) if site else None
        if site and m:
            return await _stack(client, site, m.group(1))
    return None
