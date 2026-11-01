"""Which messages get a web lookup before the model answers (app/static/js/lookup.js). Needs node; skipped without it.
Run: .venv/bin/python -I tests/test_lookup.py"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "..", "app", "static", "js", "lookup.js")

LOOKUP = [
    "Who won the 2024 Nobel Prize in Physics?",
    "Who won the 2025 Nobel Prize in Physics?",
    "What is the latest stable version of Python?",
    "Who is the current CEO of NVIDIA?",
    "How many minutes should I boil an egg for a jammy yolk?",
    "What was the population of the city of Pune in the 2011 census?",
    "What is the latest stable Linux kernel version?",
    "Can you tell me the latest version of Node?",
    "When did the Berlin Wall fall?",
    "How much does a Raspberry Pi 5 cost?",
    "What's the weather in Pune today?",
    "Who is the prime minister of Canada?",
    "what is the exchange rate of euro to dollar",
    "How long should I roast a 2 kg chicken?",
    "Which team won the 2022 FIFA World Cup?",
]
NOT_LOOKUP = [
    "What does the PyInstaller --hidden-import option do?",  # stable knowledge: the model can answer, and may still search itself
    "Explain how photosynthesis works",
    "Write a poem about the latest rain",
    "Summarize this: the quick brown fox jumps over the lazy dog",
    "What is 17 * 23 + 144 / 12?",
    "How many files are in my project folder?",
    "Who is the author of this document?",
    "Translate 'good morning' into Hindi",
    "What was the first thing I asked you?",
    "Write a function that returns the latest date in a list",
    "hi",
    "Thanks! That makes sense.",
    "What is the capital of France? Then write a story about it. Make it long and add a twist ending, with five characters.",
    "Draw a diagram of how many people live in Pune",
    "What is the error in the following code?",
    "I want to know the latest about my order",
    "",
]
QUERIES = {
    "Can you tell me the latest version of Node?": "the latest version of Node",
    "Please tell me who won the 2025 Nobel Prize in Physics?": "who won the 2025 Nobel Prize in Physics",
    "Who is the current CEO of NVIDIA?": "Who is the current CEO of NVIDIA",
}


def run_node() -> dict:
    code = """
import { needsLookup, lookupQuery } from %s;
const input = JSON.parse(process.argv[1]);
console.log(JSON.stringify({ lookup: input.lookup.map(needsLookup), not: input.not.map(needsLookup), queries: Object.fromEntries(Object.keys(input.queries).map((k) => [k, lookupQuery(k)])) }));
""" % json.dumps("file://" + os.path.abspath(MODULE))
    out = subprocess.run(["node", "--input-type=module", "-e", code, json.dumps({"lookup": LOOKUP, "not": NOT_LOOKUP, "queries": QUERIES})], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise AssertionError(out.stderr[-600:])
    return json.loads(out.stdout)


def test_questions_that_need_a_fact_are_looked_up_and_the_rest_are_left_alone():
    got = run_node()
    missed = [t for t, ok in zip(LOOKUP, got["lookup"]) if not ok]
    wrong = [t for t, ok in zip(NOT_LOOKUP, got["not"]) if ok]
    assert not missed, f"not looked up: {missed}"
    assert not wrong, f"looked up although it should not be: {wrong}"
    for asked, want in QUERIES.items():
        assert got["queries"][asked] == want, (asked, got["queries"][asked])


if __name__ == "__main__":
    if not shutil.which("node"):
        print("skip test_questions_that_need_a_fact_are_looked_up_and_the_rest_are_left_alone (node is not installed)")
        sys.exit(0)
    try:
        test_questions_that_need_a_fact_are_looked_up_and_the_rest_are_left_alone()
        print("ok   test_questions_that_need_a_fact_are_looked_up_and_the_rest_are_left_alone")
    except AssertionError as e:
        print("FAIL", e)
        sys.exit(1)
