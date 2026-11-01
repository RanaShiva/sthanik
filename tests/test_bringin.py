"""Tests for choosing a precision per part and for using an engine and weights the user already has. Offline.
Run: .venv/bin/python -I tests/test_bringin.py"""

import asyncio
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

_home = tempfile.mkdtemp(prefix="sthanik-test-")
os.environ["STHANIK_HOME"] = _home  # before the modules read it

import bringin  # noqa: E402
import hardware  # noqa: E402
import installer  # noqa: E402
import registry  # noqa: E402

HOME = Path(_home)


def gguf(path: Path, arch="lfm3", name="Test model", extra=b"") -> Path:
    """The smallest file that reads as a GGUF: magic, version 3, no tensors, two string keys."""
    def s(text):
        b = text.encode()
        return struct.pack("<Q", len(b)) + b
    body = s("general.architecture") + struct.pack("<I", 8) + s(arch) + s("general.name") + struct.pack("<I", 8) + s(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 2) + body + extra)
    return path


FAKE_SERVER = '''#!{python}
import http.server, json, sys
a = sys.argv[1:]
if "--version" in a:
    print("version: 0.8.0-dev (build 11979, commit abc1234)\\nbuilt with GNU for Linux"); sys.exit(0)
if "--list-devices" in a:
    print("Available devices:\\n  CPU: Test CPU (1000 MiB, 500 MiB free)"); sys.exit(0)
if {broken}:
    print("llama_model_load: error loading model: unknown model architecture: 'lfm3'", file=sys.stderr); sys.exit(1)
port = int(a[a.index("--port") + 1]); key = a[a.index("--api-key") + 1]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
        ok = self.headers.get("Authorization") == "Bearer " + key
        self.send_response(200 if ok else 401); self.end_headers(); self.wfile.write(json.dumps({{"content": " Paris"}}).encode())
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def fake_engine(broken=False) -> Path:
    d = Path(tempfile.mkdtemp(prefix="engine-"))
    server = d / "llama-server"
    server.write_text(FAKE_SERVER.format(python=sys.executable, broken=broken))
    server.chmod(0o755)
    return server


LISTING = {
    "LiquidAI/LFM3-VL-2B-GGUF": ["LFM3-VL-2B-BF16.gguf", "LFM3-VL-2B-Q8_0.gguf", "LFM3-VL-2B-Q4_K_M.gguf", "LFM3-VL-2B-Q6_K.gguf", "mmproj-LFM3-VL-2B-BF16.gguf", "mmproj-LFM3-VL-2B-Q8_0.gguf", "README.md"],
    "LiquidAI/LFM3-1.7B-GGUF": ["LFM3-1.7B-BF16.gguf", "LFM3-1.7B-Q8_0.gguf", "LFM3-1.7B-Q4_K_M.gguf"],
    "LiquidAI/LFM3-Omni-2B-GGUF": ["LFM3-Omni-2B-BF16.gguf", "LFM3-Omni-2B-Q4_K_M.gguf", "mmproj-LFM3-Omni-2B-BF16.gguf", "mmproj-LFM3-Omni-2B-Q4_K_M.gguf"],
    "LiquidAI/LFM3-Audio-2B-GGUF": ["LFM3-Audio-2B-BF16.gguf", "mmproj-LFM3-Audio-2B-BF16.gguf"],
    "leejet/Z-Image-Turbo-GGUF": ["z_image_turbo-Q4_K.gguf", "z_image_turbo-Q8_0.gguf", "z_image_turbo-Q2_K.gguf"],
    "unsloth/Qwen3-4B-Instruct-2507-GGUF": ["Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "Qwen3-4B-Instruct-2507-Q8_0.gguf", "Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf", "Qwen3-4B-Instruct-2507-IQ4_XS.gguf"],
    "Comfy-Org/z_image_turbo": ["split_files/vae/ae.safetensors"],
}


async def _fake_hf_files(repo, folder=""):
    names = LISTING[repo]
    return {n: {"size": 1000 + len(n), "sha256": "0" * 64} for n in names if (n.rpartition("/")[0] == folder)}


installer.hf_files = _fake_hf_files


def test_gguf_header_is_read_and_checked():
    d = Path(tempfile.mkdtemp())
    assert bringin.gguf_info(gguf(d / "m.gguf")) == {"arch": "lfm3", "name": "Test model"}
    assert bringin.check_model_file(gguf(d / "p.gguf", arch="clip"), True)["arch"] == "clip"
    for bad, projector, word in ((gguf(d / "p2.gguf", arch="clip"), False, "projector"), (gguf(d / "m2.gguf"), True, "not a projector")):
        try:
            bringin.check_model_file(bad, projector)
        except bringin.BringInError as e:
            assert word in str(e), str(e)
        else:
            raise AssertionError("the wrong kind of file was accepted")
    (d / "x.gguf").write_bytes(b"not a model at all")
    (d / "cut.gguf").write_bytes(b"GGUF\x03\x00")
    for name in ("x.gguf", "cut.gguf", "missing.gguf"):
        try:
            bringin.check_model_file(d / name, False)
        except bringin.BringInError:
            pass
        else:
            raise AssertionError(name + " was accepted")


def test_precision_is_offered_and_chosen_for_each_part():
    p = asyncio.run(installer.plan({"quants": {"text": "Q8_0", "vl": "Q4_K_M", "diffusion": "Q8_0", "llm": "Q4_K_M"}}))
    by = {c["id"]: c for c in p["components"]}
    assert by["text"]["quant"] == "Q8_0" and [f["file"] for f in by["text"]["files"]] == ["LFM3-1.7B-Q8_0.gguf"]
    assert by["vl"]["quant"] == "Q4_K_M" and by["vl"]["files"][1]["file"] == "mmproj-LFM3-VL-2B-Q8_0.gguf"  # no 4-bit projector: the 8-bit one
    assert by["omni"]["quant"] == p["recommended"] or by["omni"]["quant"] in ("BF16", "Q4_K_M")  # not asked for: the recommended one, or the nearest published
    assert [q["id"] for q in by["vl"]["quants"]] == ["BF16", "Q8_0", "Q6_K", "Q4_K_M"]  # what the repository publishes, largest first
    assert by["omni"]["files"][1]["file"].startswith("mmproj-LFM3-Omni-2B-")
    img = p["image"]
    if img:  # a system with no stable-diffusion.cpp build has none
        assert img["parts"]["diffusion"]["quant"] == "Q8_0" and img["parts"]["llm"]["quant"] == "Q4_K_M"
        assert [q["id"] for q in img["parts"]["diffusion"]["quants"]] == ["Q8_0", "Q4_K", "Q2_K"]
        assert [q["id"] for q in img["parts"]["llm"]["quants"]] == ["Q8_0", "Q4_K_M"]  # UD- and IQ builds are not offered
    # an unknown precision falls back instead of failing
    p2 = asyncio.run(installer.plan({"quants": {"text": "Q9_X"}}))
    assert {c["id"]: c["quant"] for c in p2["components"]}["text"] in ("BF16", "Q8_0", "Q4_K_M")


def test_picture_precision_is_remembered_for_drawing():
    assert installer.image_selection()["diffusion"] == "z_image_turbo-Q4_K.gguf"
    installer.save_image_selection({"diffusion": "Q8_0", "llm": "Q8_0"})
    got = installer.image_selection()
    assert got["diffusion"] == "z_image_turbo-Q8_0.gguf" and got["llm"] == "Qwen3-4B-Instruct-2507-Q8_0.gguf" and got["vae"] == "ae.safetensors"
    installer.save_image_selection({"diffusion": "../../etc/passwd", "llm": "Q4_K_M"})  # a file name is never built from anything but a known precision
    assert installer.image_selection()["diffusion"] == "z_image_turbo-Q4_K.gguf"
    installer.IMAGE_CONFIG.unlink()


def test_engine_must_be_llama_server_and_must_run():
    server = fake_engine()
    assert bringin.find_server(str(server)) == server and bringin.find_server(str(server.parent)) == server
    for bad in ("", str(server.parent / "nope"), str(Path(tempfile.mkdtemp()))):
        try:
            bringin.find_server(bad)
        except bringin.BringInError:
            pass
        else:
            raise AssertionError(repr(bad) + " was accepted")
    other = server.parent / "llama-cli"
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    try:
        bringin.find_server(str(other))
    except bringin.BringInError as e:
        assert "llama-server" in str(e)
    else:
        raise AssertionError("another program was accepted")
    assert bringin.engine_facts(server, os.environ.copy())["build"] == 11979
    assert [bringin.VERSION_RE.search(t).group(1) or bringin.VERSION_RE.search(t).group(2) for t in ("version: 11979 (abc)", "version: 0.8.0-dev (build 4321, commit x)")] == ["11979", "4321"]


def _run(options):
    s = installer.Setup()
    s.job = {"state": "running", "steps": [], "error": "", "started": 0, "finished": 0}
    asyncio.run(s._run(options))
    return s.job


def test_own_engine_and_weights_are_tried_before_they_are_kept():
    src = Path(tempfile.mkdtemp(prefix="mine-"))
    model = gguf(src / "my-chat-Q5_K_M.gguf", name="My chat model", extra=os.urandom(5000))
    server = fake_engine()
    job = _run({"components": ["text"], "own": {"engine": str(server), "models": {"text": {"model": str(model)}}}})
    assert job["state"] == "done", job
    cur = json.loads((installer.BIN_LLAMA / "current.json").read_text())
    assert cur["external"] is True and cur["dir"] == str(server.parent) and cur["tag"] == "b11979"
    rt = installer.installed_runtime()
    assert rt["server"] == server and rt["manual"] is True
    ov = registry.load_overrides()["text"]
    assert ov["model"] == "models/custom/my-chat-Q5_K_M.gguf" and (HOME / ov["model"]).is_file()
    assert os.path.samefile(model, HOME / ov["model"]), "same disk: a link, not a second copy"
    assert not installer.needed()
    plan = asyncio.run(installer.plan({}))
    chat = next(c for c in plan["components"] if c["id"] == "text")
    assert chat["installed"] and chat["own"] == "my-chat-Q5_K_M.gguf", chat  # the page says so, and Chat is not asked for again
    assert plan["runtime"]["current"]["external"] is True and plan["runtime"]["current"]["path"] == str(server)
    trial = next(s for s in job["steps"] if s["id"] == "trial")
    assert trial["state"] == "done" and "Paris" in trial["note"], trial
    assert not (HOME / "models" / "text" / "LFM3-1.7B-BF16.gguf").exists(), "the built-in model was not downloaded"
    # running it again changes nothing
    again = _run({"components": ["text"], "own": {"engine": str(server), "models": {"text": {"model": str(model)}}}})
    assert again["state"] == "done" and sorted(p.name for p in (HOME / "models" / "custom").iterdir()) == ["my-chat-Q5_K_M.gguf"]


def test_an_engine_that_cannot_load_the_model_is_not_kept_and_leaves_nothing_behind():
    for f in (installer.BIN_LLAMA / "current.json", HOME / "config" / "models.json"):
        f.unlink(missing_ok=True)
    src = Path(tempfile.mkdtemp(prefix="mine-"))
    model = gguf(src / "other.gguf")
    job = _run({"components": [], "own": {"engine": str(fake_engine(broken=True)), "models": {"text": {"model": str(model)}}}})
    assert job["state"] == "failed" and "unknown model architecture" in job["error"], job
    assert not (installer.BIN_LLAMA / "current.json").exists() and registry.load_overrides() == {}
    assert not (HOME / "models" / "custom" / "other.gguf").exists(), "the link made for the check was removed"
    assert model.is_file(), "the user's own file is never touched"


def test_wrong_files_are_refused_with_a_reason():
    src = Path(tempfile.mkdtemp(prefix="mine-"))
    server = fake_engine()
    vl = gguf(src / "vision.gguf")
    chat = {"model": str(gguf(src / "chat.gguf"))}
    job = _run({"components": [], "own": {"engine": str(server), "models": {"text": chat, "vl": {"model": str(vl)}}}})
    assert job["state"] == "failed" and "projector" in job["error"], job
    job = _run({"components": [], "own": {"engine": str(server), "models": {"text": {"model": str(gguf(src / "p.gguf", arch="clip"))}}}})
    assert job["state"] == "failed" and "projector" in job["error"], job
    job = _run({"components": [], "own": {"engine": str(server.parent / "gone"), "models": {}}})
    assert job["state"] == "failed", job


def test_vision_with_its_projector_loads():
    src = Path(tempfile.mkdtemp(prefix="mine-"))
    job = _run({"components": [], "own": {"engine": str(fake_engine()), "models": {"text": {"model": str(gguf(src / "chat.gguf"))},  # Chat is required: without files of its own it would be downloaded
                                                                                     "vl": {"model": str(gguf(src / "vis.gguf")), "mmproj": str(gguf(src / "mmproj-vis.gguf", arch="clip"))}}}})
    assert job["state"] == "done", job
    ov = registry.load_overrides()["vl"]
    assert ov["model"].endswith("vis.gguf") and ov["mmproj"].endswith("mmproj-vis.gguf")


def test_detect_lists_programs_and_files_without_reading_them():
    found = bringin.detect(HOME / "models")
    assert isinstance(found["engines"], list) and isinstance(found["files"], list)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                import traceback
                traceback.print_exc()
                print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
