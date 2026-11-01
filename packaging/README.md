# Packaging

`pyinstaller packaging/sthanik.spec` turns `app/launcher.py` and everything it imports into a self-contained folder (`dist/sthanik/`, `dist/Sthanik/` or
`dist/Sthanik.app`). `python packaging/package.py` then makes the file people download in `release/`:

| System | Made by | Output |
|---|---|---|
| Linux | `package.py` | `Sthanik-<v>-linux-<arch>.tar.gz` (program + `install.sh` + icon) |
| Windows | `package.py`, then `windows/sthanik.iss` with Inno Setup | `Sthanik-<v>-windows-x64.zip`, `…-setup.exe` |
| macOS | `package.py` (ad-hoc `codesign`, `hdiutil`) | `Sthanik-<v>-macos-<arch>.dmg` |

The programs do **not** contain the llama.cpp engine or any model: the first-run Setup downloads the build that suits the computer (see
`app/runtime_manifest.json`) and the models. That keeps each download around 50-100 MB instead of several GB, and means one package covers NVIDIA, AMD,
Intel, Apple and CPU-only machines.

## Locally

```bash
python3 -m venv .venv-build && .venv-build/bin/pip install -r requirements-build.txt
.venv-build/bin/pyinstaller --noconfirm --distpath dist --workpath build packaging/sthanik.spec
dist/sthanik/sthanik --selftest      # the same check CI runs on every system
.venv-build/bin/python packaging/package.py
```

## What is and is not covered

- Not code-signed or notarised (that needs paid Apple and Windows certificates): Windows SmartScreen and macOS Gatekeeper warn on first run (README, *Install*).
- No AppImage / `.deb` / `.rpm`: the AppImage tooling is only distributed as a moving "continuous" build, and executing an unpinned binary inside the release job was not acceptable. Adding it means pinning that file's SHA-256.
- The icons in `icons/` were rendered from `app/static/sthanik.svg`.
