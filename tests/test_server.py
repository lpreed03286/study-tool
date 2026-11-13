"""The server's life: starting, handing over between sessions, and exiting."""
import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def start(data, env=None, extra=("--no-parent-watch",)):
    process = subprocess.Popen(
        [sys.executable, "-B", str(ROOT / "server/server.py"), "--data", str(data), *extra],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})
    return process, json.loads(process.stdout.readline())


def call(ready, path, body=None, headers=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{ready['port']}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"X-Worksheet-Token": ready["token"], **(headers or {})})
    with urllib.request.urlopen(request, timeout=20) as reply:
        return json.load(reply)


def test_a_new_session_takes_over_with_the_same_address(tmp_path):
    first, ready_one = start(tmp_path)
    second, ready_two = start(tmp_path)
    try:
        assert first.wait(timeout=5) == 0
        assert (ready_two["port"], ready_two["token"]) == (ready_one["port"], ready_one["token"])
        assert call(ready_two, "/api/health")["ok"]
    finally:
        first.kill()
        second.kill()


def test_saved_token_is_private_and_binding_is_local_only(tmp_path):
    process, ready = start(tmp_path)
    try:
        mode = (tmp_path / "server.json").stat().st_mode
        assert stat.S_IMODE(mode) == 0o600
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
        listening = subprocess.run(["cat", "/proc/net/tcp"], capture_output=True, text=True).stdout
        port = f"{ready['port']:04X}"
        assert any(line.split()[1] == f"0100007F:{port}" for line in listening.splitlines()[1:])
        assert not any(line.split()[1] == f"00000000:{port}" for line in listening.splitlines()[1:])
    finally:
        process.kill()


@pytest.mark.parametrize("headers", [{"Host": "evil.example"}, {"Origin": "http://evil.example"}])
def test_other_sites_cannot_reach_the_api_even_with_the_token(tmp_path, headers):
    process, ready = start(tmp_path)
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            call(ready, "/api/sets", headers=headers)
        assert refused.value.code == 403
    finally:
        process.kill()


def test_static_files_cannot_escape_the_web_folder(tmp_path):
    process, ready = start(tmp_path)
    try:
        for path in ("/../server/server.py", "/%2e%2e/server/server.py", "/vendor/../../flake.nix"):
            with pytest.raises(urllib.error.HTTPError) as refused:
                call(ready, path)
            assert refused.value.code == 404
    finally:
        process.kill()


def test_open_starts_librewolf_with_a_new_window(tmp_path):
    bin_folder = tmp_path / "bin"
    bin_folder.mkdir()
    record = tmp_path / "args.txt"
    stub = bin_folder / "librewolf"
    stub.write_text(f'#!/bin/sh\necho "$@" > {record}\n')
    stub.chmod(0o755)
    data = tmp_path / "data"
    process, ready = start(data, {"PATH": f"{bin_folder}:{os.environ['PATH']}"})
    try:
        reply = call(ready, "/internal/open", {})
        assert reply["opened"] == "librewolf"
        for _ in range(40):
            if record.exists() and record.read_text():
                break
            time.sleep(0.05)
        assert record.read_text().strip() == f"--new-window {ready['url']}"
    finally:
        process.kill()


def test_server_exits_when_the_session_that_started_it_dies(tmp_path):
    launcher = (
        "import subprocess, sys, json, os\n"
        f"p = subprocess.Popen([sys.executable, '-B', {str(ROOT / 'server/server.py')!r}, '--data', {str(tmp_path)!r}],"
        " stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)\n"
        "print(json.dumps({'pid': p.pid, 'ready': json.loads(p.stdout.readline())}), flush=True)\n"
        "os._exit(0)\n")
    done = subprocess.run([sys.executable, "-B", "-c", launcher], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    pid = json.loads(done.stdout)["pid"]
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.25)
    os.kill(pid, 9)
    pytest.fail("the server outlived the session that started it")


FORM = {"book": "Kreyszig", "edition": "10", "topics": "2.7", "count": 3, "difficulty": "harder"}


def request_set(ready, form):
    return call(ready, "/api/action", {"action": "request_set", "form": form})


def test_a_form_request_reaches_the_mod_and_is_kept_until_dismissed(tmp_path):
    process, ready = start(tmp_path)
    try:
        library = request_set(ready, FORM)
        assert [r["state"] for r in library["requests"]] == ["waiting"]
        event = json.loads(process.stdout.readline())
        assert event["event"] == "prompt" and event["id"] == library["requests"][0]["id"]
        assert "Difficulty: harder" in event["text"] and "Don't call open_worksheet" in event["text"]
        assert call(ready, "/api/action", {"action": "dismiss_request", "id": event["id"]})["requests"] == []
    finally:
        process.kill()


def test_a_request_claude_code_refuses_shows_why_and_can_be_retried(tmp_path):
    process, ready = start(tmp_path)
    try:
        request_id = request_set(ready, FORM)["requests"][0]["id"]
        process.stdout.readline()
        call(ready, "/internal/request_failed", {"id": request_id, "reason": "A Stop hook refused it."})
        failed = call(ready, "/api/library")["requests"][0]
        assert failed["state"] == "failed" and failed["reason"] == "A Stop hook refused it."
        assert call(ready, "/api/action", {"action": "retry_request", "id": request_id})["requests"][0]["state"] == "waiting"
        assert json.loads(process.stdout.readline())["id"] == request_id
    finally:
        process.kill()


def test_a_request_left_waiting_is_marked_stopped_when_a_new_session_takes_over(tmp_path):
    first, ready = start(tmp_path)
    request_set(ready, FORM)
    second, ready = start(tmp_path)
    try:
        request = call(ready, "/api/library")["requests"][0]
        assert request["state"] == "failed" and "closed" in request["reason"]
    finally:
        first.kill()
        second.kill()


@pytest.mark.parametrize("form,words", [
    ({"edition": "10"}, "textbook"),
    ({"book": "Kreyszig"}, "textbook"),
    ({**FORM, "count": 0}, "from 1 to 30"),
    ({**FORM, "count": "many"}, "whole number"),
    ({**FORM, "difficulty": "impossible"}, "Difficulty"),
    ({**FORM, "pages": "/no/such/file.pdf"}, "no file"),
])
def test_a_bad_form_is_refused_without_bothering_claude(tmp_path, form, words):
    process, ready = start(tmp_path)
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            request_set(ready, form)
        assert refused.value.code == 400 and words in json.load(refused.value)["error"]
        assert call(ready, "/api/library")["requests"] == []
    finally:
        process.kill()


def test_typed_problems_need_no_book(tmp_path):
    process, ready = start(tmp_path)
    try:
        assert request_set(ready, {"typed": "y'+y=1"})["requests"][0]["form"]["typed"] == "y'+y=1"
    finally:
        process.kill()


def test_a_new_install_uses_a_folder_not_named_after_one_class_but_an_old_one_keeps_its_own(tmp_path):
    code = ("import sys; sys.argv=['x']; sys.path.insert(0, %r); import server; print(server.default_data_folder())" % str(ROOT / "server"))
    run = lambda: subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env={**os.environ, "HOME": str(tmp_path)}).stdout.strip()
    assert run() == str(tmp_path / ".local/share/math-worksheet")
    (tmp_path / ".local/share/mae692-worksheets").mkdir(parents=True)
    assert run() == str(tmp_path / ".local/share/mae692-worksheets")


def test_a_new_library_starts_empty(tmp_path):
    process, ready = start(tmp_path)
    try:
        assert call(ready, "/api/files") == {"files": []}
        assert call(ready, "/api/library") == {"sets": [], "docs": [], "requests": []}
    finally:
        process.kill()


def test_dark_theme_follows_a_desktop_palette_and_skips_a_light_one(tmp_path):
    nord_black = {"base00": "000000", "base01": "3b4252", "base02": "434c5e", "base03": "4c566a",
                  "base04": "d8dee9", "base05": "e5e9f0", "base06": "eceff4", "base07": "8fbcbb",
                  "base08": "bf616a", "base09": "d08770", "base0A": "ebcb8b", "base0B": "a3be8c",
                  "base0C": "88c0d0", "base0D": "81a1c1", "base0E": "b48ead", "base0F": "5e81ac",
                  "scheme": "Nord Black"}
    palette = tmp_path / "palette.json"
    palette.write_text(json.dumps(nord_black))
    process, ready = start(tmp_path / "data", env={"WORKSHEET_PALETTE": str(palette)})

    def sheet():
        with urllib.request.urlopen(f"http://127.0.0.1:{ready['port']}/palette.css", timeout=20) as reply:
            assert reply.headers["Content-Type"].startswith("text/css")
            return reply.read().decode()

    try:
        css = sheet()
        assert "@media screen" in css and ':root[data-theme="dark"]' in css
        assert "--bg: #000000;" in css and "--accent: #81a1c1;" in css and "--ink: #e5e9f0;" in css
        palette.write_text(json.dumps({**nord_black, "base00": "eceff4", "base05": "2e3440"}))
        assert sheet() == ""
        palette.unlink()
        assert sheet() == ""
    finally:
        process.kill()
