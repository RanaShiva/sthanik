"""The one place that decides how the app reaches the internet.

Only these things ever leave the machine, and only when you or the model asks for them: web, news and image searches (their query text goes
to Bing and Yahoo, and for a web search also to Wikipedia, to Stack Exchange for a programming question and to Marginalia when the others fail;
LFM3_SEARCH_EXTRAS=0 limits it to Bing and Yahoo), the top pages of a web search, which the app opens itself to read the passages that answer the
question (LFM3_SEARCH_READ=0 stops that), pages and pictures the model fetches (the address and your IP go to that site), skill installs (GitHub) and model downloads
(Hugging Face). Chats, documents, pictures and memory are never sent anywhere.

Set LFM3_PROXY (for example http://127.0.0.1:8118, a local HTTP proxy such as Privoxy in front of Tor) to send all of that through a proxy so the
sites see its address instead of yours. Without it the sites see your own IP.
"""

from __future__ import annotations

import os


def proxy() -> str | None:
    return (os.environ.get("LFM3_PROXY") or "").strip() or None
