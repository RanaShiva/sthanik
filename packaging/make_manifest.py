"""Write app/runtime_manifest.json: the llama.cpp and stable-diffusion.cpp builds Sthanik installs, with the SHA-256 GitHub publishes for each.

    python packaging/make_manifest.py                 # keep the pinned tags, refresh sizes and checksums
    python packaging/make_manifest.py --llama b12030  # move llama.cpp to another release tag

The first-run installer downloads only the files listed here and refuses a download whose SHA-256 differs, so a changed tag is a deliberate,
reviewable edit of this file's output. Uses only the standard library; set GITHUB_TOKEN to avoid the anonymous rate limit.
"""

import argparse
import json
import os
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "runtime_manifest.json"
REPOS = {"llama": "ggml-org/llama.cpp", "sd": "leejet/stable-diffusion.cpp"}
KEEP = {  # asset names the installer can pick; everything else (android, s390x, sycl, openvino ...) is not used
    "llama": ("-bin-ubuntu-x64.", "-bin-ubuntu-arm64.", "-bin-ubuntu-vulkan-x64.", "-bin-ubuntu-vulkan-arm64.", "-bin-ubuntu-cuda-12.8-x64.", "-bin-ubuntu-cuda-13.4-x64.",
              "-bin-ubuntu-cuda-13.4-arm64.", "-bin-macos-arm64.", "-bin-macos-x64.", "-bin-win-cpu-x64.", "-bin-win-cpu-arm64.", "-bin-win-vulkan-x64.", "-bin-win-vulkan-arm64.",
              "-bin-win-cuda-12.4-x64.", "-bin-win-cuda-13.4-x64.", "cudart-llama-"),
    "sd": ("-bin-Linux-Ubuntu-24.04-x86_64-vulkan.", "-bin-Linux-Ubuntu-24.04-x86_64.", "-bin-Darwin-macOS-", "-bin-win-cpu-x64.", "-bin-win-vulkan-x64.", "-bin-win-cuda12-x64.", "cudart-sd-bin-win-cu12-x64."),
}


def fetch(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "sthanik-manifest",
                                                **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.environ.get("GITHUB_TOKEN") else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llama", help="llama.cpp release tag")
    ap.add_argument("--sd", help="stable-diffusion.cpp release tag")
    args = ap.parse_args()
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    manifest = {}
    for key, repo in REPOS.items():
        tag = getattr(args, key) or old.get(key, {}).get("tag")
        if not tag:
            raise SystemExit(f"No {key} tag yet: pass --{key} <tag>")
        rel = fetch(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
        assets = {}
        for a in rel["assets"]:
            if any(k in a["name"] for k in KEEP[key]) and a["name"].endswith((".zip", ".tar.gz")):
                digest = a.get("digest") or ""
                if not digest.startswith("sha256:"):
                    raise SystemExit(f"{a['name']}: GitHub published no SHA-256 for it")
                assets[a["name"]] = {"url": a["browser_download_url"], "size": a["size"], "sha256": digest[7:]}
        manifest[key] = {"repo": repo, "tag": rel["tag_name"], "assets": dict(sorted(assets.items()))}
        print(f"{key}: {rel['tag_name']}  {len(assets)} assets")
    OUT.write_text(json.dumps(manifest, indent=1) + "\n")


if __name__ == "__main__":
    main()
