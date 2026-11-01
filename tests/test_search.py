"""Tests for the search sources, their health record and the page readers. Offline: every engine is replaced by a fake.
Run: .venv/bin/python -I tests/test_search.py"""

import asyncio
import http.server
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

_home = tempfile.mkdtemp(prefix="sthanik-test-")
os.environ["STHANIK_HOME"] = _home  # before the modules read it
os.environ.pop("LFM3_SEARXNG", None)

import evidence  # noqa: E402
import readers  # noqa: E402
import safebrowsing as sb  # noqa: E402
import sources  # noqa: E402
import tools  # noqa: E402


def rows(*urls, body="Nobel prize physics 2024 laureates"):
    return [{"title": f"Nobel prize physics {i}", "href": u, "body": body} for i, u in enumerate(urls)]


def junk():
    return [{"title": "Login", "href": f"https://junk{i}.example/login", "body": "sign in to continue"} for i in range(5)]


Q = "who won the 2024 Nobel Prize in Physics"


class Fakes:
    """Replace every source with a counter-carrying fake; undone by close()."""

    def __init__(self, **answers):
        self.calls = {}
        self.saved = {}
        sources.HEALTH.reset()
        sb._cache.clear()
        sb._stale.clear()
        names = {"yahoo": (sb, "_yahoo"), "bing": (sb, "_bing"), "wikipedia": (sources, "wikipedia"), "stackexchange": (sources, "stackexchange"),
                 "marginalia": (sources, "marginalia"), "searxng": (sources, "searxng")}
        for name, (mod, attr) in names.items():
            self.saved[name] = getattr(mod, attr)
            setattr(mod, attr, self._make(name, answers.get(name, [])))

    def _make(self, name, answer):
        def fake(*a, **k):
            self.calls[name] = self.calls.get(name, 0) + 1
            if callable(answer):
                return answer()
            if isinstance(answer, Exception):
                raise answer
            return answer
        return fake

    def close(self):
        for name, (mod, attr) in {"yahoo": (sb, "_yahoo"), "bing": (sb, "_bing"), "wikipedia": (sources, "wikipedia"), "stackexchange": (sources, "stackexchange"),
                                  "marginalia": (sources, "marginalia"), "searxng": (sources, "searxng")}.items():
            setattr(mod, attr, self.saved[name])


def test_a_failing_source_is_left_alone_and_tried_again_later():
    now = [1000.0]
    h = sources.Health(clock=lambda: now[0])
    h.failure("bing", "blocked")
    assert h.allowed("bing"), "one failure is not enough to give up on a source"
    h.failure("bing", "blocked")
    assert not h.allowed("bing") and h.snapshot()["bing"]["retry_in"] == 30
    now[0] += 31
    assert h.allowed("bing"), "after the pause it gets one more try"
    h.failure("bing", "blocked")
    assert not h.allowed("bing") and h.snapshot()["bing"]["retry_in"] == 60, "each failure in a row doubles the pause"
    for _ in range(12):
        h.failure("bing", "blocked")
    assert h.snapshot()["bing"]["retry_in"] == 600, "but never beyond ten minutes"
    now[0] += 601
    h.success("bing", 0.4)
    assert h.allowed("bing") and h.snapshot()["bing"]["failures_in_a_row"] == 0


def test_a_source_that_keeps_answering_with_junk_goes_to_the_back():
    now = [0.0]
    h = sources.Health(clock=lambda: now[0])
    for _ in range(4):
        h.junk("bing", 0.3)
    assert h.demoted("bing") and h.order(["bing", "yahoo"]) == ["yahoo", "bing"]
    now[0] += 301
    assert not h.demoted("bing"), "the demotion wears off"
    h.junk("bing", 0.3)
    h.success("bing", 0.3)
    assert not h.demoted("bing") and h.snapshot()["bing"]["unrelated_in_a_row"] == 0


def test_the_general_engines_and_wikipedia_are_merged():
    f = Fakes(yahoo=rows("https://nobelprize.org/a", "https://en.wikipedia.org/wiki/X"), bing=junk(), wikipedia=rows("https://en.wikipedia.org/wiki/X", "https://en.wikipedia.org/wiki/Y"))
    try:
        got = sb.search("text", Q, 6)
        urls = [r["href"] for r in got]
        assert urls[0] == "https://en.wikipedia.org/wiki/X", "a page two sources agree on ranks first"
        assert set(urls) == {"https://nobelprize.org/a", "https://en.wikipedia.org/wiki/X", "https://en.wikipedia.org/wiki/Y"}, urls
        assert not any("junk" in u for u in urls), "the engine that answered with unrelated pages is left out"
        assert sources.HEALTH.snapshot()["bing"]["unrelated_in_a_row"] == 1
        assert "stackexchange" not in f.calls, "Stack Overflow is only asked about programming"
    finally:
        f.close()


def test_stack_overflow_is_asked_about_programming_only():
    assert sources.looks_technical("how to fix ModuleNotFoundError in a PyInstaller build") and sources.looks_technical("git rebase conflict")
    assert not sources.looks_technical("how long to boil an egg") and not sources.looks_technical("who won the 2024 Nobel Prize")
    f = Fakes(yahoo=rows("https://a.example/x", body="python error module"), wikipedia=rows("https://en.wikipedia.org/wiki/Python", body="python error module"),
              stackexchange=rows("https://stackoverflow.com/q/1", body="python error module"))
    try:
        sb.search("text", "python module error", 6)
        assert f.calls.get("stackexchange") == 1
    finally:
        f.close()


def test_when_the_main_engines_fail_the_others_carry_the_search_and_the_failing_ones_are_skipped():
    boom = RuntimeError("HTTP 429")
    f = Fakes(yahoo=boom, bing=boom, wikipedia=rows("https://en.wikipedia.org/wiki/Nobel"), marginalia=rows("https://small.example/nobel"))
    try:
        got = sb.search("text", Q, 6)
        assert {r["href"] for r in got} == {"https://en.wikipedia.org/wiki/Nobel", "https://small.example/nobel"}, got
        before = dict(f.calls)
        sb._cache.clear()
        sb.search("text", Q + " now", 6)  # the second failure opens both circuits
        sb._cache.clear()
        mark = dict(f.calls)
        got = sb.search("text", Q + " again", 6)
        assert got, "still answers"
        assert f.calls.get("yahoo") == mark.get("yahoo") and f.calls.get("bing") == mark.get("bing"), "failing engines were not asked again"
        snap = sources.HEALTH.snapshot()
        assert not snap["yahoo"]["working"] and snap["yahoo"]["last_error"].startswith("RuntimeError") and before["yahoo"] >= 1
    finally:
        f.close()


def test_when_everything_fails_the_last_answer_to_the_same_search_is_shown_and_marked():
    f = Fakes(yahoo=rows("https://nobelprize.org/a"), wikipedia=rows("https://en.wikipedia.org/wiki/X"))
    try:
        good = sb.search("text", Q, 6)
        assert good and not good[0].get("stale")
        sb._cache.clear()  # five minutes later: the cache has expired, the saved answer has not
        boom = RuntimeError("network down")
        for name, (mod, attr) in {"yahoo": (sb, "_yahoo"), "bing": (sb, "_bing"), "wikipedia": (sources, "wikipedia"), "marginalia": (sources, "marginalia")}.items():
            def fail(*a, _b=boom, **k):
                raise _b
            setattr(mod, attr, fail)
        sources.HEALTH.reset()
        again = sb.search("text", Q, 6)
        assert [r["href"] for r in again] == [r["href"] for r in good] and all(r["stale"] for r in again)
        res = asyncio.run(tools._web_search({"query": Q}))
        assert "earlier identical search" in res.text, res.text[:200]
        try:
            sb.search("text", "a query never asked before", 6)
        except Exception as e:  # noqa: BLE001
            assert "network down" in str(e)
        else:
            raise AssertionError("with nothing saved, a total failure must be an error and not an empty answer")
        sb.set_safe(True)  # switching mode forgets saved answers: one mode's results are never shown in the other
        assert not sb._stale
    finally:
        f.close()


def test_when_every_source_is_marked_down_they_are_all_tried_so_recovery_is_noticed_at_once():
    f = Fakes(yahoo=rows("https://nobelprize.org/a"))
    try:
        for name in ("yahoo", "bing", "wikipedia", "marginalia", "stackexchange"):
            for _ in range(3):
                sources.HEALTH.failure(name, "was down")
        assert not any(sources.HEALTH.allowed(n) for n in ("yahoo", "bing", "wikipedia", "marginalia"))
        got = sb.search("text", Q, 6)
        assert [r["href"] for r in got] == ["https://nobelprize.org/a"], "the pause is ignored when there is nothing else to ask"
        assert sources.HEALTH.allowed("yahoo"), "and an answer ends it"
    finally:
        f.close()


def test_a_slow_source_does_not_hold_up_a_search_that_already_has_a_good_answer():
    f = Fakes(yahoo=rows("https://nobelprize.org/a"), bing=lambda: (time.sleep(3.5), rows("https://slow.example/x"))[1])
    try:
        t = time.time()
        got = sb.search("text", Q, 6)
        took = time.time() - t
        assert took < 3.0, f"waited {took:.1f}s for a slow engine"
        assert [r["href"] for r in got][0] == "https://nobelprize.org/a"
    finally:
        f.close()


def test_a_query_about_adult_themes_only_goes_to_engines_that_can_filter_it():
    f = Fakes(yahoo=rows("https://a.example/x", body="porn videos"), bing=rows("https://b.example/x", body="porn videos"), wikipedia=rows("https://en.wikipedia.org/wiki/P", body="porn videos"),
              marginalia=rows("https://m.example/x", body="porn videos"))
    try:
        sb.set_safe(True)
        sb.search("text", "porn videos", 6)
        assert f.calls.get("wikipedia") is None and f.calls.get("marginalia") is None and f.calls.get("stackexchange") is None, f.calls
        # an ordinary query in strict mode: rows from the extra sources that look adult are dropped
        f2 = Fakes(yahoo=rows("https://nobelprize.org/a"), wikipedia=[{"title": "Nobel prize physics", "href": "https://xxx-site.example/a", "body": "Nobel prize physics 2024"}] + rows("https://en.wikipedia.org/wiki/N"))
        try:
            urls = [r["href"] for r in sb.search("text", Q, 6)]
            assert "https://xxx-site.example/a" not in urls and "https://en.wikipedia.org/wiki/N" in urls, urls
        finally:
            f2.close()
    finally:
        f.close()


def test_a_long_question_is_shortened_to_its_telling_words_for_stack_overflow():
    assert sources._key_terms("how to fix ModuleNotFoundError in a PyInstaller onedir build") == "fix ModuleNotFoundError PyInstaller onedir" or \
        sources._key_terms("how to fix ModuleNotFoundError in a PyInstaller onedir build") == "ModuleNotFoundError PyInstaller onedir build"
    asked = []
    saved = sources._get

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake(url, params=None, timeout=8):
        asked.append(params["q"])
        return R({"items": [] if len(asked) == 1 else [{"title": "T", "link": "https://stackoverflow.com/q/1", "tags": []}]})

    sources._get = fake
    try:
        got = sources.stackexchange("how to fix ModuleNotFoundError in a PyInstaller onedir build", 3)
        assert len(asked) == 2 and asked[1] != asked[0] and got and got[0]["href"].endswith("/q/1"), asked
    finally:
        sources._get = saved


def test_the_backup_index_is_only_asked_when_little_else_came_back():
    f = Fakes(yahoo=RuntimeError("blocked"), bing=RuntimeError("blocked"), wikipedia=rows("https://en.wikipedia.org/wiki/A", "https://en.wikipedia.org/wiki/B", "https://en.wikipedia.org/wiki/C"),
              marginalia=rows("https://m.example/x"))
    try:
        sb.search("text", Q, 6)
        assert not f.calls.get("marginalia"), "three good rows are enough"
    finally:
        f.close()
    f = Fakes(yahoo=RuntimeError("blocked"), bing=RuntimeError("blocked"), wikipedia=rows("https://en.wikipedia.org/wiki/A"), marginalia=rows("https://m.example/x"))
    try:
        got = sb.search("text", Q, 6)
        assert f.calls.get("marginalia") == 1 and "https://m.example/x" in [r["href"] for r in got]
    finally:
        f.close()


def test_the_extra_sources_can_be_turned_off():
    f = Fakes(yahoo=rows("https://nobelprize.org/a"), wikipedia=rows("https://en.wikipedia.org/wiki/X"), marginalia=rows("https://m.example/x"))
    os.environ["LFM3_SEARCH_EXTRAS"] = "0"
    try:
        assert not sources.extras_enabled()
        got = sb.search("text", Q, 6)
        assert [r["href"] for r in got] == ["https://nobelprize.org/a"] and not f.calls.get("wikipedia") and not f.calls.get("marginalia"), f.calls
        os.environ.pop("LFM3_SEARCH_EXTRAS")
        assert sources.extras_enabled()
        cfg = os.path.join(_home, "config")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "search.json"), "w") as fh:
            fh.write('{"extras": false}')
        assert not sources.extras_enabled()
        os.remove(os.path.join(cfg, "search.json"))
    finally:
        os.environ.pop("LFM3_SEARCH_EXTRAS", None)
        f.close()


def test_sources_read_what_the_public_interfaces_return():
    saved = sources._get

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake(url, params=None, timeout=8):
        if "wikipedia" in url:
            return R({"pages": [{"title": "Nobel Prize in Physics", "key": "Nobel_Prize_in_Physics", "description": "Award", "excerpt": "the <span class=\"searchmatch\">Nobel</span> Prize &amp; more"}]})
        if "stackexchange" in url:
            return R({"items": [{"title": "Module not found &#39;x&#39;", "link": "https://stackoverflow.com/q/1", "answer_count": 3, "score": 9, "accepted_answer_id": 5, "tags": ["python", "pyinstaller"]}]})
        if "marginalia" in url:
            return R({"results": [{"url": "https://small.example/a", "title": "A <b>page</b>", "description": "About things"}]})
        return R({"results": [{"url": "https://my.example/a", "title": "Mine", "content": "From <i>my</i> instance"}]})

    sources._get = fake
    try:
        w = sources.wikipedia("nobel", 3)[0]
        assert w["href"] == "https://en.wikipedia.org/wiki/Nobel_Prize_in_Physics" and w["body"] == "Award. the Nobel Prize & more" and w["title"].endswith("Wikipedia")
        s = sources.stackexchange("x", 3)[0]
        assert s["title"] == "Module not found 'x' - Stack Overflow" and "accepted answer, 3 answers, score 9" in s["body"]
        assert sources.marginalia("x y", 3)[0]["title"] == "A page"
        os.environ["LFM3_SEARXNG"] = "https://my.example/"
        assert sources.searxng_address() == "https://my.example" and sources.searxng("q", 3, True)[0]["body"] == "From my instance"
        os.environ["LFM3_SEARXNG"] = "ftp://nope"
        assert sources.searxng_address() == "" and sources.searxng("q", 3, True) == [], "only http(s) addresses are accepted"
    finally:
        sources._get = saved
        os.environ.pop("LFM3_SEARXNG", None)


def test_wikipedia_and_stack_overflow_pages_are_read_through_their_interfaces():
    calls = []
    saved = readers._get

    async def fake(client, url, params):
        calls.append((url, params))
        if "w/api.php" in url:
            return {"query": {"pages": [{"title": "2024 Nobel Prizes", "extract": "The 2024 Nobel Prizes were awarded."}]}}
        if url.endswith("/answers"):
            return {"items": [{"body": "<p>Use <code>--hidden-import</code></p><pre><code>pyinstaller --hidden-import x app.py</code></pre>", "score": 3, "is_accepted": False},
                              {"body": "<p>Accepted one</p>", "score": 1, "is_accepted": True}]}
        return {"items": [{"title": "Module &amp; path", "body": "<p>It fails</p>", "score": 7, "tags": ["python"]}]}

    readers._get = fake
    try:
        title, text = asyncio.run(readers.read("https://en.wikipedia.org/wiki/2024_Nobel_Prizes"))
        assert title == "2024 Nobel Prizes" and "were awarded" in text and calls[0][1]["titles"] == "2024 Nobel Prizes"
        title, text = asyncio.run(readers.read("https://stackoverflow.com/questions/15114695/imported-module-not-found"))
        assert title == "Module & path" and calls[-1][1]["site"] == "stackoverflow"
        assert text.index("Accepted one") < text.index("--hidden-import"), "the accepted answer comes first"
        assert "    pyinstaller --hidden-import x app.py" in text, "code stays code"
        assert asyncio.run(readers.read("https://unix.stackexchange.com/questions/9/x")) is not None and calls[-1][1]["site"] == "unix"
        assert asyncio.run(readers.read("https://example.com/wiki/Thing")) is None, "any other address is read as an ordinary page"
        assert asyncio.run(readers.read("https://stackoverflow.com/users/1/someone")) is None
    finally:
        readers._get = saved


class _Site(http.server.BaseHTTPRequestHandler):
    hits: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        _Site.hits[self.path] = _Site.hits.get(self.path, 0) + 1
        if self.path == "/flaky" and _Site.hits[self.path] == 1:
            self.send_response(403)
            self.end_headers()
            return
        if self.path in ("/blocked", "/missing"):
            self.send_response(403 if self.path == "/blocked" else 404)
            self.end_headers()
            return
        if self.path == "/go":
            self.send_response(302)
            self.send_header("Location", "/flaky")
            self.end_headers()
            return
        body = b"<html><title>Hello</title><body><main><p>The answer is 42.</p></main></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_pages_that_refuse_once_are_asked_again_and_refusals_say_what_to_do_next():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    saved = tools._check_public_host

    async def allow(host):
        return None

    tools._check_public_host = allow
    try:
        got = asyncio.run(tools._fetch_url({"url": base + "/flaky"}))
        assert "The answer is 42." in got.text and _Site.hits["/flaky"] == 2, "a 403 is retried once"
        _Site.hits.clear()
        got = asyncio.run(tools._fetch_url({"url": base + "/go"}))
        assert "The answer is 42." in got.text, "a redirect to a page that refuses once still works"
        for path, words in (("/blocked", "does not let programs read"), ("/missing", "no longer exists")):
            try:
                asyncio.run(tools._fetch_url({"url": base + path}))
            except tools.ToolError as e:
                assert words in str(e) and "another of the search results" in str(e), str(e)
            else:
                raise AssertionError(path + " did not fail")
        assert _Site.hits["/blocked"] == 2, "a refusal is tried twice, not more"
    finally:
        tools._check_public_host = saved
        srv.shutdown()


PAGE_NOBEL = """Cookie notice: we use cookies. Accept all cookies. Privacy policy.

The Nobel Prize in Physics 2024 was awarded jointly to John J. Hopfield and Geoffrey Hinton "for foundational discoveries and inventions that enable machine learning with artificial neural networks".

Physics Chemistry Medicine Literature Peace Economics Facts Prizes Nominations Press Archive

Hopfield created an associative memory that can store and reconstruct images. Hinton used the Hopfield network as the foundation of a new network that uses a different method.

The prize amount is 11 million Swedish kronor, to be shared equally between the laureates."""


def test_a_page_is_cut_into_passages_at_sentence_ends():
    text = " ".join(f"This is sentence number {i} of a rather long paragraph about nothing in particular." for i in range(30))
    ps = evidence.split_passages(text, 480)
    assert 3 <= len(ps) <= 12 and all(len(p) <= 480 + 100 for p in ps), [len(p) for p in ps]
    assert all(p.endswith(".") for p in ps), "cut at the end of a sentence"
    assert evidence.split_passages("Too short.\n\nAlso short.") == [], "scraps are not passages"
    run = "word " * 400
    assert all(len(p) <= 720 for p in evidence.split_passages(run, 480)), "a long run with no full stop is cut too"


def test_the_passage_that_answers_the_question_is_chosen_not_the_page_furniture():
    got = evidence.best_passages("who won the 2024 Nobel Prize in Physics", {0: PAGE_NOBEL}, per_page=1)
    assert "Hopfield and Geoffrey Hinton" in got[0][0] and "cookies" not in got[0][0], got
    got = evidence.best_passages("what is the prize amount in Swedish kronor", {0: PAGE_NOBEL}, per_page=1)
    assert "11 million" in got[0][0]
    a = "The committee will meet again soon, in the main hall of the old academy building."
    b = "The committee will meet again in 1901, in the main hall of the old academy building."
    assert evidence.best_passages("when will the committee meet", {0: a + "\n\n" + b}, per_page=1)[0][0] == b, "a question about a date prefers a passage with a number"
    assert evidence.best_passages("who will the committee meet", {0: a + "\n\n" + b}, per_page=1)[0][0] == a, "and only then"
    assert evidence.best_passages("completely unrelated quantum gravity", {0: PAGE_NOBEL}) == {}, "nothing is better than something off topic"
    assert evidence.best_passages("Hopfield tractor", {0: PAGE_NOBEL}) == {}, "a two-word question must match both words"
    menu = "How to Peel a Soft-Boiled Egg How Long Do Soft-Boiled Eggs Keep? Storage Tips Recipes Related"
    real = "Boil the eggs for 6 to 7 minutes to reach a soft-boiled consistency, then cool them in ice water."
    got = evidence.best_passages("how long to boil a soft boiled egg", {0: menu + "\n\n" + real}, per_page=1)
    assert got[0][0] == real, got


def test_two_passages_per_page_are_not_neighbours_and_come_back_in_reading_order():
    para = lambda w: f"The {w} paragraph says that the Nobel Prize in Physics 2024 went to Hopfield and Hinton and talks about {w} for a while longer."
    text = "\n\n".join(para(w) for w in ("first", "second", "third", "fourth", "fifth"))
    got = evidence.best_passages("Nobel Prize in Physics 2024 Hopfield Hinton", {0: text}, per_page=2)[0]
    assert len(got) == 2 and text.index(got[0]) < text.index(got[1])
    assert not (("first" in got[0] and "second" in got[1]) or ("second" in got[0] and "third" in got[1])), got


def test_pages_are_read_together_slow_and_refusing_ones_are_dropped_and_refusers_remembered():
    evidence.clear()
    asked = []

    async def load(url):
        asked.append(url)
        if "slow" in url:
            await asyncio.sleep(5)
        if "refuse" in url:
            raise tools.ToolError("refuse.example does not let programs read this page (HTTP 403). Use another of the search results instead.")
        if "broken" in url:
            raise RuntimeError("boom")
        return "T", PAGE_NOBEL, url

    rows_ = [{"href": h} for h in ("https://ok.example/a", "https://slow.example/b", "https://refuse.example/c", "https://broken.example/d", "https://later.example/e", "https://ok.example/f.pdf")]
    t = time.time()
    got = asyncio.run(evidence.excerpts("who won the 2024 Nobel Prize in Physics", rows_, load, pages=4, timeout=1.5))
    assert time.time() - t < 3.0, "the slow page did not hold the search up"
    assert list(got) == [0] and "Hopfield" in got[0][0], got
    assert asked == ["https://ok.example/a", "https://slow.example/b", "https://refuse.example/c", "https://broken.example/d"], "the first four readable rows only, never the pdf"
    assert evidence.is_blocked("refuse.example") and not evidence.is_blocked("broken.example")
    asked.clear()
    asyncio.run(evidence.excerpts("who won the 2024 Nobel Prize", [{"href": "https://refuse.example/c"}, {"href": "https://ok.example/z"}], load))
    assert asked == ["https://ok.example/z"], "a site that refused is not asked again"
    evidence.clear()
    assert not evidence.is_blocked("refuse.example")


def test_web_search_attaches_the_passages_and_still_answers_when_reading_fails():
    saved_search, saved_load = sb.search, tools._load_page
    evidence.clear()
    found = [{"title": "T " * 100, "href": "https://ok.example/a", "body": "snippet one"}, {"title": "Second", "href": "https://bad.example/b", "body": "snippet two"}]
    loaded = []

    async def load(url):
        loaded.append(url)
        if "bad" in url:
            raise tools.ToolError("nope")
        return "Page", PAGE_NOBEL, url

    sb.search = lambda kind, q, n: found
    tools._load_page = load
    try:
        res = asyncio.run(tools._web_search({"query": "who won the 2024 Nobel Prize in Physics"}))
        assert "From the page: " in res.text and "John J. Hopfield" in res.text and "snippet two" in res.text, res.text
        assert res.text.count("From the page") == 1, "the page that could not be read has only its snippet"
        assert len(res.text.splitlines()[0]) < 140, "a stuffed title is cut"
        loaded.clear()
        res = asyncio.run(tools._web_search({"query": "who won the 2024 Nobel Prize in Physics", "read_pages": False}))
        assert "From the page" not in res.text and not loaded, "plan mode reads pages itself and asks for snippets only"
        os.environ["LFM3_SEARCH_READ"] = "0"
        try:
            res = asyncio.run(tools._web_search({"query": "who won the 2024 Nobel Prize in Physics"}))
            assert "From the page" not in res.text and not loaded, "reading can be switched off for good"
        finally:
            os.environ.pop("LFM3_SEARCH_READ")
        found[0] = {**found[0], "loose": True}
        res = asyncio.run(tools._web_search({"query": "who won the 2024 Nobel Prize in Physics"}))
        assert not loaded and "loosely related" in res.text, "loosely related results are not opened"
    finally:
        sb.search, tools._load_page = saved_search, saved_load


def test_a_page_read_for_a_search_is_not_fetched_again_when_the_model_asks_for_it():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    saved = tools._check_public_host

    async def allow(host):
        return None

    tools._check_public_host = allow
    evidence.clear()
    _Site.hits.clear()
    try:
        asyncio.run(tools._fetch_url({"url": base + "/page"}))
        again = asyncio.run(tools._fetch_url({"url": base + "/page"}))
        assert "The answer is 42." in again.text and _Site.hits["/page"] == 1, _Site.hits
        sb.set_safe(True)  # switching the private session forgets pages as well
        asyncio.run(tools._fetch_url({"url": base + "/page"}))
        assert _Site.hits["/page"] == 2
    finally:
        tools._check_public_host = saved
        srv.shutdown()


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
