"""The local web server: 127.0.0.1 only, and every API call needs the session token.

Stdout is the channel to the mod, one JSON object per line. Claude questions go
out that way, and answers come back as POSTs to /internal/ask_result.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from courses import CourseError  # noqa: E402
from engine import Engine, UserError  # noqa: E402
from library import MAX_FILE_BYTES, MAX_IMPORT_BYTES, LibraryError  # noqa: E402
from storage import read_json, write_json  # noqa: E402

WEB = HERE.parent / "web"
MAX_BODY = 2_000_000
MAX_CLIENT_LOG = 256_000


class Worker:
    """One math process. A hang past the timeout kills it, and the next call starts a new one."""

    def __init__(self, name: str):
        self.name = name
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.next_id = 0

    def _ensure(self) -> subprocess.Popen:
        if self.process is None or self.process.poll() is not None:
            self.process = subprocess.Popen(
                [sys.executable, "-B", str(HERE / "worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self._read_line(self.process, 60)
        return self.process

    def _read_line(self, process: subprocess.Popen, timeout: float) -> dict:
        box: list = []
        reader = threading.Thread(target=lambda: box.append(process.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            process.kill()
            self.process = None
            raise TimeoutError
        if not box or not box[0]:
            self.process = None
            raise RuntimeError(f"the {self.name} worker stopped unexpectedly")
        return json.loads(box[0])

    def call(self, op: str, args: dict, timeout: float) -> dict:
        with self.lock:
            process = self._ensure()
            self.next_id += 1
            process.stdin.write(json.dumps({"id": self.next_id, "op": op, "args": args}) + "\n")
            process.stdin.flush()
            reply = self._read_line(process, timeout)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply["result"]

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()


class App:
    def __init__(self, data: Path, emit_stdout: bool = True, allow_local_sources: bool = False):
        self.data = data
        self.workers = {"grade": Worker("grading"), "build": Worker("verification")}
        self.token = ""
        self.port = 0
        self.url = ""
        self.emit_lock = threading.Lock()
        self.emit_stdout = emit_stdout
        self.engine = Engine(data, self.call_worker, self.emit, allow_local_sources)

    def emit(self, event: dict) -> None:
        if not self.emit_stdout:
            return
        with self.emit_lock:
            sys.stdout.write(json.dumps(event) + "\n")
            sys.stdout.flush()

    def call_worker(self, which: str, op: str, args: dict, timeout: float) -> dict:
        try:
            return self.workers[which].call(op, args, timeout)
        except TimeoutError:
            if op == "grade":
                # A hung check says nothing about the answer, so it isn't marked wrong.
                return {"status": "unparsed", "message": "The check took too long, so nothing was marked. "
                        "Try simplifying the expression or writing it another way.", "rename": {}}
            raise UserError(f"That took longer than {int(timeout)} seconds and was stopped.")
        except RuntimeError as failure:
            if op == "grade":
                return {"status": "unparsed", "message": f"The checker hit an error, so nothing was marked: {failure}",
                        "rename": {}}
            raise UserError(str(failure))

    def open_browser(self) -> str:
        for command in (["librewolf", "--new-window", self.url], ["xdg-open", self.url]):
            try:
                subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return command[0]
            except FileNotFoundError:
                continue
        # macOS and Windows have neither, and Python knows their default browser.
        return "your default browser" if webbrowser.open(self.url) else ""

    def status(self) -> dict:
        return {"url": self.url, "sets": self.engine.list_sets()}

    def tool(self, name: str, args: dict) -> dict:
        engine = self.engine
        if name == "list_methods":
            return engine.list_methods()
        if name == "check_coverage":
            return engine.check_coverage(args.get("textbook") or {}, args.get("sections") or [])
        if name == "add_method":
            return engine.add_method(args["file"])
        if name == "record_coverage":
            return engine.record_coverage(args.get("textbook") or {}, args.get("entries") or [])
        if name == "study_plan":
            return engine.study_plan(args.get("methods") or [], int(args.get("count") or 1))
        if name == "error_log":
            return engine.error_log()
        if name == "create_set":
            return engine.create_set(args)
        if name == "create_doc":
            return engine.create_doc(args)
        if name == "library_overview":
            return engine.library.overview(args.get("class_name") or "")
        if name == "library_search":
            return engine.library.search(args.get("query", ""), args.get("class_name") or "", args.get("file") or "")
        if name == "library_read":
            if args.get("outline"):
                return {"file": args.get("file"), "outline": engine.library.outline(args.get("file", ""))}
            return engine.library.read(args.get("file", ""), args.get("pages") or [])
        if name == "library_pending":
            ids = args.get("files") or []
            return {"pending": engine.library.pending([engine.library.get(i) for i in ids] if ids else None)}
        if name == "library_save_index":
            return engine.save_library_index(args)
        if name == "course_create":
            return engine.course_create(args)
        if name == "course_save_research":
            return engine.course_save_research(args)
        if name == "course_save_plan":
            return engine.course_save_plan(args)
        if name == "course_save_syllabus":
            return engine.course_save_syllabus(args)
        if name == "course_overview":
            return engine.course_overview(args.get("course") or "")
        if name == "course_section":
            return engine.course_section(args.get("course") or "", args.get("section") or "")
        if name == "course_placement":
            return engine.course_placement(args.get("course") or "", args.get("sections") or [])
        if name == "status":
            return self.status()
        raise UserError(f"Unknown tool {name}")

    def action(self, body: dict) -> dict:
        engine = self.engine
        action = body.get("action")
        set_id = body.get("set", "")
        problem = body.get("problem", "")
        step = body.get("step", "")
        if action == "library_index":
            return engine.request_index(body.get("files") or [])
        if action == "library_update":
            engine.library.update(body.get("id", ""), body.get("details") or {})
            return engine.library_view()
        if action == "library_export":
            return {"export": engine.library.export(body.get("files") or [])}
        if action == "library_delete":
            return engine.delete_library_file(body.get("id", ""))
        if action == "request_set":
            return engine.request_set(body.get("form") or {})
        if action == "course_request":
            return engine.course_request(body)
        if action == "course_delete":
            return engine.course_delete(body.get("id", ""), bool(body.get("remove_items")))
        if action == "retry_request":
            return engine.retry_request(body.get("id", ""))
        if action == "dismiss_request":
            return engine.dismiss_request(body.get("id", ""))
        if action == "submit":
            return {"view": engine.submit(set_id, problem, step, body.get("latex", ""))}
        if action == "hint":
            return {"view": engine.hint(set_id, problem, step)}
        if action == "explain":
            return engine.explain(set_id, problem, step)
        if action == "walkthrough":
            return engine.walkthrough(set_id, problem)
        if action == "own":
            return engine.own_method(set_id, problem, step, body.get("work", ""), body.get("result", ""))
        if action == "skip_step":
            return {"view": engine.skip_step(set_id, problem, step)}
        if action == "unskip_step":
            return {"view": engine.unskip_step(set_id, problem, step)}
        if action == "skip_problem":
            return {"view": engine.skip_problem(set_id, problem)}
        if action == "unskip_problem":
            return {"view": engine.unskip_problem(set_id, problem)}
        if action == "fade":
            return {"view": engine.set_fade(set_id, problem, body.get("fade", 0))}
        if action == "tab":
            return {"view": engine.set_active(set_id, problem)}
        if action == "draft":
            engine.draft(set_id, problem, step, body.get("latex", ""))
            return {}
        if action == "exam_start":
            return {"view": engine.exam_start(set_id, body.get("minutes"))}
        if action == "exam_own":
            return {"view": engine.exam_own(set_id, problem, step, body.get("work", ""), body.get("result", ""))}
        if action == "exam_submit":
            return {"view": engine.exam_submit(set_id)}
        if action == "exam_close":
            return {"view": engine.exam_close(set_id)}
        if action == "add_problem":
            added = engine.add_problem(set_id, body.get("spec") or {})
            return {"view": engine.view(set_id, added["id"])}
        raise UserError(f"Unknown action {action}")


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "worksheet"

        def log_message(self, format, *args):
            pass

        def _note(self, line: str) -> None:
            """A small local log, since a browser's console can't be read from here."""
            path = app.data / "page.log"
            if path.exists() and path.stat().st_size > MAX_CLIENT_LOG:
                path.unlink()
            with open(path, "a") as out:
                out.write(f"{time.strftime('%H:%M:%S')} {line}\n")

        def _send(self, status: int, payload: bytes, content_type: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def do_HEAD(self):
            if not self._trusted_origin():
                return self._send(HTTPStatus.FORBIDDEN, b"", "text/plain")
            self._static(urlparse(self.path).path)

        def _json(self, value, status: int = 200) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def _trusted_origin(self) -> bool:
            """Only this page may call the API, and DNS rebinding can't reach it."""
            allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://{host}" for host in allowed_hosts}

        def _authorised(self) -> bool:
            supplied = self.headers.get("X-Worksheet-Token", "")
            return self._trusted_origin() and secrets.compare_digest(supplied, app.token)

        def do_GET(self):
            url = urlparse(self.path)
            self._note(f"GET {url.path}")
            if url.path.startswith("/source/"):
                return self._source_file(url)
            if url.path.startswith("/file/"):
                return self._library_file(url)
            if url.path.startswith("/export/"):
                return self._export_file(url)
            if url.path.startswith("/api/"):
                if not self._authorised():
                    return self._json({"error": "not authorised"}, HTTPStatus.FORBIDDEN)
                return self._api_get(url)
            if not self._trusted_origin():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            self._static(url.path)

        def _source_file(self, url) -> None:
            """A link can't send a header, so this one takes the token in its address."""
            supplied = parse_qs(url.query).get("token", [""])[0]
            if not self._trusted_origin() or not secrets.compare_digest(supplied, app.token):
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            path = app.engine.source_file(url.path.removeprefix("/source/"))
            if path is None:
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            self._send(HTTPStatus.OK, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

        def _library_file(self, url) -> None:
            supplied = parse_qs(url.query).get("token", [""])[0]
            if not self._trusted_origin() or not secrets.compare_digest(supplied, app.token):
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            path = app.engine.library.view_path(url.path.removeprefix("/file/"))
            if path is None or not path.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            # Text stays plain and never runs as a page, whatever the file holds.
            kind = "text/plain; charset=utf-8" if path.suffix in (".txt", ".md") else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send(HTTPStatus.OK, path.read_bytes(), kind, {"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})

        def _export_file(self, url) -> None:
            """Sent once and then deleted, so a copy of the library doesn't linger."""
            supplied = parse_qs(url.query).get("token", [""])[0]
            if not self._trusted_origin() or not secrets.compare_digest(supplied, app.token):
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            path = app.engine.library.export_path(url.path.removeprefix("/export/"))
            if path is None:
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Disposition", 'attachment; filename="library-export.zip"')
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with open(path, "rb") as source:
                    shutil.copyfileobj(source, self.wfile, 1_048_576)
            finally:
                path.unlink(missing_ok=True)

        def _import(self) -> None:
            if not self._authorised():
                return self._json({"error": "not authorised"}, HTTPStatus.FORBIDDEN)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_IMPORT_BYTES:
                self.close_connection = True
                return self._json({"error": f"That file is over {MAX_IMPORT_BYTES // 1_000_000_000} GB."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            handle, temporary = tempfile.mkstemp(dir=app.data, prefix="import", suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as out:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1_048_576, remaining))
                        if not chunk:
                            break
                        out.write(chunk)
                        remaining -= len(chunk)
                return self._json(app.engine.library.import_zip(Path(temporary)))
            except LibraryError as refusal:
                return self._json({"error": str(refusal)}, HTTPStatus.BAD_REQUEST)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                return self._json({"error": "Something broke while importing that file."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            finally:
                Path(temporary).unlink(missing_ok=True)

        def _upload(self, url) -> None:
            if not self._authorised():
                return self._json({"error": "not authorised"}, HTTPStatus.FORBIDDEN)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_FILE_BYTES:
                self.close_connection = True
                return self._json({"error": f"That file is over {MAX_FILE_BYTES // 1_000_000} MB."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            handle, temporary = tempfile.mkstemp(dir=app.data, prefix="upload", suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as out:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1_048_576, remaining))
                        if not chunk:
                            break
                        out.write(chunk)
                        remaining -= len(chunk)
                return self._json(app.engine.library.add(Path(temporary), query.get("name", ""), query))
            except LibraryError as refusal:
                return self._json({"error": str(refusal)}, HTTPStatus.BAD_REQUEST)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                return self._json({"error": "Something broke while saving that file."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            finally:
                Path(temporary).unlink(missing_ok=True)

        def _static(self, path: str) -> None:
            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (WEB / relative).resolve()
            if not target.is_relative_to(WEB.resolve()) or not target.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(HTTPStatus.OK, target.read_bytes(), kind)

        def _api_get(self, url) -> None:
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/health":
                    return self._json({"ok": True, "pid": os.getpid()})
                if url.path == "/api/sets":
                    return self._json({"sets": app.engine.list_sets()})
                if url.path == "/api/files":
                    return self._json(app.engine.library_view())
                if url.path == "/api/files/search":
                    return self._json(app.engine.library.search(query.get("q", ""), limit=200))
                if url.path == "/api/doc":
                    return self._json(app.engine.load_doc(query["id"]))
                if url.path == "/api/library":
                    return self._json(app.engine.home_view())
                if url.path == "/api/view":
                    return self._json(app.engine.view(query["set"], query.get("problem")))
                if url.path == "/api/ask":
                    return self._json(app.engine.ask_status(query["id"]))
                if url.path == "/api/courses":
                    return self._json(app.engine.courses_view())
                if url.path == "/api/course":
                    return self._json(app.engine.course_view(query["id"]))
                if url.path == "/api/methods":
                    return self._json(app.engine.list_methods())
            except (UserError, LibraryError, CourseError) as refusal:
                return self._json({"error": str(refusal)}, HTTPStatus.BAD_REQUEST)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                return self._json({"error": "Something broke in the worksheet server."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if urlparse(self.path).path == "/client_log":
                # Same-origin only, and only ever appended to a capped local file.
                if not self._trusted_origin():
                    return self._json({"error": "not authorised"}, HTTPStatus.FORBIDDEN)
                length = min(int(self.headers.get("Content-Length") or 0), 4000)
                self._note("PAGE " + self.rfile.read(length).decode(errors="replace").replace("\n", " "))
                return self._json({"ok": True})
            if urlparse(self.path).path == "/api/files/upload":
                return self._upload(urlparse(self.path))
            if urlparse(self.path).path == "/api/files/import":
                return self._import()
            if not self._authorised():
                return self._json({"error": "not authorised"}, HTTPStatus.FORBIDDEN)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"error": "too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/action":
                    return self._json(app.action(body))
                if path == "/internal/tool":
                    return self._json(app.tool(body["name"], body.get("args") or {}))
                if path == "/internal/ask_result":
                    app.engine.ask_result(body["id"], body.get("text"), body.get("error", ""))
                    return self._json({"ok": True})
                if path == "/internal/request_failed":
                    app.engine.request_failed(body["id"], body.get("reason", ""))
                    return self._json({"ok": True})
                if path == "/internal/open":
                    return self._json({"opened": app.open_browser(), "url": app.url})
                if path == "/internal/shutdown":
                    self._json({"ok": True})
                    threading.Thread(target=shutdown, args=(app,), daemon=True).start()
                    return None
            except (UserError, LibraryError, CourseError) as refusal:
                return self._json({"error": str(refusal)}, HTTPStatus.BAD_REQUEST)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                return self._json({"error": "Something broke in the worksheet server."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def shutdown(app: App) -> None:
    for worker in app.workers.values():
        worker.stop()
    os._exit(0)


def take_over_old_server(saved: dict) -> None:
    """A newer Claude session takes the worksheet, keeping the page's address."""
    if not saved.get("port"):
        return
    request = urllib.request.Request(
        f"http://127.0.0.1:{saved['port']}/internal/shutdown", data=b"{}", method="POST",
        headers={"X-Worksheet-Token": saved.get("token", ""), "Host": f"127.0.0.1:{saved['port']}"})
    try:
        urllib.request.urlopen(request, timeout=2)
        time.sleep(0.3)
    except (urllib.error.URLError, OSError, ValueError):
        pass


def bind(port: int, handler_factory) -> ThreadingHTTPServer:
    for candidate in ([port, port, port] if port else []) + [0]:
        try:
            return ThreadingHTTPServer(("127.0.0.1", candidate), handler_factory)
        except OSError:
            time.sleep(0.4)
    raise RuntimeError("couldn't bind a local port")


def watch_parent(app: App, parent: int) -> None:
    """Exit with the Claude session that started this, whatever way it ended."""
    while True:
        time.sleep(2)
        if os.getppid() != parent:
            shutdown(app)


def default_data_folder() -> Path:
    """An existing install keeps its folder. A new one gets a name not tied to one class."""
    legacy = Path.home() / ".local/share/mae692-worksheets"
    return legacy if legacy.exists() else Path.home() / ".local/share/math-worksheet"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(default_data_folder()))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-parent-watch", action="store_true")
    parser.add_argument("--allow-local-sources", action="store_true", help="for tests: let sources be on this machine")
    options = parser.parse_args()
    data = Path(options.data)
    data.mkdir(parents=True, exist_ok=True)
    os.chmod(data, 0o700)

    state_file = data / "server.json"
    saved = read_json(state_file, {})
    take_over_old_server(saved)

    app = App(data, allow_local_sources=options.allow_local_sources)
    app.engine.interrupt_requests()
    app.token = saved.get("token") or secrets.token_urlsafe(32)
    server = bind(options.port or int(saved.get("port") or 0), make_handler(app))
    app.port = server.server_address[1]
    app.url = f"http://127.0.0.1:{app.port}/?token={app.token}"
    write_json(state_file, {"port": app.port, "token": app.token})
    os.chmod(state_file, 0o600)

    if not options.no_parent_watch:
        threading.Thread(target=watch_parent, args=(app, os.getppid()), daemon=True).start()
    app.emit({"event": "ready", "port": app.port, "token": app.token, "url": app.url})
    server.serve_forever()


if __name__ == "__main__":
    main()
