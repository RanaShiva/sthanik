"""Title and address rules shared by the plan (app/static/js/doctext.js) and the server (app/doclint.py). The JS needs node; skipped without it.
Run: .venv/bin/python -I tests/test_doctext.py"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import doclint  # noqa: E402

TITLES = {
    "Document Outline for PDF on ML/DL/LLM Topics with Important Math": "ML/DL/LLM Topics with Important Math",
    "Create a PDF about solar energy": "Solar energy",
    "Write a report about the French Revolution": "The French Revolution",
    "Make a PDF for new hires on safety": "New hires on safety",
    "Outline of a document on climate policy": "Climate policy",
    "Report on Q3 Sales": "Report on Q3 Sales",
    "Plan for a Weekend in Lisbon": "Plan for a Weekend in Lisbon",
    "Draft for Q3 Plan": "Draft for Q3 Plan",
    "Overview of Kubernetes Networking": "Overview of Kubernetes Networking",
    "A Guide to Sourdough": "A Guide to Sourdough",
    "How to Make PDFs with Python": "How to Make PDFs with Python",
    "Machine Learning, Deep Learning and LLMs: A Study Guide": "Machine Learning, Deep Learning and LLMs: A Study Guide",
    "Can you make a guide for new hires?": "Can you make a guide for new hires?",
    "Outline": "Outline",
    "": "",
}
TEXT = ("Results: https://en.wikipedia.org/wiki/Neural_network_(machine_learning) and (see https://example.com/a/b). Also https://example.com/x?y=1, "
        "and [link](https://example.com/z) end.")
URLS = ["https://en.wikipedia.org/wiki/Neural_network_(machine_learning)", "https://example.com/a/b", "https://example.com/x?y=1", "https://example.com/z"]


def test_python_titles():
    for given, want in TITLES.items():
        assert doclint.clean_title(given) == want, (given, doclint.clean_title(given))


DIAGRAM_ONLY = ["Generate a deatiled mermaid diagram for a tiny url system design. Be detailed.", "Draw a sequence diagram of the login flow", "Make a flowchart for ordering coffee",
                "Create an ER diagram for a library database", "give me a mermaid state machine for a traffic light"]
MORE = ["Create a PDF report with diagrams of the architecture", "Make a presentation with a flowchart on onboarding", "Write a report on tiny url design including a diagram",
        "Create a document about system design with a mermaid diagram", "Build a spreadsheet and a diagram of our costs", "Write a guide to drawing flowcharts", "Explain how a URL shortener works"]


def test_javascript_titles_and_addresses_match():
    code = """
import { cleanTitle, urlsIn, asksForDiagramOnly } from %s;
const i = JSON.parse(process.argv[1]);
console.log(JSON.stringify({ titles: i.titles.map(cleanTitle), urls: urlsIn(i.text), only: i.only.map(asksForDiagramOnly), more: i.more.map(asksForDiagramOnly) }));
""" % json.dumps("file://" + os.path.abspath(os.path.join(HERE, "..", "app", "static", "js", "doctext.js")))
    out = subprocess.run(["node", "--input-type=module", "-e", code, json.dumps({"titles": list(TITLES), "text": TEXT, "only": DIAGRAM_ONLY, "more": MORE})], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    got = json.loads(out.stdout)
    assert got["titles"] == list(TITLES.values()), [(a, b) for a, b in zip(TITLES, got["titles"]) if TITLES[a] != b]
    assert got["urls"] == URLS, got["urls"]
    assert all(got["only"]), [t for t, ok in zip(DIAGRAM_ONLY, got["only"]) if not ok]
    assert not any(got["more"]), [t for t, ok in zip(MORE, got["more"]) if ok]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        if "javascript" in name and not shutil.which("node"):
            print("skip", name, "(node is not installed)")
            continue
        try:
            fn()
            print("ok  ", name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
