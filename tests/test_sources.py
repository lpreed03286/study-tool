"""The check that a citation is real, against a small local website."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import sources
from sources import CONFIRMED, UNCHECKED, Checker, Refused, check_blocks, fetch, html_text, plain_words

LONG = "Plenty of ordinary words on this page make it clearly a real page. " * 30
QUOTE = "the restoring force is proportional to the displacement"
ROUTES = {
    "/good": (200, "text/html; charset=utf-8", f"<html><script>var hidden = 'secret words only in a script';</script><p>{LONG}</p>"
                                               f"<p>In a spring, <em>the restoring force</em> is proportional\n to the displacement.</p></html>"),
    "/shell": (200, "text/html", "<html><div id=app></div><script src=a.js></script></html>"),
    "/plain": (200, "text/plain", f"{LONG} {QUOTE}"),
    "/image": (200, "image/png", "not really"),
    "/blocked": (403, "text/html", "no robots"),
    "/busy": (429, "text/html", "slow down"),
    "/gone": (404, "text/html", "missing"),
    "/broken": (500, "text/html", "error"),
}


@pytest.fixture(scope="module")
def site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/good")
                self.end_headers()
                return
            if self.path == "/to-file":
                self.send_response(302)
                self.send_header("Location", "file:///etc/passwd")
                self.end_headers()
                return
            code, kind, text = ROUTES[self.path]
            body = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    running = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}"
    running.shutdown()


@pytest.fixture
def checker(tmp_path):
    return Checker(tmp_path, allow_local=True)


def source(url, quote=QUOTE, title="A page"):
    return {"title": title, "url": url, "quote": quote}


def test_a_quote_on_the_page_is_confirmed_despite_markup_case_and_spacing(site, checker):
    result = checker.check(source(f"{site}/good", "The Restoring Force is  PROPORTIONAL to the displacement!"))
    assert result["status"] == CONFIRMED and result["kind"] == "web" and result["title"] == "A page"


def test_text_inside_a_script_does_not_count(site, checker):
    with pytest.raises(Refused, match="quote isn't on"):
        checker.check(source(f"{site}/good", "secret words only in a script"))


def test_a_quote_that_is_not_on_the_page_is_refused(site, checker):
    with pytest.raises(Refused, match="quote isn't on"):
        checker.check(source(f"{site}/good", "the restoring force is unrelated to the displacement"))


@pytest.mark.parametrize("path", ["/gone", "/broken"])
def test_a_page_that_does_not_open_is_refused(site, checker, path):
    with pytest.raises(Refused, match="doesn't open"):
        checker.check(source(site + path))


def test_a_site_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(Refused, match="couldn't find"):
        Checker(tmp_path).check(source("https://no-such-site.invalid/page"))


@pytest.mark.parametrize("path,words", [("/blocked", "403"), ("/busy", "429"), ("/image", "image/png"), ("/shell", "JavaScript")])
def test_what_cannot_be_read_is_kept_but_labeled_with_the_reason(site, checker, path, words):
    result = checker.check(source(site + path))
    assert result["status"] == UNCHECKED and words in result["note"]


def test_plain_text_is_read_and_a_redirect_gives_the_final_address(site, checker):
    assert checker.check(source(f"{site}/plain"))["status"] == CONFIRMED
    moved = checker.check(source(f"{site}/moved"))
    assert moved["status"] == CONFIRMED and moved["url"] == f"{site}/good"


def test_a_redirect_cannot_leave_the_web(site, checker):
    with pytest.raises(Refused):
        checker.check(source(f"{site}/to-file"))


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/x", "http://localhost:9/x", "http://192.168.1.1/x", "http://10.0.0.1/x",
                                 "http://169.254.169.254/latest", "http://[::1]/x"])
def test_this_machine_and_private_networks_are_refused(url, tmp_path):
    with pytest.raises(Refused, match="private address"):
        Checker(tmp_path).check(source(url))


def test_a_redirect_to_a_private_address_is_refused_too(site, monkeypatch):
    # The first hop is allowed by pretending it's public; the redirect handler still checks.
    real = sources._refuse_private
    calls = []

    def first_hop_public(url):
        calls.append(url)
        if len(calls) > 1:
            real(url)

    monkeypatch.setattr(sources, "_refuse_private", first_hop_public)
    assert "private address" in fetch(f"{site}/moved").refused


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/x", "data:text/html,hi", "not a url"])
def test_only_web_addresses_are_accepted(url, tmp_path):
    with pytest.raises(Refused, match="isn't a web address"):
        Checker(tmp_path).check(source(url))


@pytest.mark.parametrize("fields,words", [
    ({"title": "T", "quote": QUOTE}, "url or a file"),
    ({"url": "http://x", "quote": QUOTE}, "title"),
    ({"title": "T", "url": "http://x"}, "quote"),
    ({"title": "T", "url": "http://x", "quote": "too short"}, "quote"),
    ({"title": "T", "url": "http://x", "quote": "word " * 61}, "quote"),
    ({"title": "T", "file": "/no/such.pdf", "page": 1, "quote": QUOTE}, "no file"),
])
def test_an_incomplete_source_is_refused(fields, words, tmp_path):
    with pytest.raises(Refused, match=words):
        Checker(tmp_path).check(fields)


def test_each_page_is_only_fetched_once(site, checker, monkeypatch):
    calls = []
    real = sources.fetch
    monkeypatch.setattr(sources, "fetch", lambda url, local: (calls.append(url), real(url, local))[1])
    url = f"{site}/good"
    blocks = [{"type": "text", "text": "a", "sources": [source(url)]}, {"type": "note", "text": "b", "sources": [source(url)]}]
    checked, failed = check_blocks(blocks, checker)
    assert failed == [] and calls == [url] and checked[1]["sources"][0]["status"] == CONFIRMED


def test_plots_and_verified_examples_need_no_source_but_claims_do():
    checker = Checker("/tmp")
    _, failed = check_blocks([{"type": "plot"}, {"type": "diagram"}, {"type": "example", "method": "exact"},
                              {"type": "example", "problem": "p"}, {"type": "text", "text": "x", "own_words": True},
                              {"type": "equation", "latex": "x"}], checker)
    assert [f["index"] for f in failed] == [3, 5]


def test_helpers():
    assert plain_words("It’s  &amp; “F=ma”!") == "it s f ma"
    assert "x" not in html_text("<style>x{}</style><p>y</p>") and "y" in html_text("<p>y</p>")
