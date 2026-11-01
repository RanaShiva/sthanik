"""Pictures from the web: search (Bing images) and a careful download into the picture library.

Web pages and image hosts are untrusted. A download only talks to public addresses (checked again after every redirect), is
size-limited, must really be an image (decoded and re-encoded with Pillow, so nothing but pixels is kept), and never uses SVG.
Each saved picture records where it came from, so documents can credit it.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import re
import socket
import time
import uuid
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageOps

import images
import outbound
import safebrowsing

UA = "Mozilla/5.0 (X11; Linux x86_64) Sthanik/1.0"
MAX_BYTES = 10_000_000
MAX_PIXELS = 40_000_000
MAX_SIDE = 2400
MIN_SIDE = 280  # smaller results are thumbnails or icons
TTL = 30 * 60
CANDIDATES: dict[str, dict] = {}  # search results the UI may preview or save, by id


class WebImageError(Exception):
    """A problem the model or user can act on."""


async def _check_public_host(host: str):
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise WebImageError(f"Could not resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise WebImageError(f"Blocked: {host} is not a public address.")


async def _fetch(url: str, referer: str | None = None) -> tuple[bytes, str]:
    """Download an image URL: public hosts only, redirects re-checked, at most MAX_BYTES, must announce an image type.
    Network trouble (timeouts, resets, bad TLS) is a WebImageError like any other failed download, so callers just try the next picture."""
    try:
        return await _fetch_once(url, referer)
    except httpx.HTTPError as e:
        raise WebImageError(f"Could not download from {_domain(url) or 'that site'} ({type(e).__name__})") from e


async def _fetch_once(url: str, referer: str | None = None) -> tuple[bytes, str]:
    headers = {"User-Agent": UA, "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.5"}
    if referer:
        headers["Referer"] = referer
    async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(20, connect=8), headers=headers, proxy=outbound.proxy()) as client:
        for _ in range(5):
            u = urlparse(url)
            if u.scheme not in ("http", "https") or not u.hostname:
                raise WebImageError("Only http(s) addresses are supported.")
            await _check_public_host(u.hostname)
            async with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise WebImageError(f"HTTP {r.status_code} from {u.hostname}")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if not ctype.startswith("image/") or "svg" in ctype:
                    raise WebImageError(f"Not a picture ({ctype or 'unknown type'}).")
                if int(r.headers.get("content-length") or 0) > MAX_BYTES:
                    raise WebImageError("The picture is too large.")
                data = b""
                async for chunk in r.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_BYTES:
                        raise WebImageError("The picture is too large.")
                return data, ctype
        raise WebImageError("Too many redirects.")


def _reencode(data: bytes, max_side: int = MAX_SIDE) -> tuple[bytes, str, tuple[int, int]]:
    """Decode with Pillow and write fresh pixels: JPEG for photos, PNG when there is transparency."""
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_PIXELS:
            raise WebImageError("The picture has too many pixels.")
        im.load()
    except WebImageError:
        raise
    except Exception as e:  # noqa: BLE001 - corrupt or unsupported data
        raise WebImageError("The file is not a readable picture.") from e
    im = ImageOps.exif_transpose(im)
    im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "png", im.size
    im.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue(), "jpg", im.size


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _prune():
    now = time.time()
    for k in [k for k, v in CANDIDATES.items() if now - v["t"] > TTL]:
        CANDIDATES.pop(k, None)
    while len(CANDIDATES) > 300:
        CANDIDATES.pop(next(iter(CANDIDATES)))


# Slide decks, document hosts and watermarked stock sites make poor illustrations; reference and travel sources make good ones.
AVOID_DOMAINS = ("slideshare", "scribd", "pinterest", "shutterstock", "alamy", "dreamstime", "istockphoto", "gettyimages", "depositphotos", "123rf",
                 "adobestock", "vecteezy", "freepik", "facebook", "instagram", "tiktok", "twimg", "fity.club", "wallpaper", "pngtree", "clipartmax", "cleanpng")
AVOID_TITLE = re.compile(r"\b(pptx?|powerpoint|slides?|template|wallpapers?|clip ?art|vector|stock photo|png|infographic|packages?|book(ing)? now|deals?|offers?|discounts?|get upto|starting (at|from|@)|hotels? (in|near)|cheap|sale)\b|[₹$€£]|\d+\s?% off", re.I)
PREFER_DOMAINS = ("wikipedia", "wikimedia", "unesco", "britannica", "nationalgeographic", "smithsonian", "archive.org", "gov.in", ".gov", ".edu", "incredibleindia", "lonelyplanet", "tripadvisor")
STOP = {"the", "of", "and", "in", "a", "an", "for", "to", "on", "at", "with", "photo", "image", "picture", "pictures", "images", "from", "india"}


def _rank(cands: list[dict], query: str) -> list[dict]:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2 and w not in STOP]
    scored = []
    for c in cands:
        host = (c["source"] or "").lower()
        text = f"{c['title']} {c['page']}".lower()
        if any(d in host or d in (c["image"] or "").lower() for d in AVOID_DOMAINS) or AVOID_TITLE.search(c["title"]):
            continue
        overlap = sum(1 for w in words if w in text)
        area = min((c["width"] or 800) * (c["height"] or 600), 8_000_000) / 8_000_000
        ratio = (c["width"] / c["height"]) if c["width"] and c["height"] else 1.4
        shape = 1.0 if 1.2 <= ratio <= 2.3 else 0.85 if 0.9 <= ratio < 1.2 else 0.6
        boost = 1.5 if any(d in host for d in PREFER_DOMAINS) else 1.0
        scored.append((overlap, (0.35 + area) * shape * boost, c))
    if len([x for x in scored if x[0] > 0]) >= 3:  # enough on-topic results: drop the ones that do not mention the subject
        scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: (-(x[0] > 0), -x[0] * 0.3 - x[1]))
    return [c for _o, _s, c in scored] or cands


async def search(query: str, n: int = 8) -> list[dict]:
    """Image results for `query`, biggest first. Each has an id the UI can preview and save."""
    query = " ".join((query or "").split())[:200]
    if not query:
        raise WebImageError("Say what to look for.")
    try:
        rows = await asyncio.to_thread(safebrowsing.search, "images", query, max(n * 3, 12))
    except Exception as e:  # noqa: BLE001 - ddgs has its own exception hierarchy
        raise WebImageError(f"The image search failed: {e}") from e
    out, seen = [], set()
    for r in rows:
        url = str(r.get("image") or "")
        if not url or url in seen or url.lower().split("?")[0].endswith((".svg", ".gif")):
            continue
        w, h = int(r.get("width") or 0), int(r.get("height") or 0)
        if w and h and min(w, h) < MIN_SIDE:
            continue
        seen.add(url)
        cid = uuid.uuid4().hex[:12]
        c = {"id": cid, "title": " ".join(str(r.get("title") or query).split())[:140], "image": url, "thumbnail": str(r.get("thumbnail") or ""),
             "page": str(r.get("url") or ""), "source": _domain(str(r.get("url") or url)), "width": w, "height": h, "query": query, "t": time.time()}
        CANDIDATES[cid] = c
        out.append(c)
    _prune()
    out = _rank(out, query)
    return [{k: v for k, v in c.items() if k not in ("t", "image", "thumbnail")} for c in out[:n]]


async def thumbnail(cid: str) -> tuple[bytes, str]:
    """A small JPEG preview of a search result, fetched through the same safe downloader (the browser never contacts the host)."""
    c = CANDIDATES.get(cid)
    if not c:
        raise WebImageError("That search result has expired. Search again.")
    last = None
    for url in (c["thumbnail"], c["image"]):
        if not url:
            continue
        try:
            data, _ = await _fetch(url, c["page"] or None)
            jpg, _ext, _size = await asyncio.to_thread(_reencode, data, 360)
            return jpg, "image/jpeg"
        except WebImageError as e:
            last = e
    raise last or WebImageError("No preview available.")


async def save(cid: str) -> dict:
    """Download a search result into the picture library (full size first, the thumbnail if that host refuses)."""
    c = CANDIDATES.get(cid)
    if not c:
        raise WebImageError("That search result has expired. Search again.")
    last = None
    for url in (c["image"], c["thumbnail"]):
        if not url:
            continue
        try:
            data, _ = await _fetch(url, c["page"] or None)
            blob, ext, size = await asyncio.to_thread(_reencode, data)
            if min(size) < 160:
                raise WebImageError("The picture is too small.")
            return images.save_image(blob, c["title"] or c["query"], ext, {
                "kind": "image", "origin": "web", "prompt": c["title"], "credit": c["source"], "source_url": c["page"], "image_url": c["image"],
                "size_px": list(size), "query": c["query"]})
        except WebImageError as e:
            last = e
    raise last or WebImageError("Could not download that picture.")


async def find(query: str, count: int = 1) -> tuple[list[dict], list[str]]:
    """Search and save the first `count` pictures that download cleanly. Returns (saved infos, problems)."""
    count = max(1, min(int(count or 1), 4))
    found = await search(query, n=max(8, count * 4))
    saved, problems = [], []
    for c in found:
        if len(saved) >= count:
            break
        try:
            saved.append(await save(c["id"]))
        except WebImageError as e:
            problems.append(f"{c['source'] or 'a site'}: {e}")
            if len(problems) >= 6:
                break
    return saved, problems
