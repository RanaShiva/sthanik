"""Regression tests for the deterministic parts of the document harness. Run: .venv/bin/python -I tests/test_document_harness.py"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import docagent as g  # noqa: E402
import picturereview as pr  # noqa: E402
import webimages  # noqa: E402
import docaudit  # noqa: E402
import documents as docs  # noqa: E402

PIC = "![x](img:a.jpg)"
MESSY = f"""# Ten Places

## Introduction

Intro text that is long enough to count as a paragraph on its own, repeated below on purpose.

## Place 2: Pataliputra

{PIC}

Capital of the Mauryan empire on the Ganges, described here in a full sentence for the test.

## Place 2: Pataliputra

Capital of the Mauryan empire on the Ganges, described here in a full sentence for the test.

## New step

## Place 4: Mohenjo-Daro

[To be added]

Indus city with a long enough description to be a real paragraph in this small fixture.

```mermaid
flowchart TD
  A --> B
"""


def test_unclosed_fence_is_closed():
    fixed, closed = docs.close_unclosed_fence(MESSY)
    assert closed and fixed.rstrip().endswith("```")
    assert docs.close_unclosed_fence(fixed)[1] is False


def test_tidy_removes_junk_and_is_idempotent():
    out, fixed = docaudit.tidy(MESSY)
    assert fixed
    assert out.count("## Place 2: Pataliputra") == 1
    assert "New step" not in out and "[To be added]" not in out
    assert "Mauryan empire" in out
    again, fixed2 = docaudit.tidy(out)
    assert again == out and not fixed2


def test_numbering_gap_is_reported_not_fixed():
    src = "# T\n\n## Place 1: A\n\ntext\n\n## Place 2: B\n\ntext\n\n## Place 4: D\n\ntext\n"
    gaps = [i for i in docaudit.audit(src) if i["type"] == "numbering_gap"]
    assert gaps and not gaps[0]["fixable"] and "3" in gaps[0]["detail"]
    assert docaudit.tidy(src)[0] == src


def test_remove_pictures_text_scopes():
    src = f"# T\n\n## A\n\n{PIC}\n\ntext\n\n## B\n\n![y](img:b.jpg)\n\ntext\n"
    new, gone = docs.remove_pictures_text(src, "A")
    assert gone == ["a.jpg"] and "img:b.jpg" in new and "img:a.jpg" not in new
    new, gone = docs.remove_pictures_text(src)
    assert len(gone) == 2 and "img:" not in new


def test_explicit_titles():
    assert g.explicit_titles("add new sections 'Place 1: Anuradha' and 'Place 5: Rameswaram'") == ["Place 1: Anuradha", "Place 5: Rameswaram"]
    assert g.explicit_titles('add Place 1: Anuradha" with appropriate content and Place 5: Rameswaram" with appropriate content') == ["Place 1: Anuradha", "Place 5: Rameswaram"]
    assert g.explicit_titles("Add Place 1: Anuradha before Place 2 and Place 5: Rameswaram after Place 4") == ["Place 1: Anuradha", "Place 5: Rameswaram"]
    assert g.explicit_titles("add a section about cost savings") == []


def test_scoped_sections_do_not_swallow_neighbours():
    src = "# T\n\n## A\n\ntext\n\n## B\n\n![x](img:b.jpg)\n\n## C\n\ntext\n"
    scope = g._starts_for(src, ["A", "C"])
    assert g.uncovered_sections(src, "document", scope) == ["A", "C"]


def test_title_echo_section_is_junk():
    src = "# Ten Places (X)\n\n## Place 9: K\n\nreal text here\n\n## Ten Places (X) - Place 9: K\n\nagain\n"
    assert docaudit.tidy(src)[0].count("Place 9: K") == 1


def test_numbered_anchor_keeps_order():
    h = ["Introduction", "Place 2: B", "Place 3: C", "Place 4: D", "Place 6: F"]
    assert g.numbered_anchor(h, "Place 5: E") == "Place 4: D"
    assert g.numbered_anchor(h, "Place 1: A") == "Introduction"
    assert g.numbered_anchor(h, "Place 7: G") == "Place 6: F"
    assert g.numbered_anchor(h, "Conclusion") == "(end)"


def test_rewrite_keeps_heading_and_assets():
    original = f"## Place 3\n\n{PIC}\n\nlong text\n\n```mermaid\nflowchart TD\n A-->B\n```"
    out = g._restore_assets(original, g._force_heading("# Something else\n\nshort", "Place 3", 2))
    assert out.startswith("## Place 3") and PIC in out and "flowchart TD" in out


def test_insert_image_lines_with_duplicate_headings():
    src = "# T\n\n## A\n\ntext one\n\n## A\n\ntext two\n"
    heads = [i for i, lv, t in docs._headings(src) if lv == 2]
    assert len(heads) == 2
    out = docs.insert_image_lines(src, [{"start": heads[1], "end": len(src.rstrip("\n").split("\n")), "name": "z.jpg", "caption": "c", "where": "start"}])
    assert out.index("img:z.jpg") > out.rindex("## A") and out.index("text one") < out.index("## A", out.index("text one"))


def test_picture_review_subjects():
    assert pr._subject("Place 3: Taxila", "T") == "Taxila"
    assert pr._subject("Taxila: Learning Hub of Ancient India", "T") == "Taxila"
    assert pr._subject("Introduction and Overview", "Ancient Cities") == "Ancient Cities"
    assert pr._subject("Mohenjo-Daro", "T") == "Mohenjo-Daro"


def test_pictures_in_finds_each_picture_with_its_section():
    src = "# T\n\n## A\n\n![a](img:a.jpg)\n\ntext\n\n## B\n\ntext\n\n![b](img:b.jpg)\n"
    got = pr.pictures_in(src)
    assert [(p["section"], p["name"]) for p in got] == [("A", "a.jpg"), ("B", "b.jpg")]
    assert [p["name"] for p in pr.pictures_in(src, ["B"])] == ["b.jpg"]


def test_download_network_errors_become_web_image_errors():
    import asyncio

    import httpx

    async def ok_host(_h):
        return None

    class Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            raise httpx.ConnectTimeout("slow")

    orig_host, orig_client = webimages._check_public_host, webimages.httpx.AsyncClient
    webimages._check_public_host, webimages.httpx.AsyncClient = ok_host, Boom
    try:
        try:
            asyncio.run(webimages._fetch("https://example.org/a.jpg"))
            raise AssertionError("expected WebImageError")
        except webimages.WebImageError as e:
            assert "ConnectTimeout" in str(e)
    finally:
        webimages._check_public_host, webimages.httpx.AsyncClient = orig_host, orig_client


def test_plan_guidance_has_rules_not_whole_file_layouts():
    """Plan steps write ONE section: a skill's layouts/storylines would make each step write a whole document."""
    import skills

    store = skills.SkillStore()
    for names, banned in ((["docx"], "Proposal"), (["pdf"], "Memo"), (["pptx"], "Storyline"), (["html"], "Layout")):
        text = store.writing_guidance(names)
        assert text and banned not in text, (names, text[:200])
        assert "create_document" not in text and "create_slides" not in text
    assert "One idea per slide" in store.writing_guidance(["pptx"])


def test_diagram_limit_for_model_calls_stays_12():
    import asyncio

    blocks = "\n\n".join("```mermaid\nflowchart TD\n  A --> B\n```" for _ in range(13))
    try:
        asyncio.run(docs._prepare(blocks, strict=True))
        raise AssertionError("expected DocError")
    except docs.DocError as e:
        assert "limit 12" in str(e)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if failed else 0)
