"""Tests for hardware choice, the installer's file handling and the chat projects. Offline. Run: .venv/bin/python -I tests/test_setup.py"""

import asyncio
import hashlib
import http.server
import io
import json
import os
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

_home = tempfile.mkdtemp(prefix="sthanik-test-")
os.environ["STHANIK_HOME"] = _home  # before the modules read it
os.environ["LFM3_CHATS_DIR"] = os.path.join(_home, "chats")

import chats  # noqa: E402
import hardware  # noqa: E402
import installer  # noqa: E402


def machine(os_, arch, gpus=(), ram=16384):
    return {"os": os_, "arch": arch, "gpus": list(gpus), "ram_mb": ram}


NV = lambda vram=6144, cuda=(13, 4), cc=8.6: {"vendor": "nvidia", "name": "NVIDIA test", "vram_mb": vram, "cuda": cuda, "compute_cap": cc, "integrated": False}  # noqa: E731
AMD = {"vendor": "amd", "name": "AMD test", "vram_mb": 10240, "integrated": False}
INTEL = {"vendor": "intel", "name": "Intel test", "vram_mb": None, "integrated": True}
APPLE = {"vendor": "apple", "name": "Apple M2", "vram_mb": 16384, "integrated": True, "unified": True}

ALL_MACHINES = {
    "linux nvidia cuda13": machine("linux", "x86_64", [NV()]),
    "linux nvidia cuda12.8": machine("linux", "x86_64", [NV(cuda=(12, 8))]),
    "linux nvidia unknown version": machine("linux", "x86_64", [NV(cuda=None)]),
    "linux nvidia pascal": machine("linux", "x86_64", [NV(cc=6.1)]),
    "linux nvidia json round trip": machine("linux", "x86_64", [NV(cuda=[13, 4])]),
    "linux arm64 nvidia": machine("linux", "arm64", [NV()]),
    "linux amd": machine("linux", "x86_64", [AMD]),
    "linux intel igpu": machine("linux", "x86_64", [INTEL]),
    "linux cpu": machine("linux", "x86_64"),
    "linux arm64 cpu": machine("linux", "arm64"),
    "windows nvidia": machine("windows", "x86_64", [NV()]),
    "windows nvidia 12.4": machine("windows", "x86_64", [NV(cuda=(12, 4))]),
    "windows amd": machine("windows", "x86_64", [AMD]),
    "windows cpu": machine("windows", "x86_64"),
    "windows arm64": machine("windows", "arm64"),
    "mac arm": machine("macos", "arm64", [APPLE]),
    "mac intel": machine("macos", "x86_64"),
}


def test_every_choice_is_in_the_manifest_and_ends_on_a_cpu_build():
    for name, hw in ALL_MACHINES.items():
        cands = hardware.choose_runtime(hw)
        assert cands, name
        assert cands[-1]["backend"] in ("cpu", "metal"), (name, cands[-1])
        for c in cands:
            assert installer._asset(c["asset"]), (name, c["asset"])
            for e in c["extra"]:
                assert installer._asset(e), (name, e)


def test_runtime_choice_by_hardware():
    first = lambda hw: hardware.choose_runtime(hw)[0]  # noqa: E731
    assert "cuda-13.4-x64.tar.gz" in first(ALL_MACHINES["linux nvidia cuda13"])["asset"]
    assert "cuda-12.8" in first(ALL_MACHINES["linux nvidia cuda12.8"])["asset"]
    assert "cuda-13.4" in first(ALL_MACHINES["linux nvidia unknown version"])["asset"]  # unreadable version: try it, the device check decides
    assert first(ALL_MACHINES["linux nvidia pascal"])["backend"] != "cuda" or "cuda-12.8" in first(ALL_MACHINES["linux nvidia pascal"])["asset"]  # CUDA 13 dropped pre-Turing cards
    assert first(ALL_MACHINES["linux amd"])["backend"] == "vulkan"
    assert first(ALL_MACHINES["linux cpu"])["backend"] == "cpu"
    assert "win-cuda-13.4-x64.zip" in first(ALL_MACHINES["windows nvidia"])["asset"]
    assert "win-cuda-12.4" in first(ALL_MACHINES["windows nvidia 12.4"])["asset"]
    assert first(ALL_MACHINES["windows amd"])["asset"].endswith("win-vulkan-x64.zip")
    assert first(ALL_MACHINES["mac arm"])["asset"].endswith("macos-arm64.tar.gz")
    assert first(ALL_MACHINES["mac intel"])["asset"].endswith("macos-x64.tar.gz")


def test_precision_follows_memory():
    assert hardware.choose_quant(machine("linux", "x86_64", [NV(6144)])) == "BF16"
    assert hardware.choose_quant(machine("linux", "x86_64", [NV(4096)])) == "Q8_0"
    assert hardware.choose_quant(machine("linux", "x86_64", [NV(2048)])) == "Q4_K_M"
    assert hardware.choose_quant(machine("linux", "x86_64", ram=8192)) == "Q4_K_M"
    assert hardware.choose_quant(machine("linux", "x86_64", ram=32768)) == "Q8_0"
    assert hardware.choose_ctx(machine("linux", "x86_64", ram=4096), 32768) == 8192


def test_gpu_layers():
    hardware.PROFILE = Path(_home) / "config" / "hardware.json"
    os.environ.pop("LFM3_NGL", None)
    assert hardware.gpu_layers(3000) == "all"  # no profile: the behaviour the app always had
    hardware.save_profile({"gpu": False})
    assert hardware.gpu_layers(3000) == "0"
    hardware.save_profile({"gpu": True, "vram_mb": 5832})
    assert hardware.gpu_layers(3306) == "all"
    assert hardware.gpu_layers(5500) == "auto"
    assert hardware.gpu_layers(3306, 361) == "all"
    os.environ["LFM3_NGL"] = "12"
    assert hardware.gpu_layers(5500) == "12"
    del os.environ["LFM3_NGL"]


def test_model_files_follow_the_repositories_names():
    assert [f["file"] for f in installer.model_files("text", "Q8_0")] == ["LFM3-1.7B-Q8_0.gguf"]
    assert [f["file"] for f in installer.model_files("vl", "Q4_K_M")] == ["LFM3-VL-2B-Q4_K_M.gguf", "mmproj-LFM3-VL-2B-Q8_0.gguf"]  # Vision has no 4-bit projector
    assert [f["file"] for f in installer.model_files("omni", "BF16")] == ["LFM3-Omni-2B-BF16.gguf", "mmproj-LFM3-Omni-2B-BF16.gguf"]
    assert installer.model_files("audio", "BF16")[0]["path"] == "models/audio/LFM3-Audio-2B-BF16.gguf"  # the slot's built-in default path


def test_list_devices_parser():
    sample = "ggml_cuda_init: found 1 CUDA devices\nAvailable devices:\n  CUDA0: NVIDIA GeForce RTX 3060 Laptop GPU (5832 MiB, 819 MiB free)\n  CPU: AMD Ryzen (15827 MiB, 9000 MiB free)\n"
    got = [{"id": m[0], "name": m[1], "total_mb": int(m[2])} for m in installer.DEVICE_RE.findall(sample)]
    assert got == [{"id": "CUDA0", "name": "NVIDIA GeForce RTX 3060 Laptop GPU", "total_mb": 5832}, {"id": "CPU", "name": "AMD Ryzen", "total_mb": 15827}], got


def test_extract_refuses_paths_outside_the_folder():
    d = Path(tempfile.mkdtemp())
    bad = d / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 3
        t.addfile(info, io.BytesIO(b"abc"))
    try:
        installer._safe_extract(bad, d / "out")
    except installer.SetupError:
        pass
    else:
        raise AssertionError("a path outside the folder was accepted")
    assert not (d / "escape.txt").exists()


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves DATA with Range support; the first request is cut short to prove that the download resumes."""
    DATA = os.urandom(3_000_000)
    cut = True

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        start = 0
        if self.headers.get("Range"):
            start = int(self.headers["Range"].split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(self.DATA) - 1}/{len(self.DATA)}")
        else:
            self.send_response(200)
        body = self.DATA[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if _Handler.cut and start == 0:
            _Handler.cut = False
            self.wfile.write(body[:1_000_000])
            self.wfile.flush()
            self.connection.close()  # dropped half way
            return
        self.wfile.write(body)


def test_download_resumes_and_verifies():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/f.bin"
    want = hashlib.sha256(_Handler.DATA).hexdigest()
    dest = Path(tempfile.mkdtemp()) / "f.bin"
    step = {"done": 0, "total": 0, "note": ""}
    asyncio.run(installer.download(url, dest, step, want, len(_Handler.DATA)))
    assert dest.read_bytes() == _Handler.DATA and not dest.with_name("f.bin.part").exists()
    # a wrong checksum deletes the file
    bad = Path(tempfile.mkdtemp()) / "g.bin"
    try:
        asyncio.run(installer.download(url, bad, {"done": 0, "total": 0, "note": ""}, "0" * 64, len(_Handler.DATA)))
    except installer.SetupError as e:
        assert "SHA-256" in str(e)
    else:
        raise AssertionError("a wrong checksum was accepted")
    assert not bad.exists() and not bad.with_name("g.bin.part").exists()
    srv.shutdown()


def test_projects():
    mk = lambda cid: chats.save(cid, {"scope": "text", "history": [], "items": [{"t": "u", "text": "hi"}], "title": cid})  # noqa: E731
    mk("aaaaaaaa1"); mk("bbbbbbbb2")
    assert chats.projects() == []
    assert chats.create_project("  Work  ") == "Work" and chats.create_project("work") == "Work"
    assert chats.projects() == ["Work"]  # an empty project is kept
    chats.set_project("aaaaaaaa1", "work")
    assert chats.get("aaaaaaaa1")["project"] == "Work"
    assert {c["id"]: c["project"] for c in chats.list_chats()} == {"aaaaaaaa1": "Work", "bbbbbbbb2": ""}  # _projects.json is not a chat
    assert chats.rename_project("Work", "Jobs") == 1 and chats.projects() == ["Jobs"]
    chats.create_project("Home"); chats.rename_project("Home", "jobs")
    assert chats.projects() == ["Jobs"]
    chats.rename_project("Jobs", "")
    assert chats.projects() == [] and chats.get("aaaaaaaa1")["project"] == ""
    chats.save("cccccccc3", {"scope": "text", "history": [], "items": [], "project": "Old"})
    assert chats.projects() == ["Old"]  # a chat that names a project makes it exist


def test_packaged_linux_build_does_not_pass_its_libraries_to_the_programs_it_starts():
    """The bootloader points LD_LIBRARY_PATH at the bundled (older) libraries; kde-open and xdg-open inherited it and failed with
    "GLIBCXX_3.4.32 not found", so the browser never opened from the menu entry."""
    import launcher

    saved_env, saved_frozen, saved_platform = dict(os.environ), getattr(sys, "frozen", None), sys.platform
    try:
        sys.platform = "linux"
        sys.frozen = True
        os.environ["LD_LIBRARY_PATH"] = "/bundle/_internal:/users/own/lib"
        os.environ["LD_LIBRARY_PATH_ORIG"] = "/users/own/lib"
        launcher._clean_child_environment()
        assert os.environ["LD_LIBRARY_PATH"] == "/users/own/lib"  # what the user had before
        os.environ["LD_LIBRARY_PATH"] = "/bundle/_internal"
        os.environ.pop("LD_LIBRARY_PATH_ORIG")
        launcher._clean_child_environment()
        assert "LD_LIBRARY_PATH" not in os.environ  # the user had none
        sys.frozen = False  # a source checkout is left alone
        os.environ["LD_LIBRARY_PATH"] = "/mine"
        launcher._clean_child_environment()
        assert os.environ["LD_LIBRARY_PATH"] == "/mine"
    finally:
        sys.platform = saved_platform
        if saved_frozen is None:
            sys.__dict__.pop("frozen", None)
        else:
            sys.frozen = saved_frozen
        os.environ.clear()
        os.environ.update(saved_env)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
