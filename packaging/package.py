"""Turn PyInstaller's output (dist/) into the file people download (release/).

    python packaging/package.py                 # version from app/version.py
    python packaging/package.py --version 2.0.1

Linux:   Sthanik-<v>-linux-<arch>.tar.gz   (the program, install.sh, icon)   [the AppImage comes from linux/build-appimage.sh]
Windows: Sthanik-<v>-windows-x64.zip       (unzip and run Sthanik.exe)        [the installer comes from windows/sthanik.iss]
macOS:   Sthanik-<v>-macos-<arch>.dmg      (drag Sthanik to Applications)

Each file gets a line in release/SHA256SUMS.
"""

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST, OUT = ROOT / "dist", ROOT / "release"


def arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def linux(version: str) -> Path:
    name = f"Sthanik-{version}-linux-{arch()}"
    stage = ROOT / "build" / name
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copytree(DIST / "sthanik", stage / "sthanik", symlinks=True)
    shutil.copy(ROOT / "packaging" / "linux" / "install.sh", stage / "install.sh")
    shutil.copy(ROOT / "packaging" / "icons" / "sthanik-512.png", stage / "sthanik.png")
    (stage / "README.txt").write_text(f"Sthanik {version}\n\nRun ./install.sh to add it to your applications menu, or just run ./sthanik/sthanik.\n"
                                      "On first start it looks at this computer and offers to install the engine and the models.\n")
    out = OUT / f"{name}.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        t.add(stage, arcname=name)
    return out


def windows(version: str) -> Path:
    out = OUT / f"Sthanik-{version}-windows-x64.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted((DIST / "Sthanik").rglob("*")):
            if p.is_file():
                z.write(p, Path("Sthanik") / p.relative_to(DIST / "Sthanik"))
        z.writestr("Sthanik/README.txt", f"Sthanik {version}\n\nRun Sthanik.exe. A console window stays open while it runs: close it to quit.\n"
                   "Your data is kept in %LOCALAPPDATA%\\Sthanik. To keep it next to the program instead, add a file named portable.txt beside Sthanik.exe.\n")
    return out


def macos(version: str) -> Path:
    app = DIST / "Sthanik.app"
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)  # ad hoc: Apple silicon will not run unsigned code
    stage = ROOT / "build" / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copytree(app, stage / "Sthanik.app", symlinks=True)
    (stage / "Applications").symlink_to("/Applications")
    out = OUT / f"Sthanik-{version}-macos-{arch()}.dmg"
    out.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", "Sthanik", "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(out)], check=True)
    return out


def sums(files: list[Path]):
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files]
    path = OUT / "SHA256SUMS"
    old = [l for l in (path.read_text().splitlines() if path.exists() else []) if l.split("  ", 1)[-1] not in {f.name for f in files}]
    path.write_text("\n".join(old + lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version")
    version = ap.parse_args().version or (lambda ns: (exec((ROOT / "app" / "version.py").read_text(), ns), ns["VERSION"])[1])({})
    OUT.mkdir(exist_ok=True)
    made = {"linux": linux, "win32": windows, "darwin": macos}[sys.platform if sys.platform in ("win32", "darwin") else "linux"](version)
    sums([made])
    print(f"{made}  {made.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
