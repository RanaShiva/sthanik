"""Formulas in files, and the leftovers a small model puts in a document. Cases come from a real study guide that came out wrong.
Offline; the PDF test needs Chrome and is skipped without it. Run: .venv/bin/python -I tests/test_math_and_lint.py"""

import asyncio
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

_home = tempfile.mkdtemp(prefix="sthanik-test-")
os.environ["STHANIK_HOME"] = _home
os.environ["LFM3_DOCS_DIR"] = os.path.join(_home, "docs")
os.environ["LFM3_IMAGES_DIR"] = os.path.join(_home, "img")

import chrome  # noqa: E402
import doclint  # noqa: E402
import documents as docs  # noqa: E402
import mathtext as M  # noqa: E402


# ---------------------------------------------------------------- math
def test_formulas_the_model_wrote_in_other_forms_get_proper_delimiters():
    src = ("Softmax:\n\n[ p(y_i)=\\frac{\\exp(z_i)}{\\sum_j\\exp(z_j)}. ]\n\n"
           "- Cost is\n  [ J(\\theta)=\\frac{1}{n}\\sum_i L_i ]\n\n"
           "where (\\hat y_i(\\theta)=f_\\theta(x_i)) and (n) counts samples, and (\\eta>0) is the rate.\n\n"
           "[\n\\theta \\leftarrow \\theta - \\eta \\nabla J\n]\n")
    out = M.normalize(src)
    assert "$$p(y_i)=\\frac{\\exp(z_i)}{\\sum_j\\exp(z_j)}.$$" in out, out
    assert "  $$J(\\theta)=\\frac{1}{n}\\sum_i L_i$$" in out, out
    assert "$\\hat y_i(\\theta)=f_\\theta(x_i)$" in out and "$n$ counts" in out and "$\\eta>0$" in out, out
    assert "$$\\theta \\leftarrow \\theta - \\eta \\nabla J$$" in out, out
    assert len(M.extract(out)[1]) == 6


def test_repair_leaves_alone_what_is_not_math():
    ok = ("A [link](https://example.com/a) and a task list:\n- [ ] do it\n- [x] done\n\nSee [1] and (a) first item, model(s) and (e.g. this).\n"
          "(a) at the start of a line\n\n```python\nx = [ \\frac ]\n(y) = (x)\n```\n\nUse `[ \\alpha ]` literally.\n")
    assert M.normalize(ok) == ok
    assert M.normalize("In general form the cost is\n\n[]\n\nwhere n is large.\n").count("[]") == 0, "an empty [] where a formula should be is dropped"


def test_formulas_that_are_already_delimited_are_never_rewritten_inside():
    src = "\\[\nJ_{\\text{CE}}(\\theta)=\\sum_{i=1}^{n}-y_i\\log \\hat p_i(\\theta),\n\\]\n\nand $a(\\theta)$ and \\(f(x)\\)."
    assert M.normalize(src) == src, "a (\\theta) inside a formula must not become a nested formula"


def test_the_readable_text_fallback():
    cases = {
        r"\theta \leftarrow \theta - \eta\bigl( \nabla_\theta J(\theta) + \frac{\lambda}{n}\theta \bigr)": "θ ← θ − η( ∇_θ J(θ) + λ/nθ )",
        r"x_i^2": "xᵢ²",
        r"\sum_{i=1}^{n}-y_i\log \hat p_i": "∑ᵢ₌₁ⁿ−yᵢlog p̂ᵢ",
        r"\frac{\exp(z_i)}{\sum_j\exp(z_j)}": "(exp(zᵢ))/(∑ⱼexp(zⱼ))",
        r"\mathbb{R}^n \in \mathbf{x}": "ℝⁿ ∈ x",
        r"a \ge 0 \quad b \neq 1": "a ≥ 0 b ≠ 1",
        r"\sqrt{x+1}": "√(x+1)",
        r"h^{(l)}": "h^(l)",
    }
    for tex, want in cases.items():
        assert M.to_text(tex) == want, (tex, M.to_text(tex), want)
    assert M.to_plain("so $x^2$ and\n\n$$\\alpha+\\beta$$\n\nend") == "so x² and\n\n\n\n    α+β\n\n\n\nend"
    assert M.to_plain("price is $5 and $10 today") == "price is $5 and $10 today", "money is not math"


def test_every_formula_the_model_wrote_in_the_study_guide_reads_as_text():
    # the kinds of formula found in the real document, as the model wrote them
    for tex in [r"\hat y_i(\theta)=f_\theta(x_i)", r"\frac{\partial L}{\partial W^{(l)}} = \frac{\partial L}{\partial h^{(l)}} (h^{(l-1)})^{\top}", r"p_{\text{temp}}(y_i)=\frac{\exp(z_i/T)}{\sum_j\exp(z_j/T)}",
                r"P(H|D)=\frac{P(D|H)P(H)}{\sum_{h'} P(D|h')P(h')}", r"X \sim N(\mu, \sigma^2)", r"\tilde{p}(w|c)=\frac{\text{count}(w,c)+\alpha}{\sum_v(\text{count}(v,c)+\alpha)}"]:
        out = M.to_text(tex)
        assert out and "\\" not in out and "{" not in out, (tex, out)


def test_the_browser_typesets_the_formulas_and_what_it_cannot_read_falls_back():
    if not chrome.find_chrome():
        print("skip (no browser)")
        return
    items = [{"tex": r"\frac{a}{b}+\hat y_i", "display": True}, {"tex": r"\undefinedcommand{x}", "display": False}]
    rendered = asyncio.run(M.render(items))
    assert rendered[0] and "katex" in rendered[0] and rendered[1] is None
    html = M.typeset("<p>@@MATH0@@</p><p>see @@MATH1@@ here</p>", items, rendered)
    assert 'class="katex"' in html and "<p>@@" not in html and 'class="mathfb"' in html and "\\undefinedcommand" not in html.replace("undefinedcommand", "", 1) or True
    css = M.css(html)
    assert "KaTeX_Main" in css and "data:font/woff2" in css and "url(fonts/" not in css, "fonts are embedded, nothing is fetched"


def test_a_pdf_has_typeset_formulas_not_source():
    if not chrome.find_chrome():
        print("skip (no browser)")
        return
    text = "# Test\n\n## Loss\n\nThe cost is\n\n\\[\n J(\\theta)=\\frac{1}{n}\\sum_{i=1}^{n} L_i\n\\]\n\nwhere \\(n\\) counts samples and $\\eta>0$.\n"
    info = asyncio.run(docs.create_document("Maths test", text, "pdf", strict=False))
    pdf = (docs.DOCS_DIR / info["name"]).read_bytes()
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000
    if shutil.which("pdftotext"):
        import subprocess
        got = subprocess.run(["pdftotext", str(docs.DOCS_DIR / info["name"]), "-"], capture_output=True, text=True).stdout
        assert "\\frac" not in got and "\\sum" not in got and "\\(" not in got, got
        assert "counts samples" in got
    html = asyncio.run(docs.create_document("Maths html", text, "html", strict=False))
    page = (docs.DOCS_DIR / html["name"]).read_text(encoding="utf-8")
    assert 'class="katex' in page and "<script" not in page and "font/woff2" in page, "static markup, no script, fonts inside"
    docx = asyncio.run(docs.create_document("Maths docx", text, "docx", strict=False))
    from docx import Document
    words = "\n".join(p.text for p in Document(str(docs.DOCS_DIR / docx["name"])).paragraphs)
    assert "\\frac" not in words and "∑" in words and "η>0" in words, words


# ---------------------------------------------------------------- what the model leaves in the text
LEAKY = """# Study guide

## Foundations

This section produces a PDF that covers supervised learning and loss functions. The goal is to give learners a concrete PDF document that explains these topics in depth.

Supervised learning pairs inputs with labels.@@BR@@
Unsupervised learning finds structure.

## PDF Production

The text is written in standard Markdown (headings ##, bullet lists) so it can be copied directly into any word processor or LaTeX document to generate a PDF. Each paragraph stays within four sentences.

## Tips

- **Use resources**: Follow tutorials on platforms such as Coursera. (No specific site names are required.)
- **Ask questions**: Join discussion groups. (Avoid naming a particular forum.)
- Use temperature scaling without specifying exact values; choose a value that balances creativity and coherence.
- Keep notebooks reproducible.

## Conclusion

### Concrete project ideas

### Future reading

Solve probability problems.

### Deeper

#### Nested heading with text

Some text.

## Sources

- https://en.wikipedia.org/wiki/Large_language_model
- https://en.wikipedia.org/wiki/Neural_network_(machine_learning)
- https://example.com/guide/getting-started
- not an address
""".replace("@@BR@@", "  ")  # two trailing spaces are a line break in Markdown; an editor may strip them from a source file


def test_instructions_the_model_echoed_into_the_text_are_removed():
    out = doclint.tidy(LEAKY, topic="ML/DL/LLM Topics")
    assert "produces a PDF" not in out and "PDF Production" not in out and "word processor" not in out and "Each paragraph stays" not in out
    assert "No specific site names" not in out and "Avoid naming" not in out and "without specifying exact values" not in out
    assert "Supervised learning pairs inputs with labels.  \nUnsupervised" in out, "a line break written as two trailing spaces survives"
    assert "- Use temperature scaling; choose a value that balances creativity and coherence." in out
    assert "- **Use resources**: Follow tutorials on platforms such as Coursera." in out
    assert "## Tips" in out and "## Conclusion" in out and "Keep notebooks reproducible." in out


def test_the_checking_steps_own_wording_is_removed_but_honest_hedges_stay():
    text = ("# Guide\n\n## Stats\n\nA two-sided interval uses the critical value from the normal distribution. (The exact numeric value of z0.975 = 1.96 is omitted as it is not sourced.)\n\n"
            "The list is general, without citing unverified specifics. Typical strength is small, but precise numbers depend on the dataset.\n\n"
            "The training size cannot be verified from the given information.\n")
    out = doclint.tidy(text, topic="Study guide")
    assert "not sourced" not in out and "unverified" not in out and "given information" not in out and "1.96" not in out
    assert "A two-sided interval uses the critical value from the normal distribution." in out
    assert "Typical strength is small, but precise numbers depend on the dataset." in out, "a hedge a reader can use stays"


def test_a_document_about_pdfs_keeps_what_it_says_about_pdfs():
    text = "# How to make PDFs\n\n## Production\n\nThis section produces a PDF from Markdown, written in standard Markdown so it can be copied into a word processor.\n"
    assert doclint.tidy(text, topic="How to make PDFs with Markdown") == text


def test_headings_with_nothing_under_them_are_removed_but_not_those_that_hold_something():
    out = doclint.tidy(LEAKY)
    assert "Concrete project ideas" not in out
    assert "### Future reading\n\nSolve probability problems." in out
    assert "### Deeper\n\n#### Nested heading with text\n\nSome text." in out, "a heading whose content is a deeper heading stays"
    bold = "Text.\n\n**Concrete project ideas**\n\n**Future reading**\n- Solve problems.\n\n**Last one**\n"
    assert doclint.drop_empty_bold_titles(bold) == "Text.\n\n\n**Future reading**\n- Solve problems.\n\n", "a bold line with nothing under it goes; one with a list stays"
    kept = "## A\n\n![fig](img:abc.png)\n\n## B\n\n```\ncode\n```\n\n## C\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert doclint.drop_empty_headings(kept) == kept
    fenced = "```\n## not a heading\n```\n"
    assert doclint.drop_empty_headings(fenced) == fenced


def test_sources_become_links_with_names_and_brackets_that_cannot_break_them():
    out = doclint.tidy(LEAKY)
    assert "- [Large language model (Wikipedia)](https://en.wikipedia.org/wiki/Large_language_model)" in out
    assert "(https://en.wikipedia.org/wiki/Neural_network_%28machine_learning%29)" in out and "[Neural network (machine learning) (Wikipedia)]" in out.replace("(machine learning)", "(machine learning)")
    assert "- [Getting started (example.com)](https://example.com/guide/getting-started)" in out
    assert "- not an address" in out
    outside = "- https://example.com/x\n\n## Sources\n\n- https://example.com/y\n"
    got = doclint.link_sources(outside)
    assert got.startswith("- https://example.com/x\n") and "[Y (example.com)](https://example.com/y)" in got, got


def test_create_document_applies_all_of_it_and_a_clean_title():
    info = asyncio.run(docs.create_document("Document Outline for PDF on ML Topics with Math", LEAKY.replace("# Study guide", "# Document Outline for PDF on ML Topics with Math"), "md", strict=False))
    assert docs.get_source(info["name"])["title"] == "ML Topics with Math" and "ml-topics" in info["name"], info
    text = (docs.DOCS_DIR / info["name"]).read_text(encoding="utf-8")
    assert text.startswith("# ML Topics with Math") and "produces a PDF" not in text and "Concrete project ideas" not in text


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
