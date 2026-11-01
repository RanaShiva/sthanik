# PyInstaller recipe for the packaged program. Build with:  pyinstaller --noconfirm --distpath dist --workpath build packaging/sthanik.spec
# Output: dist/Sthanik/ (Windows, Linux) or dist/Sthanik.app (macOS).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
APP = ROOT / "app"
VERSION = {}
exec((APP / "version.py").read_text(), VERSION)
VERSION = VERSION["VERSION"]

datas = [(str(APP / "static"), "app/static"), (str(APP / "builtin_skills"), "app/builtin_skills"), (str(APP / "runtime_manifest.json"), "app")]
binaries, hiddenimports = [], []
# packages that load parts of themselves by name, or ship templates and data files next to their code
for pkg in ("ddgs", "pptx", "docx", "openpyxl", "xlsxwriter", "certifi", "primp", "markdown_it", "psutil"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h
hiddenimports += collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))  # mcp.cli needs typer and exits without it
datas += collect_data_files("mcp")
hiddenimports += collect_submodules("uvicorn") + collect_submodules("websockets") + collect_submodules("httpx") + collect_submodules("anyio")
datas += collect_data_files("pydantic")

a = Analysis(
    [str(APP / "launcher.py")],
    pathex=[str(APP)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "numpy.tests", "pytest", "IPython"],
)
pyz = PYZ(a.pure)

windows = sys.platform == "win32"
mac = sys.platform == "darwin"
icon = str(ROOT / "packaging" / "icons" / ("sthanik.ico" if windows else "sthanik.icns" if mac else "sthanik-512.png"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sthanik" if (windows or mac) else "sthanik",
    # Windows keeps a console window while the server runs: closing it quits (there is no tray icon). macOS and Linux run without one;
    # the sidebar's Quit button stops the server there.
    console=windows,
    icon=icon if (windows or mac) else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Sthanik" if (windows or mac) else "sthanik")

if mac:
    app = BUNDLE(
        coll,
        name="Sthanik.app",
        icon=icon,
        bundle_identifier="app.sthanik.studio",
        version=VERSION,
        info_plist={"CFBundleName": "Sthanik", "CFBundleDisplayName": "Sthanik", "CFBundleShortVersionString": VERSION, "NSHighResolutionCapable": True,
                    "LSMinimumSystemVersion": "12.0", "NSMicrophoneUsageDescription": "Sthanik can listen to you speak when you use Voice."},
    )
