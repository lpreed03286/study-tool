"""Checks citations when they're saved, so a made-up link or quote can't get through.

Each web page is opened here and the quote looked for in its text. Nothing here trusts
that the writer read the page. A site that refuses automated checks is kept, labeled
as not checked. A page that's missing, or that doesn't contain the quote, is refused.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from library import Library, LibraryError

CONFIRMED = "confirmed"  # The quote was found in the page's text.
UNCHECKED = "unchecked"  # The page couldn't be read automatically, and the page says so.

MIN_QUOTE_WORDS = 4
MAX_QUOTE_WORDS = 60
MAX_PAGE_BYTES = 8_000_000
MAX_FILE_BYTES = 200_000_000
FETCH_SECONDS = 20
FETCH_WORKERS = 8
SCRIPTED_PAGE_CHARS = 800  # Less text than this usually means the page is drawn by JavaScript.
BLOCKED_CODES = {401, 403, 406, 429, 451, 503}
FILE_TYPES = {".pdf", ".png", ".jpg", ".jpeg"}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
NEEDS_SOURCE = {"text", "list", "equation", "table", "note", "definition", "nomenclature"}


class Refused(Exception):
    """Why a citation can't be used, for Claude to fix."""


@dataclass
class Page:
    final_url: str = ""
    text: str | None = None  # None when the text couldn't be read.
    problem: str = ""  # Why it couldn't be checked, if it can still be kept.
    refused: str = ""  # Why it can't be kept at all.


def plain_words(text: str) -> str:
    """Punctuation and spacing differ between a quote and a page, so only words count."""
    folded = unicodedata.normalize("NFKC", html.unescape(text)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", folded))


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(markup: str) -> str:
    parser = _TextOnly()
    parser.feed(markup)
    return " ".join(parser.parts)


def pdf_text(path: Path, page: int | None = None) -> str | None:
    """None if there's no pdftotext here, since that says nothing about the source."""
    pages = ["-f", str(page), "-l", str(page)] if page else []
    try:
        done = subprocess.run(["pdftotext", *pages, str(path), "-"], capture_output=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return done.stdout.decode(errors="replace") if done.returncode == 0 else None


def pdf_page_count(path: Path) -> int | None:
    try:
        done = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    found = re.search(r"^Pages:\s+(\d+)", done.stdout, re.M)
    return int(found.group(1)) if found else None


def _refuse_private(url: str) -> None:
    host = urlparse(url).hostname or ""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        raise Refused(f"couldn't find the site {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise Refused(f"{host} is a private address, which a student can't check")


def _opener(allow_local: bool):
    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if urlparse(newurl).scheme not in ("http", "https"):
                raise Refused(f"it redirects to {newurl}")
            if not allow_local:
                _refuse_private(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(Redirects)


def fetch(url: str, allow_local: bool = False) -> Page:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return Page(refused=f"{url} isn't a web address")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"})
    try:
        if not allow_local:
            _refuse_private(url)
        with _opener(allow_local).open(request, timeout=FETCH_SECONDS) as reply:
            final, kind = reply.geturl(), reply.headers.get_content_type()
            charset = reply.headers.get_content_charset() or "utf-8"
            body = reply.read(MAX_PAGE_BYTES + 1)
    except Refused as reason:
        return Page(refused=str(reason))
    except urllib.error.HTTPError as error:
        if error.code in BLOCKED_CODES:
            return Page(final_url=url, problem=f"the site refused the automatic check (HTTP {error.code})")
        return Page(refused=f"{url} doesn't open (HTTP {error.code})")
    except (TimeoutError, socket.timeout, ssl.SSLError) as error:
        return Page(final_url=url, problem=f"the site didn't answer the automatic check ({type(error).__name__})")
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout, ssl.SSLError)):
            return Page(final_url=url, problem="the site didn't answer the automatic check")
        return Page(refused=f"{url} doesn't open ({error.reason})")
    if len(body) > MAX_PAGE_BYTES:
        return Page(final_url=final, problem="the page is too large to check automatically")
    if kind == "application/pdf" or body[:5] == b"%PDF-":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as copy:
            copy.write(body)
            copy.flush()
            text = pdf_text(Path(copy.name))
        if text is None:
            return Page(final_url=final, problem="this PDF can't be read automatically")
        return Page(final_url=final, text=text)
    if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
        return Page(final_url=final, problem=f"a {kind} file can't be read automatically")
    decoded = body.decode(charset, errors="replace")
    return Page(final_url=final, text=decoded if kind == "text/plain" else html_text(decoded))


class Checker:
    def __init__(self, data: Path, allow_local: bool = False):
        self.data = Path(data)
        self.allow_local = allow_local
        self.pages: dict[str, Page] = {}
        self.page_text: dict[tuple[str, int | None], str | None] = {}

    def prefetch(self, sources: list[dict]) -> None:
        wanted = {s["url"] for s in sources if isinstance(s, dict) and s.get("url")} - set(self.pages)
        if wanted:
            with ThreadPoolExecutor(FETCH_WORKERS) as pool:
                for url, page in zip(wanted, pool.map(lambda u: fetch(u, self.allow_local), wanted)):
                    self.pages[url] = page

    @staticmethod
    def _quote(source: dict) -> str:
        quote = str(source.get("quote") or "").strip()
        words = len(plain_words(quote).split())
        if not MIN_QUOTE_WORDS <= words <= MAX_QUOTE_WORDS:
            raise Refused(f"each source needs a word-for-word quote of {MIN_QUOTE_WORDS} to {MAX_QUOTE_WORDS} words, and this has {words}")
        return quote

    def check(self, source: dict) -> dict:
        title = str(source.get("title") or "").strip()
        if not title:
            raise Refused("each source needs a title")
        quote = self._quote(source)
        if source.get("library"):
            return self._check_library(source, title, quote)
        if source.get("file"):
            return self._check_file(source, title, quote)
        return self._check_url(source, title, quote)

    def _result(self, base: dict, quote: str, text: str | None, problem: str, where: str) -> dict:
        checked = {**base, "quote": quote, "checked_at": time.time()}
        if text is None:
            return {**checked, "status": UNCHECKED, "note": problem}
        if plain_words(quote) in plain_words(text):
            return {**checked, "status": CONFIRMED, "note": ""}
        if len(plain_words(text)) < SCRIPTED_PAGE_CHARS and where == "page":
            return {**checked, "status": UNCHECKED, "note": "this page shows its text with JavaScript, so it can't be read automatically"}
        raise Refused(f"the quote isn't on {where}: {quote[:80]}")

    def _check_url(self, source: dict, title: str, quote: str) -> dict:
        url = str(source.get("url") or "").strip()
        if not url:
            raise Refused("each source needs a url or a file")
        self.prefetch([{"url": url}])
        page = self.pages[url]
        if page.refused:
            raise Refused(page.refused)
        return self._result({"kind": "web", "url": page.final_url, "title": title}, quote, page.text, page.problem, "page")

    def _check_file(self, source: dict, title: str, quote: str) -> dict:
        path = Path(str(source["file"])).expanduser()
        if not path.is_file():
            raise Refused(f"there's no file at {path}")
        suffix = path.suffix.lower()
        if suffix not in FILE_TYPES or path.stat().st_size > MAX_FILE_BYTES:
            raise Refused(f"{path.name} has to be a PDF or image of at most {MAX_FILE_BYTES // 1_000_000} MB")
        page = source.get("page")
        if suffix == ".pdf" and page is None:
            raise Refused("a PDF source needs the page number")
        if page is not None and (not isinstance(page, int) or page < 1):
            raise Refused("the page number has to be a whole number from 1 up")
        if suffix == ".pdf":
            count = pdf_page_count(path)
            if count and page > count:
                raise Refused(f"{path.name} has only {count} pages, not {page}")
        kept = self._keep(path, suffix)
        base = {"kind": "file", "file_id": kept, "name": path.name, "title": title, "page": page}
        if suffix != ".pdf":
            return {**base, "quote": quote, "checked_at": time.time(), "status": UNCHECKED, "note": "this is an image, so it can't be read automatically"}
        key = (str(path), page)
        if key not in self.page_text:
            self.page_text[key] = pdf_text(path, page)
        text = self.page_text[key]
        if text is None:
            return self._result(base, quote, None, "this PDF can't be read automatically", f"page {page}")
        if len(plain_words(text)) < 5:
            return self._result(base, quote, None, "this page is a picture with no text, so it can't be read automatically", f"page {page}")
        return self._result(base, quote, text, "", f"page {page} of {path.name}")

    def _check_library(self, source: dict, title: str, quote: str) -> dict:
        library = Library(self.data)
        try:
            entry = library.get(str(source["library"]))
        except LibraryError as reason:
            raise Refused(str(reason))
        page = source.get("page")
        if not isinstance(page, int) or not 1 <= page <= entry["pages"]:
            raise Refused(f"{entry['name']} has {entry['unit']}s 1 to {entry['pages']}, so give one of those as page")
        base = {"kind": "library", "file_id": entry["id"], "name": entry["name"], "unit": entry["unit"], "title": title, "page": page}
        text = library.stored_text(entry["id"], page)
        if len(plain_words(text).split()) < 5:
            return {**base, "quote": quote, "checked_at": time.time(), "status": UNCHECKED,
                    "note": f"this {entry['unit']} is a picture or handwriting, so open it to check"}
        return self._result(base, quote, text, "", f"{entry['unit']} {page} of {entry['name']}")

    def _keep(self, path: Path, suffix: str) -> str:
        """A copy, so the link keeps working if the original moves."""
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        folder = self.data / "sources"
        folder.mkdir(parents=True, exist_ok=True)
        kept = f"{digest}{suffix}"
        if not (folder / kept).exists():
            shutil.copyfile(path, folder / kept)
        return kept

    def check_all(self, sources: list[dict]) -> list[dict]:
        if not isinstance(sources, list):
            raise Refused("sources has to be a list")
        return [self.check(source) for source in sources]


def check_blocks(blocks: list[dict], checker: Checker) -> tuple[dict[int, dict], list[dict]]:
    """Every claim needs a source, its section's source, or the honest own_words label."""
    checker.prefetch([s for b in blocks for s in (b.get("sources") or []) if isinstance(s, dict)])
    checked: dict[int, dict] = {}
    failed = []
    section_cited = False
    for index, block in enumerate(blocks):
        kind = block.get("type")
        try:
            sources = checker.check_all(block.get("sources") or [])
            if kind == "heading":
                section_cited = bool(sources)
            needs = kind in NEEDS_SOURCE or (kind == "example" and not block.get("method"))
            if needs and not sources and not section_cited and not block.get("own_words"):
                raise Refused("it has no source. Cite one here or on its section's heading, or set own_words if it's your own explanation")
            checked[index] = {"sources": sources, "own_words": bool(block.get("own_words"))}
        except Refused as reason:
            failed.append({"index": index, "type": kind, "reason": str(reason)})
    return checked, failed


def cited(item: dict, checker: Checker, what: str) -> dict:
    """For one worksheet note or concept question."""
    sources = checker.check_all(item.get("sources") or [])
    if not sources and not item.get("own_words"):
        raise Refused(f"{what} has no source. Cite one, or set own_words if it's your own explanation")
    return {**item, "sources": sources, "own_words": bool(item.get("own_words"))}
