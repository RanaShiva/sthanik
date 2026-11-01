"""Guards on the page source for mistakes that only show up when a page is used. Offline. Run: .venv/bin/python -I tests/test_ui_source.py"""

import re
import sys
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js"


def test_optional_parts_of_a_chat_are_null_not_false():
    """chatview.js calls ws?.x() and menu?.x(); `?.` skips null and undefined but not false, so `const menu = toolsEnabled && make()` made every
    send on the Vision page (which has no tools) throw "menu?.setKind is not a function"."""
    src = (JS / "chatview.js").read_text(encoding="utf-8")
    called = set(re.findall(r"\b(\w+)\?\.\w+\(", src))
    for name in sorted(called):
        for m in re.finditer(rf"^\s*const {name} = ([^\n]*)", src, re.M):
            assert not re.match(r"\w+ && ", m.group(1)), f"`const {name} = ... && ...` is false when off; use `cond ? make() : null` ({m.group(0).strip()[:80]})"


def test_every_script_is_a_module_import_that_exists():
    for f in JS.rglob("*.js"):
        for rel in re.findall(r"""from ['"](\.{1,2}/[^'"]+)['"]""", f.read_text(encoding="utf-8")):
            assert (f.parent / rel).resolve().is_file(), f"{f.name} imports {rel}, which does not exist"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except AssertionError as e:
                failed += 1
                print("FAIL", name, "-", e)
    sys.exit(1 if failed else 0)
