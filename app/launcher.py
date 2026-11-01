"""Start Sthanik: the program the packaged builds run, and what `./run.sh` runs from a source checkout.

    sthanik                  start the server and open it in the browser (a second start just opens the browser)
    sthanik --no-browser     start the server only
    sthanik --setup          install the engine and models from the terminal (no browser needed), then exit
    sthanik --selftest       start, check that the program works as built (pages, hardware, documents), then exit with 0 or 1
    sthanik --data-dir DIR   keep chats, models and settings in DIR (also: STHANIK_HOME)
    sthanik --port N         listen on 127.0.0.1:N (default 7860; another free port is used if 7860 belongs to some other program)

The settings it reads must be in the environment before the server module is imported, so everything here happens before `import server`.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from version import VERSION


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_sthanik(port: int) -> bool:
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=2)
        return r.status_code == 200 and "llama" in r.json()
    except (httpx.HTTPError, ValueError):
        return False


def _wait_until_up(port: int, seconds: float = 60) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if _is_sthanik(port):
            return True
        time.sleep(0.3)
    return False


def _clean_child_environment():
    """A packaged Linux build starts with LD_LIBRARY_PATH pointing at its own bundled libraries, which are older than the system's. Every
    program started from here inherits that: the browser opener (kde-open, xdg-open) then died with "GLIBCXX_3.4.32 not found" and
    nothing opened. This process has loaded what it needs, so give the programs it starts the value it was started with."""
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("linux"):
        return
    original = os.environ.get("LD_LIBRARY_PATH_ORIG")  # set by the PyInstaller bootloader when the variable existed before it
    if original is None:
        os.environ.pop("LD_LIBRARY_PATH", None)
    else:
        os.environ["LD_LIBRARY_PATH"] = original


def _attach_streams():
    """A windowless build has no stdout/stderr, and uvicorn's logging fails on None: send them to a log file in the data folder."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    import paths
    log = paths.HOME / "data" / "server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "a", buffering=1, encoding="utf-8")  # noqa: SIM115 - lives as long as the process
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def run_setup(args) -> int:
    """Install from the terminal: the same job the Setup page runs, with progress lines."""
    import asyncio

    import installer

    async def go() -> int:
        setup = installer.Setup()
        # --quant Q8_0 for every part, or per part: text=BF16,vl=Q8_0,diffusion=Q6_K
        quant, quants = "", {}
        for item in (args.quant or "").split(","):
            if "=" in item:
                k, _, v = item.partition("=")
                quants[k.strip()] = v.strip()
            elif item.strip():
                quant = item.strip()
        models = {}
        for item in args.own or []:  # PART=MODEL.gguf  or  PART=MODEL.gguf::MMPROJ.gguf
            part, _, files = item.partition("=")
            model, _, mmproj = files.partition("::")
            models[part.strip()] = {"model": model, "mmproj": mmproj or None}
        own = {"engine": args.llama, "models": models}
        opts = {"components": args.parts.split(",") if args.parts else ["text"], "quant": quant, "quants": quants, "image": args.pictures, "own": own if (args.llama or models) else {}}
        p = await installer.plan(opts)
        hw = p["hardware"]
        print(f"System: {hw['os']} {hw['arch']} | {hw['cpu']} | {(hw['ram_mb'] or 0) // 1024} GB RAM | " + (", ".join(g["name"] for g in hw["gpus"]) or "no graphics card"))
        print(f"Precision: {p['quant']} (recommended {p['recommended']}) | engine: {p['runtime']['candidates'][0]['backend'] if p['runtime']['candidates'] else '?'} | data folder: {installer.ROOT}")
        setup.start(opts)
        shown = ""
        while setup.running():
            line = " | ".join(f"{s['label']}: {s['state']}" + (f" {100 * s['done'] // s['total']}%" if s["state"] == "running" and s["total"] else "") for s in setup.job["steps"] if s["state"] != "pending")
            if line != shown:
                print(line, flush=True)
                shown = line
            await asyncio.sleep(1)
        if setup.job["state"] != "done":
            print(f"Setup {setup.job['state']}: {setup.job['error']}", file=sys.stderr)
            return 1
        print("Setup finished.")
        return 0

    return asyncio.run(go())


def run_selftest(server_module, port: int) -> int:
    """Start the real server on a free port and check what a user's first minutes depend on. Prints one line per check."""
    import httpx
    import uvicorn

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = ""):
        print(f"{'ok  ' if ok else 'FAIL'} {name}{(': ' + detail) if detail and not ok else ''}", flush=True)
        if not ok:
            failures.append(name)

    config = uvicorn.Config(server_module.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        check("server starts", _wait_until_up(port, 60))
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=120) as c:
            r = c.get("/")
            check("page is served", r.status_code == 200 and "Sthanik" in r.text, f"{r.status_code}")
            for asset in ("/js/main.js", "/js/pages/setup.js", "/css/app.css", "/vendor/marked.js", "/vendor/mermaid/mermaid.min.js", "/sthanik.svg"):
                a = c.get(asset)
                check(f"static file {asset}", a.status_code == 200 and len(a.content) > 100, f"{a.status_code}")
            st = c.get("/api/status").json()
            check("status", "llama" in st and "setup" in st, str(list(st)))
            hw = c.get("/api/hardware").json()
            check("hardware detected", bool(hw["detected"]["os"]) and bool(hw["detected"]["cpu"]), str(hw["detected"]))
            print(f"     {hw['detected']['os']} {hw['detected']['arch']} | {hw['detected']['cpu']} | {hw['detected']['ram_mb']} MB RAM | gpus: {[g['name'] for g in hw['detected']['gpus']]}")
            import hardware
            rt = hardware.choose_runtime(hw["detected"])
            check("engine choice", bool(rt) and rt[-1]["backend"] in ("cpu", "metal"), str(rt))
            import installer
            check("runtime manifest", all(installer._asset(c_["asset"]) for c_ in rt), "an asset the choice names is not in the manifest")
            skills = c.get("/api/skills").json()
            check("built-in skills", len(skills.get("skills", skills)) >= 5, str(skills)[:200])
            for fmt, body in (("html", "# Title\n\nHello **world**.\n\n- one\n- two\n"), ("docx", "# Title\n\nHello world.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"),
                              ("pptx", "## Slide one\n\n- point\n- another\n\n## Slide two\n\n- more\n"), ("xlsx", "Name,Value\nA,1\nB,2\n")):
                r = c.post("/api/documents/export", json={"title": f"selftest {fmt}", "markdown": body, "format": fmt})
                ok = r.status_code == 200
                detail = r.text[:200]
                if ok:
                    import documents
                    f = documents.DOCS_DIR / r.json()["name"]
                    data = f.read_bytes() if f.is_file() else b""
                    ok = len(data) > 500 and (data[:2] == b"PK" if fmt != "html" else b"<html" in data[:400].lower() or b"<!doctype" in data[:100].lower())
                    detail = f"{f.name}: {len(data)} bytes"
                check(f"create a {fmt} file", ok, detail)
            for module in ("ddgs.engines.yahoo", "ddgs.engines.bing", "mcp", "lxml.etree", "PIL.Image", "yaml", "bs4", "markdown_it"):
                try:
                    __import__(module)
                    check(f"import {module}", True)
                except Exception as e:  # noqa: BLE001
                    check(f"import {module}", False, repr(e))
            r = c.get("/api/setup/status")
            check("setup status", r.status_code == 200, r.text[:200])
            sh = c.get("/api/search/health")
            check("search health is reported", sh.status_code == 200 and isinstance(sh.json().get("sources"), dict), sh.text[:200])
            found = c.get("/api/setup/detect")
            check("setup lists the engines and model files already on this computer", found.status_code == 200 and isinstance(found.json().get("engines"), list) and isinstance(found.json().get("files"), list), found.text[:200])
            # bad input must be refused with a clear 4xx, never a server error, and must never reach the file system
            for path in ("/api/memory", "/api/documents/export", "/api/tools/call", "/api/skills/match", "/api/projects", "/api/schedules", "/api/setup/start"):
                bad = [c.post(path, content=b"{nope", headers={"Content-Type": "application/json"}), c.post(path, content=b"", headers={"Content-Type": "application/json"}), c.post(path, json=[1, 2])]
                check(f"malformed body to {path} is refused", all(400 <= r.status_code < 500 for r in bad), str([r.status_code for r in bad]))
            for path in ("/api/documents/%00", "/api/chats/%00"):
                check(f"NUL character in {path} is refused", c.get(path).status_code == 400)
            for path in ("/api/documents/..%2f..%2fconfig%2fx", "/api/chats/..%2f..%2fconfig%2fx", "/api/images/..%2f..%2fconfig%2fx", "/api/skills/docx/file?path=..%2f..%2f..%2f..%2fetc%2fpasswd"):
                r = c.get(path)
                check(f"path traversal {path[:45]} is refused", r.status_code in (400, 404, 422) and "root:" not in r.text, str(r.status_code))
            r = c.post("/api/tools/call", json={"name": "calculator", "arguments": {"expression": "__import__('os').getcwd()"}})
            check("the calculator does not run code", r.status_code < 500 and r.json().get("ok") is False, r.text[:160])
            r = c.post("/api/tools/call", json={"name": "calculator", "arguments": {"expression": "9**9**9**9"}})
            check("the calculator does not hang on huge powers", r.status_code < 500)
            if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
                leaked = [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if p and str(getattr(sys, "_MEIPASS", "\0")) in p]
                check("programs started from here do not inherit the bundled libraries", not leaked, str(leaked))
            r = c.post("/api/tools/call", json={"name": "use_skill", "arguments": {"name": "no-such-skill"}})
            check("an unknown skill answers with the skills that exist", "docx" in r.text and "mermaid" in r.text, r.text[:160])
    finally:
        server.should_exit = True
        thread.join(15)
    print("selftest ok" if not failures else f"selftest FAILED: {', '.join(failures)}")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sthanik", description="Sthanik: a local AI studio around Liquid AI's LFM3 models.")
    ap.add_argument("--port", type=int, help="port to listen on (default 7860)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--data-dir", help="where chats, models and settings are kept")
    ap.add_argument("--setup", action="store_true", help="install the engine and models from the terminal, then exit")
    ap.add_argument("--parts", help="with --setup: comma-separated parts to install (text,vl,omni,audio; default text)")
    ap.add_argument("--quant", help="with --setup: the precision, for every part (Q8_0) or per part (text=BF16,vl=Q8_0,diffusion=Q6_K); default: what suits this computer")
    ap.add_argument("--llama", help="with --setup: use this llama-server (or the folder it is in) instead of downloading one")
    ap.add_argument("--own", action="append", help="with --setup: use your own weights for a part: PART=MODEL.gguf, or PART=MODEL.gguf::MMPROJ.gguf (repeat for more parts)")
    ap.add_argument("--pictures", action="store_true", help="with --setup: also install picture drawing")
    ap.add_argument("--selftest", action="store_true", help="check that this build works, then exit")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(f"Sthanik {VERSION}")
        return 0
    _clean_child_environment()
    if args.data_dir:
        os.environ["STHANIK_HOME"] = str(Path(args.data_dir).expanduser().resolve())
    if args.selftest and not os.environ.get("STHANIK_HOME"):
        import tempfile
        os.environ["STHANIK_HOME"] = tempfile.mkdtemp(prefix="sthanik-selftest-")  # never touch a real installation
    _attach_streams()

    if args.setup:
        return run_setup(args)

    port = args.port or int(os.environ.get("LFM3_UI_PORT") or 7860)
    if args.selftest:
        port = _free_port()
    elif _port_in_use(port):
        if _is_sthanik(port):
            print(f"Sthanik is already running at http://127.0.0.1:{port}")
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{port}")
            return 0
        if not args.port:
            port = _free_port()  # 7860 belongs to another program
    os.environ["LFM3_UI_PORT"] = str(port)

    import server  # reads LFM3_UI_PORT and STHANIK_HOME while it loads
    if args.selftest:
        return run_selftest(server, port)

    import uvicorn
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(config)
    server.app.state.shutdown = lambda: setattr(uv, "should_exit", True)  # the Quit button
    url = f"http://127.0.0.1:{port}"
    print(f"Sthanik {VERSION} is running at {url}  (data in {server.ROOT}). Close this window or press Ctrl+C to stop it.", flush=True)
    if not args.no_browser:
        threading.Thread(target=lambda: _wait_until_up(port) and webbrowser.open(url), daemon=True).start()
    uv.run()
    return 0


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        import multiprocessing
        multiprocessing.freeze_support()
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))  # `python app/launcher.py`: the modules sit beside this file
    sys.exit(main())
