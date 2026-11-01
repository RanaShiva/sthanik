"""Tools the models can call: built-ins (web, time, math, local files) and MCP servers.

Design notes
- Tool *results are untrusted text* (web pages, files, third-party servers): they go back to the model
  as data, and nothing here can write files or run commands. Anything that reaches the network or the
  disk is read-only and bounded in size.
- fetch_url refuses non-public addresses (loopback, LAN, link-local, cloud metadata) at every redirect hop.
  It resolves DNS and then lets httpx connect by name, so a hostile DNS server that answers differently
  the second time could still slip through; that residual risk is accepted for a local tool.
- Local MCP servers (stdio) are started from config/mcp.json only. They run arbitrary commands as you,
  so the UI can list and toggle them but never create them.
"""

import ast
import asyncio
import contextvars
import contextlib
import ipaddress
import json
import math
import operator
import os
import re
import shlex
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from bs4 import BeautifulSoup

import documents as docs
import evidence
import images
import memory
import outbound
import paths
import readers
import safebrowsing
import sources as search_sources  # _web_search has its own list called sources
from skills import SkillError, SkillStore

ROOT = paths.HOME  # user data, models and binaries; see paths.py
CONFIG_DIR = ROOT / "config"
TOOLS_CONFIG = CONFIG_DIR / "tools.json"
MCP_CONFIG = CONFIG_DIR / "mcp.json"

MAX_RESULT_CHARS = 12000
# Pages are asked for the way a browser asks: many sites answer a program-looking request with 403 and the same page to a browser.
PAGE_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5", "Accept-Language": "en-US,en;q=0.9"}


class ToolError(Exception):
    """A failure the model should see (bad arguments, blocked URL, missing file...)."""


@dataclass
class ToolResult:
    text: str
    sources: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)  # generated documents the UI should offer for download
    images: list[dict] = field(default_factory=list)  # generated pictures the UI should show


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    group: str
    handler: Callable[[dict], Awaitable[ToolResult]]
    timeout: int = 90


def _short(text: str, limit: int) -> str:
    """One line cut at a word, for a title: some sites stuff theirs with several pages' worth of keywords."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + f"\n…[truncated, {len(text) - limit} more characters]"


def _atomic_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def _read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- web


NO_RESULTS = "No results. Search again with fewer, simpler words (three to five), or answer from what you already have and say what you could not find."


async def _web_search(args: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("query is required")
    n = max(1, min(int(args.get("max_results") or 5), 8))
    try:
        rows = await asyncio.to_thread(safebrowsing.search, "text", query, n)
    except Exception as e:  # ddgs raises its own hierarchy; surface it plainly
        raise ToolError(f"Search failed: {e}") from e
    if not rows:
        return ToolResult(NO_RESULTS)
    read = {}
    if args.get("read_pages") is not False and search_sources.reading_enabled() and not rows[0].get("loose"):  # a loosely related result is not worth opening
        try:
            read = await evidence.excerpts(query, rows, _load_page)
        except Exception:  # noqa: BLE001 - the snippets alone are still an answer
            read = {}
    lines, sources = [], []
    for i, r in enumerate(rows, 1):
        line = f"[{i}] {_short(r.get('title', ''), 120)}\n    {r.get('href', '')}\n    {_clip(r.get('body', ''), 400)}"
        if read.get(i - 1):
            line += "\n    From the page: " + " … ".join(read[i - 1])
        lines.append(line)
        sources.append({"title": r.get("title", ""), "url": r.get("href", "")})
    if rows and rows[0].get("loose"):
        lines.insert(0, "Note: the search engine matched only part of this query, so these results are loosely related. Say so if they do not answer it.")
    if rows and rows[0].get("stale"):
        lines.insert(0, "Note: no search engine could be reached just now, so these are the results of an earlier identical search and may be out of date. Say so.")
    return ToolResult("\n".join(lines), sources)


async def _news_search(args: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("query is required")
    n = max(1, min(int(args.get("max_results") or 5), 8))
    try:
        rows = await asyncio.to_thread(safebrowsing.search, "news", query, n)
    except Exception as e:
        raise ToolError(f"News search failed: {e}") from e
    if not rows:
        return ToolResult(NO_RESULTS)
    lines, sources = [], []
    for i, r in enumerate(rows, 1):
        lines.append(f"[{i}] {r.get('title', '').strip()} ({r.get('source', '')}, {str(r.get('date', ''))[:10]})\n"
                     f"    {r.get('url', '')}\n    {_clip(r.get('body', ''), 300)}")
        sources.append({"title": r.get("title", ""), "url": r.get("url", "")})
    return ToolResult("\n".join(lines), sources)


async def _check_public_host(host: str):
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ToolError(f"Could not resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ToolError(f"Blocked: {host} resolves to a non-public address ({ip}).")


def _refusal(status: int, host: str) -> str:
    """What to tell the model when a page could not be read, ending with what to do next."""
    if status in (401, 402, 403, 451):
        return f"{host} does not let programs read this page (HTTP {status}). Use another of the search results instead."
    if status == 429:
        return f"{host} is limiting requests (HTTP 429). Use another of the search results instead."
    if status in (404, 410):
        return f"That page no longer exists at {host} (HTTP {status}). Use another of the search results instead."
    return f"{host} could not give the page (HTTP {status}). Use another of the search results instead."


async def _read_once(client: httpx.AsyncClient, url: str) -> tuple[int, httpx.Headers, bytes, str | None]:
    async with client.stream("GET", url) as r:
        if r.status_code >= 300:
            return r.status_code, r.headers, b"", None
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype and not (ctype.startswith("text/") or ctype in ("application/json", "application/xml", "application/xhtml+xml")):
            raise ToolError(f"Unsupported content type: {ctype}")
        body = b""
        async for chunk in r.aiter_bytes():
            body += chunk
            if len(body) > 2_000_000:
                break
        return r.status_code, r.headers, body, r.encoding


def _readable_text(raw: str, ctype: str) -> tuple[str, str]:
    """(title, the page's text) from what a site sent: scripts, menus and footers are dropped and block boundaries become line breaks."""
    text, title = raw, ""
    if ctype in ("", "text/html", "application/xhtml+xml"):
        soup = BeautifulSoup(text, "html.parser")
        title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()
        node = soup.find("main") or soup.find("article") or soup.body or soup
        for t in node.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "pre", "blockquote", "br"]):
            t.append("\n")  # block boundaries become line breaks; inline tags just join with spaces
        text = node.get_text(" ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return title, re.sub(r"\n\s*\n+", "\n\n", text).strip()


async def _load_page(url: str) -> tuple[str, str, str]:
    """(title, the whole text, final address) of a page. Wikipedia and Stack Overflow come through their own interfaces. Pages read in the last five
    minutes are not fetched again (search reads the top ones, and the model may ask for one of them next)."""
    hit = evidence.cached_page(url)
    if hit:
        return hit
    first = urlparse(url)
    if first.scheme not in ("http", "https") or not first.hostname:
        raise ToolError("Only http(s) URLs are supported.")
    try:  # Wikipedia and Stack Overflow answer programs through their own interfaces, not as web pages
        special = await readers.read(url)
    except Exception:  # noqa: BLE001 - whatever went wrong, the page itself is still worth a try
        special = None
    if special:
        page = (special[0], special[1], url)
        evidence.remember_page(url, page)
        return page
    start = url
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15, connect=8), headers=PAGE_HEADERS, proxy=outbound.proxy()) as client:
            for _ in range(6):
                u = urlparse(url)
                if u.scheme not in ("http", "https") or not u.hostname:
                    raise ToolError("Only http(s) URLs are supported.")
                await _check_public_host(u.hostname)
                status, headers, body, encoding = await _read_once(client, url)
                if status in (403, 429, 503):  # often a passing refusal: one more try, after the pause the site asks for
                    wait = headers.get("retry-after", "")
                    await asyncio.sleep(min(float(wait), 3.0) if wait.replace(".", "", 1).isdigit() else 0.8)
                    status, headers, body, encoding = await _read_once(client, url)
                if status in (301, 302, 303, 307, 308) and headers.get("location"):
                    url = urljoin(url, headers["location"])
                    continue
                if status >= 400:
                    raise ToolError(_refusal(status, u.hostname))
                break
            else:
                raise ToolError("Too many redirects.")
    except httpx.TimeoutException as e:
        raise ToolError(f"{urlparse(url).hostname} did not answer in time. Use another of the search results instead.") from e
    except httpx.HTTPError as e:
        raise ToolError(f"Could not read {urlparse(url).hostname} ({type(e).__name__}). Use another of the search results instead.") from e
    ctype = headers.get("content-type", "").split(";")[0].strip().lower()
    title, text = await asyncio.to_thread(_readable_text, body.decode(encoding or "utf-8", errors="replace"), ctype)
    page = (title, text, url)
    evidence.remember_page(start, page)
    return page


async def _fetch_url(args: dict) -> ToolResult:
    url = str(args.get("url", "")).strip()
    limit = max(500, min(int(args.get("max_chars") or 6000), 20000))
    title, text, url = await _load_page(url)
    head = f"{title}\n{url}\n\n" if title else f"{url}\n\n"
    return ToolResult(head + _clip(text, limit), [{"title": title or urlparse(url).hostname, "url": url}])


# ---------------------------------------------------------------- time, math


async def _current_time(args: dict) -> ToolResult:
    tz_name = str(args.get("timezone") or "").strip()
    try:
        now = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now().astimezone()
    except ZoneInfoNotFoundError as e:
        raise ToolError(f"Unknown timezone '{tz_name}'. Use an IANA name like 'Europe/Paris'.") from e
    return ToolResult(f"{now:%A, %d %B %Y, %H:%M:%S} ({now.tzname()}, UTC{now:%z})  ISO: {now.isoformat(timespec='seconds')}")


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
          "atan": math.atan, "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
          "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial,
          "min": min, "max": max}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 10000:
            raise ToolError("Exponent too large.")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and (not args or args[0] > 500):
            raise ToolError("factorial argument too large.")
        return _FUNCS[node.func.id](*args)
    raise ToolError("Unsupported expression. Use numbers, + - * / // % **, parentheses, pi, e, and functions like sqrt, sin, log.")


async def _calculator(args: dict) -> ToolResult:
    expr = str(args.get("expression", "")).strip().replace("^", "**")
    if not expr or len(expr) > 200:
        raise ToolError("Provide an expression of at most 200 characters.")
    try:
        value = _eval(ast.parse(expr, mode="eval"))
    except ToolError:
        raise
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError) as e:
        raise ToolError(f"Could not evaluate: {e}") from e
    if isinstance(value, float):
        value = round(value, 12)
    return ToolResult(f"{expr} = {value}")


# ---------------------------------------------------------------- local files (read-only, sandboxed to one folder)


class FilesConnector:
    def __init__(self, root: str | None):
        self.root = Path(root).expanduser().resolve() if root else None

    def _resolve(self, rel: str) -> Path:
        if not self.root:
            raise ToolError("No folder is connected. Choose one on the Tools page.")
        p = (self.root / (rel or ".").lstrip("/")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError("Path is outside the connected folder.")
        if any(part.startswith(".") for part in p.relative_to(self.root).parts):
            raise ToolError("Hidden files and folders are not accessible.")
        return p

    async def list_files(self, args: dict) -> ToolResult:
        p = self._resolve(str(args.get("path") or "."))
        if not p.is_dir():
            raise ToolError("Not a directory.")
        rows = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith("."):
                continue
            rows.append(f"{'[dir] ' if child.is_dir() else '      '}{child.name}" + ("" if child.is_dir() else f"  ({child.stat().st_size} bytes)"))
            if len(rows) >= 200:
                rows.append("…(more entries not shown)")
                break
        return ToolResult("\n".join(rows) or "(empty folder)")

    async def read_file(self, args: dict) -> ToolResult:
        p = self._resolve(str(args.get("path", "")))
        if not p.is_file():
            raise ToolError("Not a file.")
        limit = max(500, min(int(args.get("max_chars") or 8000), 30000))
        data = p.read_bytes()[:1_000_000]
        if b"\x00" in data[:4096]:
            raise ToolError("Binary file; only text files can be read.")
        return ToolResult(_clip(data.decode("utf-8", errors="replace"), limit))


# ---------------------------------------------------------------- MCP


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:64]


class McpServer:
    def __init__(self, name: str, cfg: dict):
        self.name, self.cfg = name, cfg
        self.status = "starting"  # starting | connected | error | disabled
        self.error: str | None = None
        self.tools: list[dict] = []
        self.client = None
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.ready = asyncio.Event()

    @property
    def kind(self) -> str:
        return "http" if self.cfg.get("url") else "stdio"

    async def _run(self):
        from mcp import StdioServerParameters
        from mcp.client.client import Client

        try:
            if self.kind == "http":
                target: Any = self.cfg["url"]
            else:
                cmd = self.cfg["command"]
                if cmd in ("python", "python3"):
                    cmd = sys.executable
                target = StdioServerParameters(command=cmd, args=[str(a) for a in self.cfg.get("args", [])],
                                               env=self.cfg.get("env") or None, cwd=self.cfg.get("cwd") or str(ROOT))
            async with Client(target) as client:
                listed = await client.list_tools()
                self.tools = [{"name": t.name, "description": t.description or "", "schema": t.input_schema or {"type": "object", "properties": {}}}
                              for t in listed.tools]
                self.client, self.status, self.error = client, "connected", None
                self.ready.set()
                await self.stop_event.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # incl. ExceptionGroup from the transport's task group
            self.status, self.error = "error", _first_message(e)
        finally:
            self.client = None
            self.ready.set()

    def start(self):
        self.stop_event.clear()
        self.ready.clear()
        self.status, self.error, self.tools = "starting", None, []
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        self.stop_event.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, 8)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
            except Exception:
                pass
        self.task = None
        self.client = None

    async def call(self, tool: str, args: dict) -> ToolResult:
        if not self.client:
            raise ToolError(f"MCP server '{self.name}' is not connected.")
        try:
            res = await asyncio.wait_for(self.client.call_tool(tool, args), 60)
        except asyncio.TimeoutError as e:
            raise ToolError("MCP tool timed out after 60 s.") from e
        parts = []
        for c in res.content or []:
            parts.append(c.text if getattr(c, "type", "") == "text" else f"[{getattr(c, 'type', 'content')} content omitted]")
        text = "\n".join(parts) or "(no output)"
        if res.is_error:
            raise ToolError(text)
        return ToolResult(_clip(text, MAX_RESULT_CHARS))


def _first_message(e: BaseException) -> str:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return (str(e) or e.__class__.__name__)[:300]


# ---------------------------------------------------------------- documents


def _doc_result(info: dict) -> ToolResult:
    kb = max(1, info["size"] // 1024)
    return ToolResult(f"Created {info['name']} ({info['format'].upper()}, {kb} KB). The user can download it from this chat or from the Documents page.", files=[info])


async def _create_document(args: dict) -> ToolResult:
    try:
        return _doc_result(await docs.create_document(str(args.get("title", "")), str(args.get("content", "")), str(args.get("format") or "pdf")))
    except docs.DocError as e:
        raise ToolError(str(e)) from e


async def _create_slides(args: dict) -> ToolResult:
    try:
        return _doc_result(await docs.create_slides(str(args.get("title", "")), str(args.get("content", "")), str(args.get("format") or "pptx")))
    except docs.DocError as e:
        raise ToolError(str(e)) from e


async def _create_spreadsheet(args: dict) -> ToolResult:
    try:
        return _doc_result(await docs.create_spreadsheet(str(args.get("title", "")), str(args.get("csv", ""))))
    except docs.DocError as e:
        raise ToolError(str(e)) from e


# The document the conversation is working on, sent by the browser with each tool call. It wins over "the newest file", which is
# often a by-product (a plan, an export) and not the document the user means.
ACTIVE_DOC: contextvars.ContextVar[str | None] = contextvars.ContextVar("active_doc", default=None)
# The sections the user's request named ("the picture in Place 3"): picture tools stay inside them even if the model forgets the argument.
ONLY_SECTIONS: contextvars.ContextVar[list] = contextvars.ContextVar("only_sections", default=[])


def _resolve_doc(name: str) -> str:
    """Accept a file name with or without extension. With no name, or a name that matches nothing, the document being worked on;
    failing that, the most recent editable document."""
    items = [d for d in docs.list_documents() if d["editable"]]
    names = {d["name"] for d in items}
    active = ACTIVE_DOC.get()
    active = active if active in names else None
    name = (name or "").strip()
    if not name:
        if active:
            return active
        if items:
            return items[0]["name"]
        raise ToolError("There are no editable documents yet.")
    if name in names:
        return name
    hits = [d["name"] for d in items if d["name"].rsplit(".", 1)[0] == name or d["name"].startswith(name)]
    if len(hits) == 1:
        return hits[0]
    if active:  # a made-up or mangled name while a document is open: it means that one
        return active
    raise ToolError(f"No editable document called '{name}'. Use list_documents to see the names.")


async def _list_documents(args: dict) -> ToolResult:
    items = docs.list_documents()[:15]
    if not items:
        return ToolResult("There are no documents yet.")
    return ToolResult("\n".join(f"- {d['name']} ({d['format'].upper()}, {'editable' if d['editable'] else 'not editable'})" for d in items))


async def _read_document(args: dict) -> ToolResult:
    try:
        g = docs.get_source(_resolve_doc(str(args.get("name", ""))))
    except docs.DocError as e:
        raise ToolError(str(e)) from e
    heads = "\n".join(f"{'  ' * (o['level'] - 1)}- {o['title']}" for o in g["outline"]) or "(no headings)"
    return ToolResult(f"{g['name']} - title: {g['title']}\nSections:\n{heads}\n\n--- source ---\n" + _clip(g["source"], 12000))


async def _edit_document(args: dict, change) -> ToolResult:
    try:
        name = _resolve_doc(str(args.get("name", "")))
        info = await docs.update_source(name, change(docs.get_source(name)["source"]), by="model")
        heads = ", ".join(o["title"] for o in docs.get_source(name)["outline"]) or "(no headings)"
    except docs.DocError as e:
        raise ToolError(str(e)) from e
    return ToolResult(f"Updated {info['name']}. Sections now: {heads}. The user can open it, edit it further or undo this change.", files=[info])


async def _edit_document(hub, args: dict) -> ToolResult:
    import docagent
    name = _resolve_doc(str(args.get("name", "")))
    try:
        r = await docagent.edit_document(name, str(args.get("instruction", "")), hub.llm, hub.llm_text)
    except (docagent.AgentError, docs.DocError) as e:
        raise ToolError(str(e)) from e
    except Exception as e:  # noqa: BLE001 - model unavailable, bad JSON...
        raise ToolError(f"The edit could not be completed: {e}") from e
    text = f"Edited {name}. Done: {'; '.join(r['applied'])}."
    if r["failed"]:
        text += f" NOT done: {'; '.join(r['failed'])}."
    if r.get("deferred"):
        text += (f" Picture requests were not handled here ({'; '.join(r['deferred'])}): call illustrate_document (source web for real photos, replace true to swap the old pictures) "
                 "or add_picture_to_document.")
    return ToolResult(text + " The user can open it, edit it further or undo this change.", files=[r["info"]])


async def _illustrate_document(hub, args: dict) -> ToolResult:
    import docagent
    name = _resolve_doc(str(args.get("name", "")))
    try:
        count = int(args.get("count")) if args.get("count") else None  # None = every section that has no picture
    except (TypeError, ValueError):
        count = None
    try:
        r = await docagent.illustrate_document(name, count, hub.llm, hub.imagegen, str(args.get("source") or "auto"), bool(args.get("replace")), ONLY_SECTIONS.get() or None)
    except (docagent.AgentError, docs.DocError) as e:
        raise ToolError(str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"The pictures could not be added: {e}") from e
    if r.get("note"):
        return ToolResult(f"Nothing was added to {name}: {r['note']}. The job is done. To swap them for new ones call illustrate_document with replace true.", files=[r["info"]])
    web = sum(1 for a in r["added"] if a.get("source") == "web")
    text = (f"Added {len(r['added'])} of {r['requested']} requested pictures to {name} ({web} from the web, {len(r['added']) - web} generated), in: "
            f"{', '.join(a['section'] or 'the document' for a in r['added'])}.")
    if r.get("removed"):
        text += f" Removed {len(r['removed'])} old picture{'s' if len(r['removed']) != 1 else ''} first."
    if r["failed"]:
        text += f" Problems: {'; '.join(r['failed'])}."
    if len(r["added"]) < r["requested"]:
        text += " NOT all requested pictures were added; tell the user exactly how many were."
    return ToolResult(text, files=[r["info"]], images=[images.info(images.image_path(a["name"])) for a in r["added"][:6]])


async def _remember(args: dict) -> ToolResult:
    try:
        item = memory.add(str(args.get("fact", "")), source="model")
    except memory.MemoryError_ as e:
        raise ToolError(str(e)) from e
    return ToolResult(f"Remembered: {item['text']}")


async def _recall(args: dict) -> ToolResult:
    items = memory.search(str(args.get("query", "")))
    return ToolResult("\n".join(f"- {i['text']}" for i in items) if items else "Nothing in memory matches that.")


async def _forget(args: dict) -> ToolResult:
    try:
        return ToolResult(f"Forgot: {memory.delete(str(args.get('fact', '')))}")
    except memory.MemoryError_ as e:
        raise ToolError(str(e)) from e


async def _review_document_pictures(hub, args: dict) -> ToolResult:
    import docagent
    import picturereview
    name = _resolve_doc(str(args.get("name", "")))
    fix = args.get("fix") is not False
    try:
        r = await hub.review_pictures(name, fix, ONLY_SECTIONS.get() or None)
    except (docagent.AgentError, docs.DocError) as e:
        raise ToolError(str(e)) from e
    except RuntimeError as e:
        raise ToolError(str(e)) from e
    text = picturereview.summary(r)
    if r["unchanged"]:
        text += "\nTell the user which sections still have a weak picture; offer to try generating one with illustrate_document (source generate, replace true) for those sections."
    new = [images.info(p) for x in r["checked"] if x["action"] == "replaced" and (p := images.image_path(x["new"]))]
    return ToolResult(text, files=[r["info"]], images=new[:6])


async def _undo_document(args: dict) -> ToolResult:
    name = _resolve_doc(str(args.get("name", "")))
    try:
        info = await docs.revert(name)
    except docs.DocError as e:
        raise ToolError(str(e)) from e
    return ToolResult(f"Undid the last change to {name}; it is back to the previous version.", files=[info])


async def _check_document(args: dict) -> ToolResult:
    import docaudit
    name = _resolve_doc(str(args.get("name", "")))
    try:
        issues = docaudit.audit(docs.get_source(name)["source"])
    except docs.DocError as e:
        raise ToolError(str(e)) from e
    text = f"{name}:\n{docaudit.summary(issues)}"
    if any(i["fixable"] for i in issues):
        text += "\nThe fixable ones are removed by tidy_document."
    if any(i["type"] == "numbering_gap" for i in issues):
        text += "\nMissing numbered sections: do NOT invent them. Tell the user which are missing and ask what they should contain."
    return ToolResult(text)


async def _tidy_document(args: dict) -> ToolResult:
    import docaudit
    name = _resolve_doc(str(args.get("name", "")))
    try:
        cur = docs.get_source(name)["source"]
        new, fixed = docaudit.tidy(cur)
        if new == cur:
            return ToolResult(f"{name} has nothing to tidy.\n" + docaudit.summary(docaudit.audit(cur)))
        info = await docs.update_source(name, new, by="model")
    except docs.DocError as e:
        raise ToolError(str(e)) from e
    left = docaudit.audit(new)
    return ToolResult(f"Tidied {name}: " + "; ".join(fixed) + f".\nStill to deal with:\n{docaudit.summary(left)}" + "\nThe user can undo this from the editor.", files=[info])


async def _remove_pictures(args: dict) -> ToolResult:
    import docagent
    name = _resolve_doc(str(args.get("name", "")))
    try:
        r = await docagent.remove_pictures(name, str(args.get("section") or "") or None, str(args.get("image") or "") or None, ONLY_SECTIONS.get() or None)
    except (docagent.AgentError, docs.DocError) as e:
        raise ToolError(str(e)) from e
    return ToolResult(f"Removed {len(r['removed'])} picture{'s' if len(r['removed']) != 1 else ''} from {name}.", files=[r["info"]])


async def _add_picture(args: dict) -> ToolResult:
    import docagent
    name = _resolve_doc(str(args.get("name", "")))
    names = [str(args["image"])] if args.get("image") else []
    try:
        count = int(args.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    try:
        only = ONLY_SECTIONS.get()
        r = await docagent.attach_pictures(name, names, str(args.get("section") or "") or None, count, only[0] if len(only) == 1 else None)
    except (docagent.AgentError, docs.DocError) as e:
        raise ToolError(str(e)) from e
    where = ", ".join(a["section"] or "the top" for a in r["added"])
    return ToolResult(f"Placed {len(r['added'])} picture{'s' if len(r['added']) != 1 else ''} in {name}: {where}.", files=[r["info"]])


async def _find_image(args: dict) -> ToolResult:
    import webimages
    query = str(args.get("query", "")).strip()
    try:
        count = int(args.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    try:
        saved, problems = await webimages.find(query, count)
    except webimages.WebImageError as e:
        raise ToolError(str(e)) from e
    if not saved:
        raise ToolError("No usable picture was found on the web for that" + (f" ({problems[0]})" if problems else "") + ". Try other words, or make one with generate_image.")
    lines = [f"- {i['name']}: {(i.get('prompt') or '')[:90]} (source: {i.get('credit') or 'the web'})" for i in saved]
    return ToolResult("Found and saved " + str(len(saved)) + " picture" + ("s" if len(saved) != 1 else "") + " (the user can see them):\n" + "\n".join(lines)
                      + "\nTo show one in your reply write ![short caption](img:NAME). To put it into the document the user is working on, call add_picture_to_document. "
                      "Mention where it came from.", sources=[{"title": i.get("prompt") or i["name"], "url": i["source_url"]} for i in saved if i.get("source_url")], images=saved)


async def _list_images(args: dict) -> ToolResult:
    items = [i for i in images.list_images() if i["kind"] == "image"][:20]
    if not items:
        return ToolResult("There are no pictures yet.")
    return ToolResult("\n".join(f"- {i['name']}: {(i.get('prompt') or '')[:100]}" for i in items) + "\nShow one with ![caption](img:NAME).")


async def _generate_image(hub, args: dict) -> ToolResult:
    import imagegen
    try:
        info = await hub.imagegen.generate(str(args.get("prompt", "")), str(args.get("size") or "square"))
    except imagegen.ImageError as e:
        raise ToolError(str(e)) from e
    return ToolResult(f"Made the picture {info['name']}. To put it into the document the user is working on, call add_picture_to_document. "
                      f"In a new document or in your reply, write ![short caption](img:{info['name']}) on its own line.", images=[info])


# ---------------------------------------------------------------- hub


WEB_SCHEMA_Q = {"type": "object", "properties": {
    "query": {"type": "string", "description": "What to search for."},
    "max_results": {"type": "integer", "description": "Number of results, 1-8 (default 5)."}}, "required": ["query"]}

GROUPS = {
    "web": {"label": "Web", "description": "Search the internet, read web pages and find pictures.", "icon": "globe", "default_approval": "auto"},
    "utility": {"label": "Utilities", "description": "Current date/time and a calculator.", "icon": "sparkles", "default_approval": "auto"},
    "files": {"label": "Local files", "description": "Read-only access to one folder you choose.", "icon": "folder", "default_approval": "ask"},
    "documents": {"label": "Documents", "description": "Create and edit PDF, Word, slide decks (PowerPoint), HTML and Excel files.", "icon": "filetext", "default_approval": "auto"},
    "images": {"label": "Images", "description": "Make pictures with a local text-to-image model (Z-Image-Turbo). The model can add them to documents and slides.", "icon": "image", "default_approval": "ask"},
    "memory": {"label": "Memory", "description": "Remember facts about you and your work across conversations (viewable and editable on the Memory page).", "icon": "book", "default_approval": "auto"},
    "skills": {"label": "Skills", "description": "Expert instruction sets the model can load on demand.", "icon": "book", "default_approval": "auto"},
}


class _NoImages:
    @staticmethod
    def available() -> bool:
        return False

    @staticmethod
    def missing() -> list[str]:
        return ["image model"]

    async def generate(self, *a, **k):
        raise ToolError("Image generation is not set up.")


class ToolHub:
    def __init__(self, skills: SkillStore):
        self.skills = skills
        self.settings = _read_json(TOOLS_CONFIG, {})
        self.files = FilesConnector(self.settings.get("files_root"))
        self.mcp: dict[str, McpServer] = {}
        self.imagegen = _NoImages()  # replaced by the server with the real generator
        self.llm = None  # async (messages, json_schema, max_tokens) -> dict, supplied by the server
        self.llm_text = None  # async (messages, max_tokens) -> str
        self.builtin: dict[str, Tool] = {}
        self._register_builtins()

    # -- setup
    def _register_builtins(self):
        def add(name, desc, schema, group, handler):
            self.builtin[name] = Tool(name, desc, schema, group, handler)

        add("web_search", "Search the web for current information, facts, documentation or anything you do not know. Returns titles, URLs and snippets, and for the top results the passages of the page itself that match the question (marked From the page).",
            WEB_SCHEMA_Q, "web", _web_search)
        add("news_search", "Search recent news articles on a topic. Returns headlines, sources, dates and URLs.",
            WEB_SCHEMA_Q, "web", _news_search)
        add("fetch_url", "Download a web page and return its readable text. Use after web_search to read a result in full.",
            {"type": "object", "properties": {"url": {"type": "string", "description": "The http(s) URL to read."},
                                               "max_chars": {"type": "integer", "description": "Maximum characters to return (default 6000)."}},
             "required": ["url"]}, "web", _fetch_url)
        add("find_image", "Find a REAL picture on the internet (a photo of a landmark, place, person, animal, plant, object, map, flag, artwork or event), save it to the picture library and show it to the user. Use it when the question is about how a real, named thing looks. For imagined, abstract or stylised pictures use generate_image instead.",
            {"type": "object", "properties": {"query": {"type": "string", "description": "What to look for, specific: the name plus the place or what is shown, e.g. 'Konark Sun Temple Odisha'."},
                                               "count": {"type": "integer", "description": "How many different pictures (1-4, default 1)."}}, "required": ["query"]}, "web", _find_image)
        self.builtin["find_image"].timeout = 180
        add("current_time", "Get the current date and time, optionally in a given timezone.",
            {"type": "object", "properties": {"timezone": {"type": "string", "description": "IANA timezone such as 'Asia/Kolkata'. Omit for local time."}}},
            "utility", _current_time)
        add("calculator", "Evaluate a math expression exactly (use this for arithmetic instead of computing in your head).",
            {"type": "object", "properties": {"expression": {"type": "string", "description": "e.g. '(17*23)+sqrt(144)'"}}, "required": ["expression"]},
            "utility", _calculator)
        add("create_document", "Create a downloadable document file from Markdown. Use when the user asks for a report, memo, letter, summary or any document as a PDF, Word file, HTML or text.",
            {"type": "object", "properties": {"title": {"type": "string", "description": "Document title."},
                                               "content": {"type": "string", "description": "The full document body in Markdown (headings, lists, tables, ```mermaid diagrams)."},
                                               "format": {"type": "string", "enum": list(docs.DOC_FORMATS), "description": "File format (default pdf)."}},
             "required": ["title", "content"]}, "documents", _create_document)
        add("create_slides", "Create a presentation (PowerPoint .pptx or PDF slides). Each slide starts with a '## Slide title' heading followed by short bullet points.",
            {"type": "object", "properties": {"title": {"type": "string", "description": "Deck title."},
                                               "content": {"type": "string", "description": "Markdown: one '## Title' heading per slide, then '- bullet' lines."},
                                               "format": {"type": "string", "enum": list(docs.SLIDE_FORMATS), "description": "pptx (default) or pdf."}},
             "required": ["title", "content"]}, "documents", _create_slides)
        add("create_spreadsheet", "Create an Excel (.xlsx) spreadsheet from CSV text. The first row is the header.",
            {"type": "object", "properties": {"title": {"type": "string", "description": "Spreadsheet title."},
                                               "csv": {"type": "string", "description": "CSV rows, one per line, comma separated."}},
             "required": ["title", "csv"]}, "documents", _create_spreadsheet)
        name_prop = {"name": {"type": "string", "description": "File name of the document (from list_documents or the create result). Omit for the most recent one."}}
        add("list_documents", "List the documents that have been created, with their file names and whether they can be edited.",
            {"type": "object", "properties": {}}, "documents", _list_documents)
        add("read_document", "Read the current text of a created document and its list of sections. Call this before editing so you know what is in it.",
            {"type": "object", "properties": dict(name_prop)}, "documents", _read_document)
        add("edit_document", "Change an existing document, slide deck or spreadsheet: shorten, expand, rewrite, translate, fix, add sections, remove sections, reorder. Describe ALL the changes in one instruction; the tool carries out every one of them and reports what it did. Use this instead of creating a new file.",
            {"type": "object", "properties": {**name_prop, "instruction": {"type": "string", "description": "Everything to change, in plain words, e.g. 'make the introduction shorter and add a section about cost savings after Benefits'."}},
             "required": ["instruction"]}, "documents", lambda args: _edit_document(self, args))
        self.builtin["edit_document"].timeout = 600
        add("review_document_pictures", "LOOK at every picture in a document with the vision model and replace the poor ones (wrong subject, adverts, banners, low quality) with better real photos from the web. Use when the user says the pictures are not good, asks to check, analyse or review them, or wants better images. It judges the real pixels, not the captions. Takes a minute or two: the vision model is loaded for it. With fix false it only reports.",
            {"type": "object", "properties": {**name_prop, "fix": {"type": "boolean", "description": "Replace weak pictures (default true); false = only report."}}},
            "documents", lambda a: _review_document_pictures(self, a))
        self.builtin["review_document_pictures"].timeout = 900
        add("undo_document_change", "Undo the last change made to a document (the previous version comes back). Use when the user says 'undo that', 'go back' or 'that was wrong'. Repeating it steps further back (the last twelve versions are kept).",
            {"type": "object", "properties": dict(name_prop)}, "documents", _undo_document)
        add("check_document", "Look for damage in a document: repeated sections, placeholders like [To be added], leftover instructions, empty or stub sections, repeated paragraphs or pictures, broken picture references, an unclosed diagram block, numbered sections with gaps. Call it before tidy_document, or when asked whether a document is in good shape.",
            {"type": "object", "properties": dict(name_prop)}, "documents", _check_document)
        add("tidy_document", "Clean a document: removes placeholders, leftover instructions, stub and empty sections, repeated sections/paragraphs/pictures and broken picture references, and closes a diagram block that never ended. It never rewrites real content. Use it for 'clean this up', 'make it uniform', 'remove the weird stuff'.",
            {"type": "object", "properties": dict(name_prop)}, "documents", _tidy_document)
        add("remove_pictures_from_document", "Remove pictures from a document: all of them (default), the ones in one section, or one named picture. Use before adding new ones when the user wants pictures REPLACED (or call illustrate_document with replace=true).",
            {"type": "object", "properties": {**name_prop, "section": {"type": "string", "description": "Only the pictures in this section (heading text)."}, "image": {"type": "string", "description": "Only this picture (exact name from the document)."}}}, "documents", _remove_pictures)
        add("add_picture_to_document", "Put pictures that ALREADY exist into the document (for example ones just made with generate_image). Without an image name it uses the newest pictures the document does not show yet; count says how many. Without a section it spreads them over sections that have no picture.",
            {"type": "object", "properties": {**name_prop, "image": {"type": "string", "description": "Exact picture name from list_images (optional)."},
                                               "count": {"type": "integer", "description": "How many of the newest unused pictures to place (default 1)."},
                                               "section": {"type": "string", "description": "Heading of the section to put them in (optional)."}}}, "documents", _add_picture)
        add("illustrate_document", "Add AI-generated pictures to an existing document or slide deck: one call places the requested number of pictures in its sections. Slow (about a minute per picture). Use it whenever the user wants pictures or illustrations in a document.",
            {"type": "object", "properties": {**name_prop, "count": {"type": "integer", "description": "How many pictures to add. Omit it to give EVERY section that has no picture one (the right choice for 'add images to the document' or 'one for each place')."},
                                               "source": {"type": "string", "enum": ["auto", "web", "generate"], "description": "auto (default): for each section it chooses a real picture from the web (real places, people, animals, objects) or a generated illustration (concepts, imagined scenes). web = real photos only (use for 'find images online'); generate = drawn only."},
                                               "replace": {"type": "boolean", "description": "true = remove ALL the document's current pictures first, then give every section a new one. Use for 'replace the images'."}}}, "images", lambda args: _illustrate_document(self, args))
        self.builtin["illustrate_document"].timeout = 60 * 60
        add("remember", "Save one short fact worth keeping across conversations: a lasting preference, a name, a project detail, a standing instruction. Use when the user asks you to remember something.",
            {"type": "object", "properties": {"fact": {"type": "string", "description": "One short sentence, e.g. 'The user prefers replies in Hindi.'"}}, "required": ["fact"]}, "memory", _remember)
        add("recall", "Look up facts saved from EARLIER conversations, about the user or a topic. What was said earlier in this conversation is already in the messages above: read it there instead of calling this.",
            {"type": "object", "properties": {"query": {"type": "string", "description": "Keywords; empty lists the most recent facts."}}}, "memory", _recall)
        add("forget", "Delete a remembered fact (give some of its words).",
            {"type": "object", "properties": {"fact": {"type": "string", "description": "Words from the fact to delete."}}, "required": ["fact"]}, "memory", _forget)
        add("list_images", "List the pictures that were made earlier (names and what they show), so they can be reused in a document or reply.",
            {"type": "object", "properties": {}}, "images", _list_images)
        add("generate_image", "Make an IMAGINED picture from a text description with a local image model: stylised art, concepts, scenes that do not exist, a consistent illustration style. For how a real, named thing looks use find_image instead. Slow (about a minute) and it needs the GPU, so use it only when a picture is wanted. Returns an image name to place in documents with ![caption](img:NAME).",
            {"type": "object", "properties": {"prompt": {"type": "string", "description": "Detailed description in English: subject, setting, style, lighting. No text or letters in the picture."},
                                               "size": {"type": "string", "enum": ["square", "landscape", "portrait", "wide"], "description": "square (default), landscape (documents and slides), portrait or wide."}},
             "required": ["prompt"]}, "images", lambda args: _generate_image(self, args))
        self.builtin["generate_image"].timeout = 20 * 60
        add("list_files", "List files and folders in the connected local folder.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Relative path inside the folder (default: the root)."}}},
            "files", self._files_list)
        add("read_file", "Read a text file from the connected local folder.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Relative path of the file."},
                                               "max_chars": {"type": "integer", "description": "Maximum characters to return (default 8000)."}},
             "required": ["path"]}, "files", self._files_read)

    async def _files_list(self, args):
        return await self.files.list_files(args)

    async def _files_read(self, args):
        return await self.files.read_file(args)

    def _mcp_config(self) -> dict:
        raw = _read_json(MCP_CONFIG, {})
        servers = raw.get("servers") or raw.get("mcpServers") or {}
        return {k: v for k, v in servers.items() if isinstance(v, dict) and (v.get("command") or v.get("url"))}

    async def start(self):
        await self.reload_mcp()

    async def stop(self):
        await asyncio.gather(*(s.stop() for s in self.mcp.values()), return_exceptions=True)

    async def reload_mcp(self):
        await self.stop()
        self.mcp = {}
        for name, cfg in self._mcp_config().items():
            srv = McpServer(_safe_name(name), cfg)
            self.mcp[srv.name] = srv
            if cfg.get("enabled", True):
                srv.start()
            else:
                srv.status = "disabled"
        waiters = [s.ready.wait() for s in self.mcp.values() if s.status == "starting"]
        if waiters:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.gather(*waiters), 20)

    # -- config mutation (always via the same file the user can edit by hand)
    def _write_mcp(self, mutate):
        raw = _read_json(MCP_CONFIG, {"servers": {}})
        key = "mcpServers" if "mcpServers" in raw and "servers" not in raw else "servers"
        servers = raw.setdefault(key, {})
        mutate(servers)
        _atomic_write(MCP_CONFIG, raw)

    async def set_server_enabled(self, name: str, enabled: bool):
        if name not in self.mcp:
            raise ToolError("Unknown server.")
        self._write_mcp(lambda s: s[name].__setitem__("enabled", enabled) if name in s else None)
        await self.reload_mcp()

    async def add_http_server(self, name: str, url: str):
        u = urlparse(url)
        name = _safe_name(name.strip())
        if not name or u.scheme not in ("http", "https") or not u.hostname:
            raise ToolError("Give a name and an http(s) URL.")
        self._write_mcp(lambda s: s.__setitem__(name, {"url": url, "enabled": True}))
        await self.reload_mcp()

    async def add_local_server(self, name: str, command: str, args: str):
        """A local (stdio) server runs a command as you, so it is saved switched OFF: nothing runs until you turn it on (the UI asks first)."""
        name = _safe_name(name.strip())
        command = command.strip()
        if not name or not command or "\n" in command:
            raise ToolError("Give a name and the command that starts the server.")
        try:
            argv = shlex.split(args or "")
        except ValueError:
            raise ToolError("The arguments have an unclosed quote.")
        if name in self._mcp_config():
            raise ToolError(f"A server called {name} already exists.")
        self._write_mcp(lambda s: s.__setitem__(name, {"command": command, "args": argv, "enabled": False}))
        await self.reload_mcp()

    async def remove_server(self, name: str):
        if name not in self._mcp_config():
            raise ToolError("Unknown server.")
        self._write_mcp(lambda s: s.pop(name, None))
        await self.reload_mcp()

    def set_files_root(self, path: str | None):
        if path:
            p = Path(path).expanduser().resolve()
            if not p.is_dir():
                raise ToolError("That folder does not exist.")
            self.settings["files_root"] = str(p)
        else:
            self.settings.pop("files_root", None)
        self.files = FilesConnector(self.settings.get("files_root"))
        _atomic_write(TOOLS_CONFIG, self.settings)

    # -- queries
    def all_tools(self) -> dict[str, Tool]:
        tools = dict(self.builtin)
        for srv in self.mcp.values():
            if srv.status != "connected":
                continue
            for t in srv.tools:
                name = f"{srv.name}__{_safe_name(t['name'])}"[:64]
                tools[name] = Tool(name, t["description"], t["schema"], f"mcp:{srv.name}",
                                   self._mcp_handler(srv, t["name"]))
        enabled = self.skills.enabled()
        if enabled:
            tools["use_skill"] = Tool("use_skill", "Load the full instructions of a skill. Call this first whenever the task matches one of the available skills, then follow what it says.",
                                      {"type": "object", "properties": {"name": {"type": "string", "enum": [x["name"] for x in enabled], "description": "The skill to load."}},
                                       "required": ["name"]}, "skills", self._use_skill)
            if any(x["files"] for x in enabled):
                tools["read_skill_file"] = Tool("read_skill_file", "Read a reference file that a skill mentions (paths are relative to the skill).",
                                                {"type": "object", "properties": {"name": {"type": "string", "enum": [x["name"] for x in enabled if x["files"]]},
                                                                                   "path": {"type": "string", "description": "e.g. references/tools.md"}},
                                                 "required": ["name", "path"]}, "skills", self._read_skill_file)
        return tools

    async def _use_skill(self, args):
        name = str(args.get("name", ""))
        try:
            sk = self.skills.get(name)
        except SkillError as e:
            names = ", ".join(x["name"] for x in self.skills.enabled())
            raise ToolError(f"There is no skill called '{name}' ({e}) Skills you can load: {names}. If none fits, answer without one.") from e
        if not sk["enabled"]:
            raise ToolError(f"Skill '{name}' is turned off.")
        extra = f"\n\nReference files (read with read_skill_file): {', '.join(sk['files'])}" if sk["files"] else ""
        return ToolResult(f"# Skill: {sk['name']}\n\n{sk['body']}{extra}")

    async def _read_skill_file(self, args):
        try:
            return ToolResult(self.skills.read_file(str(args.get("name", "")), str(args.get("path", ""))))
        except SkillError as e:
            raise ToolError(str(e)) from e

    def _mcp_handler(self, srv: McpServer, tool: str):
        async def handler(args):
            return await srv.call(tool, args)
        return handler

    def describe(self) -> dict:
        tools = self.all_tools()
        groups = []
        for gid, meta in GROUPS.items():
            status, error = "ready", None
            extra = {}
            if gid == "images" and not self.imagegen.available():
                status, error = "needs_setup", "Image model not installed: missing " + ", ".join(self.imagegen.missing()[:3])
            if gid == "files" and not self.files.root:
                status, error = "needs_setup", "Choose a folder to enable."
            if gid == "skills":
                enabled = self.skills.enabled()
                extra["catalog"] = [{"name": x["name"], "description": x["description"]} for x in enabled]
                if not enabled:
                    status, error = "needs_setup", "Install or enable a skill first."
            groups.append({"id": gid, "kind": "builtin", **meta, **extra, "status": status, "error": error,
                           "tools": [self._tool_json(t) for t in tools.values() if t.group == gid]})
        for srv in self.mcp.values():
            groups.append({"id": f"mcp:{srv.name}", "kind": "mcp", "label": srv.name, "icon": "plug",
                           "description": ("Remote MCP server " + srv.cfg["url"]) if srv.kind == "http"
                           else "Local MCP server: " + " ".join([srv.cfg.get("command", ""), *map(str, srv.cfg.get("args", []))])[:120],
                           "transport": srv.kind, "status": srv.status, "error": srv.error, "default_approval": "ask",
                           "enabled": srv.cfg.get("enabled", True),
                           "tools": [self._tool_json(t) for t in tools.values() if t.group == f"mcp:{srv.name}"]})
        return {"groups": groups, "files_root": str(self.files.root) if self.files.root else None}

    @staticmethod
    def _tool_json(t: Tool) -> dict:
        return {"name": t.name, "description": t.description, "schema": t.schema}

    async def call(self, name: str, args: dict, active_document: str | None = None, only_sections: list | None = None, turn: str | None = None) -> dict:
        docs.TURN.set(turn)
        ACTIVE_DOC.set(active_document)
        ONLY_SECTIONS.set([str(x) for x in (only_sections or [])][:12])
        tool = self.all_tools().get(name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool '{name}'.", "ms": 0}
        if not isinstance(args, dict):
            return {"ok": False, "error": "Arguments must be a JSON object.", "ms": 0}
        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(tool.handler(args), tool.timeout)
            return {"ok": True, "text": _clip(res.text, MAX_RESULT_CHARS), "sources": res.sources[:10], "files": res.files, "images": res.images,
                    "ms": round((time.perf_counter() - t0) * 1000)}
        except ToolError as e:
            return {"ok": False, "error": str(e), "ms": round((time.perf_counter() - t0) * 1000)}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Tool timed out.", "ms": tool.timeout * 1000}
        except Exception as e:  # a buggy handler must not take the server down
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}", "ms": round((time.perf_counter() - t0) * 1000)}
