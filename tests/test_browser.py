"""The real page in a real browser, with real keystrokes through MathLive.

Run with the browserTests package from flake.nix (see README). Skipped otherwise.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(os.environ.get("WORKSHEET_SHOTS", "/tmp/worksheet-shots"))
BOOK = {"name": "Kreyszig, Advanced Engineering Mathematics", "edition": "10", "source": "memory"}
COVERAGE = [
    {"section": "2.7", "topic": "Nonhomogeneous linear ODEs", "methods": ["undetermined_coefficients"],
     "rationale": "2.7 teaches the method with its Basic, Modification and Sum rules."},
    {"section": "1.5", "topic": "Linear ODEs", "methods": ["first_order_linear"],
     "rationale": "1.5 teaches y'+p(x)y=r(x) with the integrating factor."},
]
PROBLEMS = [
    {"statement": "y''+3y'+2y=3e^{2x}", "method": "undetermined_coefficients", "section": "2.7", "topic": "Nonhomogeneous linear ODEs"},
    {"statement": "y'+2y=e^{x}", "ic": ["y(0)=1"], "method": "first_order_linear", "section": "1.5", "topic": "Linear ODEs"},
]
NOTES = {"undetermined_coefficients": {
    "summary": "Kreyszig picks $y_p$ with the Basic, Modification and Sum Rules, then determines the coefficients by substituting.",
    "source": "memory", "own_words": True}}


class Server:
    def __init__(self, data: Path):
        self.process = subprocess.Popen(
            [sys.executable, "-B", str(ROOT / "server/server.py"), "--data", str(data), "--no-parent-watch", "--allow-local-sources"],
            stdout=subprocess.PIPE, stderr=sys.stderr, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        ready = json.loads(self.process.stdout.readline())
        self.port, self.token, self.url = ready["port"], ready["token"], ready["url"]
        self.claude_replies = []
        self.prompts = []
        threading.Thread(target=self._fake_claude, daemon=True).start()

    def _fake_claude(self):
        for line in self.process.stdout:
            event = json.loads(line)
            if event.get("event") == "prompt":
                self.prompts.append(event)
            if event.get("event") != "ask":
                continue
            text = {"explain": "Your sign on $2y$ flipped when you moved it. The book keeps $y$ terms on the left.",
                    "walkthrough": "Take $y'+y=1$ as an example. Multiply by $e^{x}$.",
                    "own_method": '{"valid": true, "reason": "That is a valid path."}',
                    "discussion": '{"valid": true, "reason": "You read the plot correctly."}',
                    "concept": '{"valid": false, "reason": "You named one method, but there is a second one."}'
                    if "only one" in event["prompt"] else '{"valid": true, "reason": "Both methods are right."}'}[event["kind"]]
            self.claude_replies.append(event)
            self.post("/internal/ask_result", {"id": event["id"], "text": text})

    def post(self, path, body):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(), method="POST",
                                         headers={"X-Worksheet-Token": self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=300) as reply:
            return json.load(reply)

    def tool(self, name, bare=False, **args):
        # Most tests aren't about the quick reference blocks a study guide must have.
        if name == "create_doc" and args.get("kind") == "study_guide" and not bare:
            kinds = {block["type"] for block in args["blocks"]}
            extra = [] if "definition" in kinds else [{"type": "definition", "term": "T", "meaning": "M", "own_words": True}]
            if "equation" in kinds and "nomenclature" not in kinds:
                extra.append({"type": "nomenclature", "rows": [{"symbol": "$x$", "meaning": "M"}], "own_words": True})
            args["blocks"] = [*args["blocks"], *extra]
        return self.post("/internal/tool", {"name": name, "args": args})

    def stop(self):
        self.process.kill()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    instance = Server(tmp_path_factory.mktemp("data"))
    instance.tool("record_coverage", textbook=BOOK, entries=COVERAGE)
    yield instance
    instance.stop()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as driver:
        launched = getattr(driver, os.environ.get("WORKSHEET_BROWSER", "chromium")).launch()
        yield launched
        launched.close()


@pytest.fixture
def page(server, browser):
    created = server.tool("create_set", title=f"HW {time.time_ns()}", textbook=BOOK, problems=PROBLEMS, book_methods=NOTES)
    assert created["created"], created
    context = browser.new_context(viewport={"width": 900, "height": 1000})
    tab = context.new_page()
    tab.outside = []
    tab.errors = []
    tab.on("request", lambda r: tab.outside.append(r.url) if not r.url.startswith(f"http://127.0.0.1:{server.port}") else None)
    tab.on("console", lambda m: tab.errors.append(m.text) if m.type == "error" else None)
    tab.on("pageerror", lambda e: tab.errors.append(str(e)))
    # Chromium hides a missing font: it just falls back without a console error.
    tab.on("response", lambda r: tab.errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    tab.goto(f"{server.url}&set={created['set']}")
    tab.wait_for_selector("math-field[data-open='true']")
    tab.set_id = created["set"]
    yield tab
    assert tab.outside == [], "the page must make no network requests off this machine"
    assert tab.errors == []
    context.close()


def field(page):
    return page.locator("math-field[data-open='true']")


def open_id(page):
    return page.locator("article[data-open-step]").get_attribute("data-open-step")


def press_math(page, *keys):
    """Keys into the open field, as a person would type them."""
    field(page).click()
    page.wait_for_timeout(150)
    for key in keys:
        if key in ("ArrowRight", "ArrowLeft", "Backspace"):
            page.keyboard.press(key)
        else:
            page.keyboard.type(key, delay=15)


def set_math(page, latex):
    field(page).evaluate("(node, value) => { node.value = value; node.dispatchEvent(new Event('input', {bubbles: true})); }", latex)


def submit(page, step):
    page.locator("[data-action='submit']").click()
    page.wait_for_function("id => !document.querySelector(`article[data-open-step=\"${id}\"] [data-action=submit]:disabled`)", arg=step)


def expect_advanced(page, step_before):
    page.wait_for_function(
        "id => { const o = document.querySelector('article[data-open-step]'); return !o || o.dataset.openStep !== id; }",
        arg=step_before, timeout=20000)


def solve(page, step, *keys):
    assert open_id(page) == step
    press_math(page, *keys)
    page.locator("[data-action='submit']").click()
    expect_advanced(page, step)


RIGHT = "ArrowRight"


def test_undetermined_coefficients_with_real_keystrokes(page):
    assert page.locator(".tab").count() == 2
    # Only the problem and the first prompt, with one input box.
    assert page.locator("math-field[data-open='true']").count() == 1
    assert page.locator("article.step").count() == 1
    assert page.locator("[data-solution]").count() == 0
    page.screenshot(path=SHOTS / "1-start.png")

    solve(page, "char_eq", "r^2", RIGHT, "+3r+2=0")
    assert page.locator("article.step.correct").count() == 1
    solve(page, "roots", "-1,-2")
    solve(page, "y_h", "c_1", RIGHT, "e^", "-x", RIGHT, "+c_2", RIGHT, "e^", "-2x", RIGHT)
    solve(page, "y_p", "Ae^", "2x", RIGHT)
    solve(page, "y_p_d1", "2Ae^", "2x", RIGHT)
    solve(page, "y_p_d2", "4Ae^", "2x", RIGHT)
    solve(page, "substitute", "12Ae^", "2x", RIGHT, "=3e^", "2x", RIGHT)
    solve(page, "match", "12A=3")
    solve(page, "solve", "A=1/4", RIGHT)
    solve(page, "y_p_final", "1/4", RIGHT, "e^", "2x", RIGHT)
    assert page.locator("article.step.correct").count() == 10
    page.screenshot(path=SHOTS / "2-progress.png", full_page=True)
    solve(page, "general", "c_1", RIGHT, "e^", "-x", RIGHT, "+c_2", RIGHT, "e^", "-2x", RIGHT, "+1/4", RIGHT, "e^", "2x", RIGHT)
    # Worked solution, top to bottom.
    solution = page.locator("[data-solution]")
    solution.wait_for()
    assert solution.locator(".solution-row").count() == 11
    assert "checkodesol passed" in solution.inner_text()
    page.screenshot(path=SHOTS / "3-solution.png", full_page=True)


def test_first_order_with_wrong_answer_explain_and_retry(page, server):
    page.locator(".tab").nth(1).click()
    page.wait_for_function("document.querySelector('.tab[aria-selected=true]').dataset.tab === 'p2'")
    page.wait_for_selector("article[data-open-step='standard']")
    # Wrong: said so, answer stays in the box, Explain why offered.
    press_math(page, "y'-2y=e^", "x", RIGHT)
    page.locator("[data-action='submit']").click()
    page.wait_for_selector(".feedback.incorrect")
    assert open_id(page) == "standard"
    kept = field(page).evaluate("node => node.value")
    assert "2y" in kept and "-" in kept
    explain = page.locator("[data-action='explain']")
    assert explain.is_visible()
    page.screenshot(path=SHOTS / "4-wrong.png")
    explain.click()
    page.wait_for_selector("[data-explanation]")
    assert "sign on" in page.locator("[data-explanation]").inner_text()
    # The question Claude got is about this specific answer.
    assert any("2y" in ask["prompt"] and "student_answer" in ask["prompt"] for ask in server.claude_replies)
    page.screenshot(path=SHOTS / "5-explain.png")
    # Retry by editing, then the rest of the method.
    set_math(page, "y'+2y=e^{x}")
    submit(page, "standard")
    expect_advanced(page, "standard")
    assert page.locator("article.step.correct").count() == 1
    solve(page, "identify", "p=2,r=e^", "x", RIGHT)
    solve(page, "mu", "e^", "2x", RIGHT)
    solve(page, "multiply", "e^", "3x", RIGHT)
    # Missing +C is called out specifically.
    press_math(page, "1/3", RIGHT, "e^", "3x", RIGHT)
    page.locator("[data-action='submit']").click()
    page.wait_for_selector(".feedback.incorrect")
    assert "+C" in page.locator(".feedback.incorrect").inner_text()
    press_math(page, "+C")
    page.locator("[data-action='submit']").click()
    expect_advanced(page, "integrate")
    solve(page, "general", "1/3", RIGHT, "e^", "x", RIGHT, "+Ce^", "-2x", RIGHT)
    solve(page, "ivp", "1/3", RIGHT, "e^", "x", RIGHT, "+2/3", RIGHT, "e^", "-2x", RIGHT)
    page.locator("[data-solution]").wait_for()
    # The first tab kept its own place.
    page.locator(".tab").nth(0).click()
    page.wait_for_selector("article[data-open-step='char_eq']")


def test_live_preview_follows_the_field(page):
    press_math(page, "1/2", RIGHT)
    preview = page.locator("[data-preview='char_eq'] .katex")
    preview.wait_for()
    assert page.locator("[data-preview='char_eq'] .katex .mfrac").count() == 1


def test_hints_in_three_levels_then_resume_after_reload(page, server):
    hint = page.locator("[data-action='hint']")
    assert hint.inner_text() == "Hint: nudge"
    hint.click()
    page.wait_for_selector("[data-hint='1']")
    page.locator("[data-action='hint']").click()
    page.wait_for_selector("[data-hint='2']")
    page.locator("[data-action='hint']").click()
    page.wait_for_selector("[data-hint='3']")
    assert page.locator("[data-action='hint']").count() == 0
    page.screenshot(path=SHOTS / "6-hints.png")
    set_math(page, r"r^2+3r+")
    page.wait_for_timeout(700)  # the draft saves itself
    page.reload()
    page.wait_for_selector("math-field[data-open='true']")
    assert page.locator("[data-hint='3']").count() == 1
    assert "3r" in field(page).evaluate("node => node.value")


def test_slider_changes_the_current_problem_live(page):
    slider = page.locator("#fade")
    slider.evaluate("node => { node.value = 50; node.dispatchEvent(new Event('input', {bubbles: true})); }")
    page.wait_for_selector("article.step.prefilled")
    assert page.locator("article.step.prefilled").count() == 5
    assert "5 of 11" in page.locator("#fade-count").inner_text()
    page.screenshot(path=SHOTS / "7-slider.png", full_page=True)
    assert open_id(page) == "y_p_d2"
    slider.evaluate("node => { node.value = 0; node.dispatchEvent(new Event('input', {bubbles: true})); }")
    page.wait_for_selector("article[data-open-step='char_eq']")
    assert page.locator("article.step.prefilled").count() == 0


def test_exam_mode(page):
    page.locator("#exam-toggle").click()
    page.locator("#exam-minutes").fill("30")
    page.locator("#exam-dialog button.primary").click()
    page.wait_for_selector("#timer:not([hidden])")
    assert "left" in page.locator("#timer").inner_text()
    # All steps open at once, no hints, no explain, no slider.
    assert page.locator("article.step").count() == 11
    assert page.locator("math-field[data-step]").count() >= 11
    assert page.locator("[data-action='hint']").count() == 0
    assert page.locator("[data-action='explain']").count() == 0
    assert not page.locator("#fade-row").is_visible()
    page.screenshot(path=SHOTS / "8-exam.png", full_page=True)
    first = page.locator("math-field[data-step='char_eq']")
    first.click()
    page.wait_for_timeout(200)
    page.keyboard.type("r^2", delay=15)
    page.keyboard.press("ArrowRight")
    page.keyboard.type("+3r+2=0", delay=15)
    # No grading feedback while it's running.
    assert page.locator(".feedback").count() == 0
    # Submitting straight after typing must not lose the last answer.
    page.locator("button.primary", has_text="Submit exam").click()
    page.wait_for_selector("[data-exam-results]")
    assert "1 / 18" in page.locator(".score-big").inner_text()
    page.screenshot(path=SHOTS / "9-results.png", full_page=True)
    page.locator("button.primary", has_text="Back to practice").click()
    page.wait_for_selector("article[data-open-step='char_eq']")


def test_own_method_on_a_step(page):
    page.locator("article[data-open-step] details.own-method summary").click()
    page.locator("textarea[data-own-work='char_eq']").fill("Substitute $y=e^{rx}$ and divide by $e^{rx}$.")
    own_field = page.locator("article[data-open-step] details.own-method math-field")
    own_field.evaluate("node => { node.value = 'r^2+3r+2=0'; node.dispatchEvent(new Event('input', {bubbles: true})); }")
    page.locator("[data-action='own']").click()
    page.wait_for_selector("article[data-open-step='roots']", timeout=15000)
    assert "own method" in page.locator("article.step.correct").inner_text()


@pytest.mark.parametrize("keys,expected", [
    (["sin x"], r"\\sin"),
    (["A cos 2x + B sin 2x"], r"\\cos"),
    (["y''+3y'+2y=0"], "y''"),
    (["y_p''"], "y_{p}''"),
    (["int x dx"], "x^{2}"),
    (["-1pm2i"], r"\\pm"),
    (["Shift+Backslash", "x", "Shift+Backslash"], r"\\left|"),
    (["e^", "-x", RIGHT, "(A cos2x+B sin2x)"], "e^{- x}"),
])
def test_what_mathlive_writes_is_readable_by_the_parser(page, keys, expected):
    field(page).click()
    page.wait_for_timeout(200)
    for key in keys:
        if key == RIGHT or key.startswith("Shift+"):
            page.keyboard.press(key)
        else:
            page.keyboard.type(key, delay=15)
    written = field(page).evaluate("node => node.value")
    from mathparse import parse_list
    for item in parse_list(written, allow_i=True):
        assert item.parts
        readback = item.readback
    if expected.startswith("\\"):
        assert expected[1:] in written
    else:
        assert expected.replace("{", "").replace("}", "").replace(" ", "") in readback.replace("{", "").replace("}", "").replace(" ", "") \
            or expected in written


def test_cheat_sheet_inserts_into_the_field(page):
    page.locator("#cheat summary").click()
    assert page.locator(".cheat-item").count() >= 20
    page.screenshot(path=SHOTS / "10-cheat.png", full_page=True)
    field(page).click()
    page.locator(".cheat-item", has_text="\\frac").click()
    assert "frac" in field(page).evaluate("node => node.value")


def test_screens_for_a_half_screen_window_in_dark_mode(server, browser):
    created = server.tool("create_set", title="Dark", textbook=BOOK, problems=PROBLEMS, book_methods=NOTES)
    context = browser.new_context(viewport={"width": 640, "height": 900}, color_scheme="dark")
    tab = context.new_page()
    tab.goto(f"{server.url}&set={created['set']}")
    tab.wait_for_selector("math-field[data-open='true']")
    tab.locator(".tab").nth(1).click()
    tab.wait_for_selector("article[data-open-step='standard']")
    tab.wait_for_timeout(300)
    tab.locator("math-field[data-open='true']").click()
    tab.wait_for_timeout(150)
    tab.keyboard.type("y'+3y=x", delay=15)
    tab.locator("[data-action='submit']").click()
    tab.wait_for_selector(".feedback.incorrect")
    tab.screenshot(path=SHOTS / f"11-dark-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")
    width = tab.evaluate("document.documentElement.scrollWidth")
    assert width <= 640, "the page must not scroll sideways in a half-screen window"
    context.close()


SYSTEM = {"statement": "y_1'=y_1+2y_2,\\quad y_2'=2y_1+y_2", "ic": ["y_1(0)=1", "y_2(0)=0"], "method": "linear_system",
          "section": "4.3", "topic": "Constant-coefficient systems", "context": "A coupled system."}
EC = {"statement": "x^2y''-2xy'+2y=0", "ic": ["y(1)=1", "y'(1)=0"], "method": "euler_cauchy",
      "section": "2.5", "topic": "Euler-Cauchy"}
EXTRA_COVERAGE = [
    {"section": "4.3", "topic": "Systems", "methods": ["linear_system"], "rationale": "4.3 solves y'=Ay by eigenvalues."},
    {"section": "2.5", "topic": "Euler-Cauchy", "methods": ["euler_cauchy"], "rationale": "2.5 uses y=x^m."},
]


def open_set(server, browser, problem, shot_size=(900, 1100), scheme="light"):
    server.tool("record_coverage", textbook=BOOK, entries=EXTRA_COVERAGE)
    made = server.tool("create_set", title=f"S {time.time_ns()}", textbook=BOOK, problems=[problem])
    assert made["created"], made
    context = browser.new_context(viewport={"width": shot_size[0], "height": shot_size[1]}, color_scheme=scheme)
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    tab.errors = errors
    tab.goto(f"{server.url}&set={made['set']}")
    tab.wait_for_selector("article[data-open-step]")
    return context, tab


def send_math(tab, latex):
    step = open_id(tab)
    set_math(tab, latex)
    tab.locator("[data-action='submit']").click()
    expect_advanced(tab, step)


def test_a_system_all_the_way_to_a_judged_discussion(server, browser):
    context, tab = open_set(server, browser, SYSTEM)
    assert "A coupled system." in tab.locator(".problem-card").first.inner_text()
    send_math(tab, "1,2,2,1")
    send_math(tab, r"\lambda^2-2\lambda-3=0")
    send_math(tab, "-1,3")
    assert open_id(tab) == "eigvec1"
    assert tab.locator("[data-figure]").count() == 0, "the plot must not appear before classification"
    # A typed vector through MathLive.
    press_math(tab, "1,-1")
    tab.locator("[data-action='submit']").click()
    expect_advanced(tab, "eigvec1")
    send_math(tab, "1,1")
    send_math(tab, r"y_1=c_1e^{-t}+c_2e^{3t},\ y_2=-c_1e^{-t}+c_2e^{3t}")
    send_math(tab, "W=2e^{2t}")
    send_math(tab, r"y_1=\frac12e^{-t}+\frac12e^{3t},\ y_2=-\frac12e^{-t}+\frac12e^{3t}")
    send_math(tab, r"p=2,q=-3,\Delta=16")
    # A wrong choice says so and stays open.
    assert open_id(tab) == "type"
    tab.locator(".choice", has_text="Center").locator("input").check()
    tab.locator("[data-action='submit']").click()
    tab.wait_for_selector(".feedback.incorrect")
    assert open_id(tab) == "type"
    assert tab.locator("[data-action='explain']").count() == 1
    assert tab.locator(".choice", has_text="Center").locator("input").is_checked()
    tab.locator(".choice", has_text="Saddle point").locator("input").check()
    tab.locator("[data-action='submit']").click()
    expect_advanced(tab, "type")
    tab.locator(".choice", has_text="Unstable").locator("input").check()
    tab.locator("[data-action='submit']").click()
    expect_advanced(tab, "stability")
    # The plots appear for the discussion: portrait with arrows, and y(t).
    assert open_id(tab) == "discuss"
    tab.wait_for_selector("[data-figure='phase'] svg .trajectory", state="attached")
    assert tab.locator("[data-figure='phase'] svg.chart .arrow").count() > 5
    assert tab.locator("[data-figure='phase'] svg.chart .eigenline").count() == 2
    assert tab.locator("[data-figure='phase'] svg.chart .highlight").count() == 1
    assert tab.locator("[data-figure='lines'] svg.chart .series").count() == 2
    assert tab.locator("[data-figure='lines'] .fig-legend").count() == 1
    tab.locator("[data-figure='lines'] svg.chart").hover()
    assert "t =" in tab.locator("[data-figure='lines'] .fig-readout").inner_text()
    tab.screenshot(path=SHOTS / f"12-system-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png", full_page=True)
    tab.locator("textarea[data-discussion]").fill("A saddle: q<0. Solutions come in along (1,-1) and leave along (1,1).")
    tab.locator("[data-action='own']").click()
    tab.wait_for_selector("[data-solution]", timeout=15000)
    assert any("(1,-1)" in ask["prompt"] and "Saddle point" not in ask["system"] for ask in server.claude_replies)
    solution = tab.locator("[data-solution]")
    assert solution.locator("[data-figure='phase']").count() == 1
    assert "matrix exponential agrees" in solution.inner_text()
    assert tab.errors == []
    context.close()


def test_euler_cauchy_plot_in_dark_mode(server, browser):
    context, tab = open_set(server, browser, EC, scheme="dark")
    send_math(tab, "m^2-3m+2=0")
    send_math(tab, "1,2")
    send_math(tab, "c_1x+c_2x^2")
    send_math(tab, "y=2x-x^2")
    tab.wait_for_selector("[data-figure='lines'] .series", state="attached")
    assert tab.locator("[data-figure='lines'] .fig-legend").count() == 0, "one series needs no legend"
    assert tab.locator("[data-figure='lines'] .highlight-dot").count() == 1
    tab.locator("[data-figure='lines'] summary").click()
    assert tab.locator("[data-figure='lines'] table tr").count() > 5
    tab.screenshot(path=SHOTS / f"13-ec-dark-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png", full_page=True)
    assert tab.evaluate("document.documentElement.scrollWidth") <= 900
    assert tab.errors == []
    context.close()


def test_exam_covers_choices_and_discussions(server, browser):
    context, tab = open_set(server, browser, SYSTEM)
    tab.locator("#exam-toggle").click()
    tab.locator("#exam-dialog button.primary").click()
    tab.wait_for_selector("#timer:not([hidden])")
    tab.wait_for_selector("fieldset[data-choices='type']")
    tab.locator("fieldset[data-choices='type'] .choice", has_text="Saddle point").locator("input").check()
    tab.wait_for_timeout(200)
    tab.locator("textarea[data-exam-discussion]").fill("It is a saddle, so solutions leave along (1,1).")
    tab.locator("button.primary", has_text="Submit exam").click()
    tab.wait_for_selector("[data-exam-results]")
    tab.wait_for_function("!document.querySelector('[data-exam-results]').textContent.includes('Waiting on Claude')", timeout=15000)
    results = tab.locator("[data-exam-results]").inner_text()
    assert "2 / " in tab.locator(".score-big").inner_text(), results[:400]
    assert tab.errors == []
    context.close()


def test_theme_defaults_to_dark_ignoring_what_the_browser_reports_and_is_remembered(server, browser):
    # resistFingerprinting always reports light, which is what this context does too.
    created = server.tool("create_set", title="Theme", textbook=BOOK, problems=PROBLEMS)
    context = browser.new_context(color_scheme="light")
    tab = context.new_page()
    tab.goto(f"{server.url}&set={created['set']}")
    tab.wait_for_selector("math-field[data-open='true']")
    theme = "document.documentElement.dataset.theme"
    assert tab.evaluate(theme) == "dark"
    assert tab.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(14, 17, 22)"
    assert tab.locator("meta[name='darkreader-lock']").count() == 1
    tab.locator("#theme-toggle").click()
    assert tab.evaluate(theme) == "light"
    tab.reload()
    tab.wait_for_selector("math-field[data-open='true']")
    assert tab.evaluate(theme) == "light"
    context.close()


def test_api_refuses_a_missing_token(server):
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/sets")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=5)
    assert refused.value.code == 403


@pytest.fixture
def home(server, browser):
    context = browser.new_context(viewport={"width": 1000, "height": 900})
    tab = context.new_page()
    tab.errors = []
    # Leaving the page cancels font downloads (NS_BINDING_ABORTED in Firefox), and MathLive logs that too.
    # Harmless here: the page fixture fails on any font that really is missing.
    cancelled = ("status=2152398850", "The math fonts could not be loaded")
    tab.on("console", lambda m: tab.errors.append(m.text) if m.type == "error" and not any(c in m.text for c in cancelled) else None)
    tab.on("pageerror", lambda e: tab.errors.append(str(e)))
    tab.goto(server.url)
    tab.wait_for_selector("#main .set-row, #main p")
    yield tab
    assert tab.errors == []
    context.close()


def make_set(server, title, **extra):
    created = server.tool("create_set", title=title, textbook=BOOK, problems=PROBLEMS, book_methods=NOTES, **extra)
    assert created["created"], created
    return created["set"]


def test_worksheet_address_alone_opens_the_list_and_its_controls_stay_out_of_the_way(server, home):
    made = make_set(server, "Listed set", difficulty="harder")
    home.reload()
    row = home.locator(f".set-row[data-set='{made}']")
    row.wait_for()
    assert "Listed set" in row.inner_text() and "Harder" in row.inner_text() and "0 of 2 done" in row.inner_text()
    assert "Sections 2.7, 1.5" in row.inner_text()
    for hidden in ("#set-picker", "#exam-toggle-label", "#fade-row", "#tabs", "#tools", "#home-link"):
        assert not home.locator(hidden).is_visible(), hidden
    home.screenshot(path=SHOTS / f"20-home-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")


def test_a_row_opens_the_unchanged_worksheet_and_the_link_returns(server, home):
    made = make_set(server, "Round trip", difficulty="challenge")
    home.reload()
    home.locator(f".set-row[data-set='{made}']").click()
    home.wait_for_selector("math-field[data-open='true']")
    for shown in ("#set-picker", "#exam-toggle-label", "#fade-row", "#tabs", "#tools"):
        assert home.locator(shown).is_visible(), shown
    for hidden in ("#new-set", "#home-sort", "#home-title"):
        assert not home.locator(hidden).is_visible(), hidden
    assert "Challenge" in home.locator("#book").inner_text()
    home.locator("#home-link").click()
    home.locator(f".set-row[data-set='{made}']").wait_for()
    assert "set=" not in home.url


def test_typing_in_a_worksheet_survives_the_trip_to_the_list(server, home):
    made = make_set(server, "Keeps drafts")
    home.goto(f"{server.url}&set={made}")
    home.wait_for_selector("math-field[data-open='true']")
    set_math(home, "r^2")
    home.locator("#home-link").click()
    home.locator(f".set-row[data-set='{made}']").click()
    home.wait_for_selector("math-field[data-open='true']")
    assert field(home).evaluate("node => node.value") == "r^2"


def test_sorting_reorders_and_is_remembered(server, home):
    make_set(server, "Zeta set")
    make_set(server, "Alpha set")
    home.reload()
    home.locator(".set-row[data-set]").first.wait_for()
    home.select_option("#home-sort", "title")
    names = home.locator(".set-row[data-set] .name").all_inner_texts()
    assert names == sorted(names, key=str.lower) or names.index("Alpha set") < names.index("Zeta set")
    home.select_option("#home-sort", "newest")
    assert home.locator(".set-row[data-set] .name").first.inner_text() == "Alpha set"
    home.reload()
    home.locator(".set-row[data-set]").first.wait_for()
    assert home.locator("#home-sort").input_value() == "newest"


def test_new_form_sends_the_request_and_the_finished_set_replaces_its_row(server, home):
    before = len(server.prompts)
    home.locator("#new-set").click()
    assert home.locator("#new-book").input_value() == BOOK["name"], "starts from the latest book"
    home.screenshot(path=SHOTS / f"21-new-form-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")
    home.fill("#new-topics", "1.5 and 2.7")
    home.fill("#new-count", "2")
    home.select_option("#new-difficulty", "harder")
    home.fill("#new-focus", "Mostly the Modification Rule")
    home.locator("#new-submit").click()
    row = home.locator(".set-row[data-request]")
    row.wait_for()
    assert not home.locator("#new-dialog").is_visible()
    assert "making this" in row.inner_text() and "Harder" in row.inner_text()
    home.screenshot(path=SHOTS / f"22-waiting-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    for _ in range(50):
        if len(server.prompts) > before:
            break
        time.sleep(0.1)
    sent = server.prompts[-1]
    for wanted in ("make-set", BOOK["name"], "1.5 and 2.7", "not counting the concept questions: 2", "Difficulty: harder",
                   "Mostly the Modification Rule", f'request_id "{sent["id"]}"'):
        assert wanted in sent["text"], wanted

    made = make_set(server, "From the form", request_id=sent["id"], difficulty="harder")
    home.locator(f".set-row[data-set='{made}']").wait_for(timeout=8000)
    assert home.locator(".set-row[data-request]").count() == 0
    assert "ready" in home.locator("#toast").inner_text()


def test_new_form_shows_a_refusal_and_stays_open(server, home):
    before = len(server.prompts)
    home.locator("#new-set").click()
    home.fill("#new-book", "")
    home.locator("#new-submit").click()
    home.locator("#new-error").wait_for()
    assert "textbook" in home.locator("#new-error").inner_text()
    assert home.locator("#new-dialog").is_visible()
    home.fill("#new-book", "Kreyszig")
    home.fill("#new-topics", "2.7")
    home.fill("#new-pages", "/no/such/file.pdf")
    home.locator("#new-submit").click()
    home.wait_for_function("document.getElementById('new-error').textContent.includes('no file')")
    assert len(server.prompts) == before
    home.errors.clear()  # The browser logs each refusal's 400; showing it is what's under test.


CONCEPT = {"statement": "\\text{Concept check}", "method": "concept_questions", "section": "2.7", "topic": "Nonhomogeneous linear ODEs",
           "questions": [
               {"question": "Which methods get $y_p$ once you know $y_h$?", "answer": "Undetermined coefficients and variation of parameters.",
                "nudge": "There are two.", "method_hint": "One guesses a form, one varies constants.", "own_words": True},
               {"question": "Why add $y_h$ to $y_p$?", "answer": "To get all the free constants.",
                "nudge": "Count constants.", "method_hint": "Compare with the order.", "own_words": True}]}


def test_concept_questions_come_first_and_work_like_written_steps(server, home):
    made = server.tool("create_set", title="With concepts", textbook=BOOK, problems=[CONCEPT, PROBLEMS[0]], book_methods=NOTES)
    assert made["created"], made
    home.goto(f"{server.url}&set={made['set']}")
    box = home.locator("textarea[data-discussion='q1']")
    box.wait_for()
    assert "Which methods get" in home.locator("article[data-open-step='q1'] h3").inner_text()
    assert "$" not in home.locator("article[data-open-step='q1'] h3").inner_text(), "math is drawn"
    assert "Concept check" in home.locator(".statement .katex-html").inner_text().replace("\xa0", " ")
    assert home.locator("#tabs .tab").first.inner_text().startswith("Problem 1")
    assert home.locator("details.own-method summary", has_text="How ").count() == 0
    assert box.get_attribute("placeholder").startswith("Write your answer")

    box.fill("only one: undetermined coefficients")
    home.locator("article[data-open-step='q1'] [data-action='own']").click()
    home.wait_for_selector("article[data-open-step='q1'] .feedback.incorrect")
    assert "second one" in home.locator("article[data-open-step='q1'] .feedback.incorrect").inner_text()
    home.wait_for_function("!document.querySelector(\"article[data-open-step='q1'] [data-action='own']\").disabled")

    home.locator("textarea[data-discussion='q1']").fill("Undetermined coefficients and variation of parameters.")
    home.locator("article[data-open-step='q1'] [data-action='own']").click()
    home.locator("textarea[data-discussion='q2']").wait_for()
    home.locator("textarea[data-discussion='q2']").fill("It brings in the free constants.")
    home.locator("article[data-open-step='q2'] [data-action='own']").click()
    home.locator("[data-solution]").wait_for()
    solution = home.locator("[data-solution]").inner_text()
    assert "Variation of parameters".lower() in solution.lower() and "not checked by SymPy" in solution
    assert "verified before you saw it" not in solution
    home.screenshot(path=SHOTS / f"23-concept-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png", full_page=True)

    home.locator("#tabs .tab").nth(1).click()
    home.wait_for_selector("math-field[data-open='true']")
    home.wait_for_function("document.querySelectorAll('details.problem-card select option').length > 0")
    methods = home.locator("details.problem-card select option").all_inner_texts()
    assert methods and "Concept questions" not in methods


def test_concept_questions_in_the_slider_and_in_an_exam(server):
    made = server.tool("create_set", title="Concept exam", textbook=BOOK, problems=[CONCEPT], book_methods=NOTES)
    assert made["created"], made
    set_id = made["set"]
    act = lambda **body: server.post("/api/action", {"set": set_id, "problem": "p1", **body})

    view = act(action="fade", fade=100)["view"]["problem"]
    assert view["steps"][0]["status"] == "prefilled" and view["steps"][0]["answer_format"] == "text"
    assert "variation of parameters" in view["steps"][0]["answer"]
    act(action="fade", fade=0)

    act(action="exam_start", minutes=None)
    act(action="exam_own", step="q1", work="Undetermined coefficients and variation of parameters.", result="")
    view = act(action="exam_submit")["view"]
    for _ in range(50):
        rows = server.post("/api/action", {"set": set_id, "problem": "p1", "action": "tab"})["view"]["exam"]
        answered = [r for p in rows["problems"] for r in p["rows"] if r.get("own")]
        if answered and not answered[0].get("pending"):
            break
        time.sleep(0.1)
    assert answered[0]["status"] == "correct"
    blank = [r for p in rows["problems"] for r in p["rows"] if not r.get("own")]
    assert blank[0]["status"] == "blank"


def fresh_set(server, title):
    made = server.tool("create_set", title=title, textbook=BOOK, problems=PROBLEMS, book_methods=NOTES)
    assert made["created"], made
    return made["set"]


def api_get(server, path):
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", headers={"X-Worksheet-Token": server.token})
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.load(reply)


def test_skip_a_part_shows_its_answer_opens_the_next_and_can_be_undone(page, server):
    before = server.tool("error_log")
    first = open_id(page)
    page.locator("[data-action='skip']").click()
    page.locator(f"[data-skipped-step='{first}']").wait_for()
    card = page.locator(f"[data-skipped-step='{first}']")
    assert "Skipped" in card.inner_text() and card.locator(".katex").count() > 0, "its answer is shown"
    second = open_id(page)
    assert second != first
    tab = page.locator(".tab[aria-selected='true'] .count").inner_text()
    done, total = tab.split("/")
    assert done == "0"
    page.screenshot(path=SHOTS / f"24-skip-part-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    assert server.tool("error_log") == before, "a skip isn't a miss"
    page.reload()
    page.locator(f"[data-skipped-step='{first}']").wait_for()

    page.locator(f"[data-skipped-step='{first}'] [data-action='unskip']").click()
    page.locator(f"article[data-open-step='{first}']").wait_for()
    assert page.locator(".tab[aria-selected='true'] .count").inner_text() == f"0/{int(total) + 1}"


def test_skipping_every_part_still_reaches_the_worked_solution(server):
    set_id = fresh_set(server, "Skip them all")
    act = lambda **body: server.post("/api/action", {"set": set_id, "problem": "p2", **body})
    view = act(action="tab")["view"]
    while not view["problem"]["complete"]:
        open_step = next(s for s in view["problem"]["steps"] if s["status"] == "unresolved")
        view = act(action="skip_step", step=open_step["id"])["view"]
    assert view["problem"]["solution"] and all(r["status"] == "skipped" for r in view["problem"]["solution"])
    tab = next(t for t in view["tabs"] if t["id"] == "p2")
    assert tab["complete"] and tab["total"] == 0


def test_only_the_open_step_can_be_skipped(server):
    set_id = fresh_set(server, "Only the open one")
    steps = server.post("/api/action", {"set": set_id, "problem": "p2", "action": "tab"})["view"]["problem"]
    with pytest.raises(urllib.error.HTTPError) as refused:
        server.post("/api/action", {"set": set_id, "problem": "p2", "action": "skip_step", "step": "general"})
    assert refused.value.code == 400 and steps["steps"][0]["id"] != "general"


def test_skip_a_problem_moves_on_and_can_be_brought_back(page, server):
    page.locator("[data-action='skip-problem']").click()
    page.wait_for_function("document.querySelector('.tab[aria-selected=\"true\"]').dataset.tab === 'p2'")
    page.wait_for_selector("math-field[data-open='true']")
    assert page.locator(".tab[data-tab='p1'] .count").inner_text() == "skipped"
    page.screenshot(path=SHOTS / f"25-skip-problem-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    page.locator(".tab[data-tab='p1']").click()
    page.locator("[data-skipped-problem]").wait_for()
    assert not page.locator("[data-action='skip-problem']").is_visible()
    assert page.locator("#fade-value").inner_text().endswith("%")
    assert page.locator("#fade-value").inner_text() != "undefined%"
    listed = next(s for s in api_get(server, "/api/library")["sets"] if s["id"] == page.set_id)
    assert (listed["problems"], listed["skipped"], listed["done"]) == (1, 1, 0)

    page.locator("[data-action='unskip-problem']").click()
    page.wait_for_selector("math-field[data-open='true']")
    assert page.locator(".tab[data-tab='p1'] .count").inner_text().startswith("0/")
    listed = next(s for s in api_get(server, "/api/library")["sets"] if s["id"] == page.set_id)
    assert (listed["problems"], listed["skipped"]) == (2, 0)


def test_a_skipped_problem_stays_out_of_an_exam_and_nothing_can_be_skipped_during_one(server):
    set_id = fresh_set(server, "Exam without it")
    act = lambda problem="p1", **body: server.post("/api/action", {"set": set_id, "problem": problem, **body})
    act(action="skip_problem")
    view = act(action="exam_start", minutes=None)["view"]
    assert [t["id"] for t in view["tabs"]] == ["p2"] and view["active"] == "p2"
    with pytest.raises(urllib.error.HTTPError):
        act(problem="p2", action="skip_problem")
    results = act(problem="p2", action="exam_submit")["view"]["exam"]
    assert [p["id"] for p in results["problems"]] == ["p2"]
    assert results["score"]["total"] == len(results["problems"][0]["rows"])

    view = act(problem="p2", action="exam_close")["view"]
    assert {t["id"]: t.get("skipped", False) for t in view["tabs"]} == {"p1": True, "p2": False}


def test_an_exam_needs_a_problem_left(server):
    set_id = fresh_set(server, "All skipped")
    for problem in ("p1", "p2"):
        server.post("/api/action", {"set": set_id, "problem": problem, "action": "skip_problem"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        server.post("/api/action", {"set": set_id, "problem": "p1", "action": "exam_start"})
    assert "nothing to put in an exam" in json.load(refused.value)["error"]


def test_typing_y_of_x_in_the_real_answer_box_is_graded_as_y(page, server):
    server.post("/api/action", {"set": page.set_id, "problem": "p2", "action": "fade", "fade": 80})
    page.reload()
    page.locator(".tab[data-tab='p2']").click()
    page.wait_for_function("document.querySelector(\"article[data-open-step]\")?.dataset.openStep === 'general'")
    press_math(page, "y(x)=ce^", "-2x", "ArrowRight", "+e^", "x", "ArrowRight", "/3")
    typed = field(page).evaluate("node => node.value")
    assert "\\left(" in typed or "(x)" in typed, typed
    page.locator("article[data-open-step='general'] button.primary").click()
    page.wait_for_function("document.querySelector(\"article[data-open-step]\")?.dataset.openStep !== 'general'")
    assert "Correct" in page.locator("article.step.correct").last.inner_text(), typed


DIAGRAM = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80"><rect x="10" y="10" width="60" height="60" fill="none" stroke="#111"/>'
           '<text x="90" y="45" font-size="14" fill="#111">Tank</text></svg>')
GUIDE = [
    {"type": "heading", "text": "Damped motion", "own_words": True},
    {"own_words": True, "type": "text", "text": "The model is $my''+cy'+ky=0$."},
    {"own_words": True, "type": "list", "items": ["Find $\\lambda$", "Read the damping case"]},
    {"own_words": True, "type": "equation", "name": "Characteristic equation", "latex": "m\\lambda^2+c\\lambda+k=0", "note": "For $m>0$."},
    {"own_words": True, "type": "table", "headers": ["Case", "Roots"], "rows": [["Under", "complex"], ["Over", "real"]]},
    {"own_words": True, "type": "note", "title": "Watch out", "text": "Check the sign of $c$."},
    {"type": "plot", "plot_type": "curves", "title": "Decay", "curves": [{"latex": "e^{-x}\\cos 3x", "name": "y"}, {"latex": "e^{-x}", "name": "envelope"}],
     "from": 0, "to": 6, "alt": "Decaying oscillation", "caption": "The envelope shrinks."},
    {"type": "plot", "plot_type": "phase", "matrix": [[0, 1], [-2, -3]], "ic": [1, 0], "alt": "A node"},
    {"type": "diagram", "svg": DIAGRAM, "alt": "A tank", "caption": "One tank."},
    {"type": "example", "title": "Verified one", "method": "undetermined_coefficients", "statement": "y''+3y'+2y=3e^{2x}",
     "section": "2.7", "topic": "Nonhomogeneous linear ODEs"},
    {"own_words": True, "type": "example", "title": "Written one", "problem": "A beam carries $w$.", "steps": [{"label": "Sum moments", "text": "$M=wL$"}]},
]


@pytest.fixture
def guide(server):
    made = server.tool("create_doc", kind="study_guide", title=f"Guide {time.time_ns()}", subject="MAE 692", textbook=BOOK, blocks=GUIDE, source="memory")
    assert made["created"], made
    assert (made["examples_verified"], made["examples_not_checked"], made["plots"], made["diagrams"]) == (1, 1, 2, 1)
    return made["doc"]


def test_a_study_guide_lists_opens_and_draws_every_block(server, home, guide):
    home.reload()
    row = home.locator(f".set-row[data-doc='{guide}']")
    row.wait_for()
    assert "Study guide" in row.inner_text() and "MAE 692" in row.inner_text()
    row.click()
    home.locator(".doc").wait_for()
    assert home.title().startswith("Guide") and home.locator("#home-title").inner_text().startswith("Guide")
    for hidden in ("#set-picker", "#exam-toggle-label", "#fade-row", "#tabs", "#tools", "#new-set", "#home-sort"):
        assert not home.locator(hidden).is_visible(), hidden
    assert home.locator(".doc h2").inner_text() == "Damped motion"
    assert home.locator(".doc .katex-error").count() == 0 and home.locator(".doc .katex").count() >= 5
    assert home.locator(".doc-equation .name").inner_text() == "Characteristic equation"
    assert home.locator(".doc-table").first.locator("tr").count() == 3
    assert home.locator("[data-figure='lines'] polyline.series").count() == 2
    assert home.locator("[data-figure='phase'] svg.chart").count() == 1
    assert home.locator("[data-figure='lines'] .end-label").count() == 0, "a long name would be cut off, and the legend has it"
    picture = home.locator(".diagram img")
    assert picture.evaluate("node => node.complete && node.naturalWidth > 0"), "the drawing loads"

    verified, written = home.locator("[data-example='verified']"), home.locator("[data-example='written']")
    assert "verified by SymPy" in verified.inner_text() and "not checked by SymPy" in written.inner_text()
    assert not verified.locator(".doc-step").first.is_visible(), "you can try it first"
    verified.locator("summary").click()
    assert verified.locator(".doc-step").first.is_visible() and verified.locator(".doc-step .katex").count() > 0
    assert "Enter" not in verified.inner_text() and "List a" not in verified.inner_text(), "worksheet instructions stay on worksheets"
    assert "not the book's pages" in home.locator(".doc-source").inner_text()
    home.screenshot(path=SHOTS / f"30-guide-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png", full_page=True)

    home.locator("#home-link").click()
    home.locator(f".set-row[data-doc='{guide}']").wait_for()


def test_printing_shows_the_title_and_solutions_without_the_page_controls(server, home, guide):
    home.goto(f"{server.url}&doc={guide}")
    home.locator(".doc").wait_for()
    home.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    home.emulate_media(media="print")
    assert not home.locator("#top").is_visible()
    assert home.locator(".doc-print-title").is_visible()
    assert home.locator("[data-example='verified'] .doc-step").first.is_visible()
    assert home.evaluate("getComputedStyle(document.body).backgroundColor") in ("rgb(255, 255, 255)", "white")
    home.screenshot(path=SHOTS / f"31-print-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png", full_page=True)
    home.emulate_media(media="screen")


def test_an_equation_sheet_sets_in_columns(server, home):
    blocks = [{"type": "heading", "text": "Formulas"}] + [{"type": "equation", "name": f"Rule {n}", "latex": f"x^{{{n}}}+1", "note": "Note.", "own_words": True} for n in range(8)]
    made = server.tool("create_doc", kind="equation_sheet", title="A sheet", subject="MAE 692", blocks=blocks)
    assert made["created"], made
    home.goto(f"{server.url}&doc={made['doc']}")
    home.locator(".doc-equation").first.wait_for()
    assert home.locator(".doc-body").evaluate("node => getComputedStyle(node).columnCount") == "2"
    lefts = {round(box["x"]) for box in (node.bounding_box() for node in home.locator(".doc-equation").all())}
    assert len(lefts) == 2
    home.screenshot(path=SHOTS / f"32-sheet-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png", full_page=True)


def test_a_document_is_all_or_nothing_and_needs_its_repertoire_check(server):
    before = {d["id"] for d in api_get(server, "/api/library")["docs"]}
    failed = server.tool("create_doc", kind="study_guide", title="Bad", blocks=[{"type": "text", "text": "ok", "own_words": True}, {"type": "plot", "plot_type": "pie"}])
    assert failed["created"] is False and failed["failed"][0]["index"] == 1
    with pytest.raises(urllib.error.HTTPError) as refused:
        server.tool("create_doc", kind="study_guide", title="Uncovered", textbook=BOOK, blocks=[
            {"type": "example", "method": "separable", "statement": "y'=xy", "section": "1.3", "topic": "Separable"}])
    assert "repertoire check" in json.load(refused.value)["error"]
    with pytest.raises(urllib.error.HTTPError):
        server.tool("create_doc", kind="poster", title="No", blocks=[{"type": "text", "text": "x", "own_words": True}])
    assert {d["id"] for d in api_get(server, "/api/library")["docs"]} == before


def test_the_form_asks_for_what_each_kind_needs_and_finishes_with_its_row(server, home):
    before = len(server.prompts)
    home.locator("#new-set").click()
    assert home.locator("#new-count").is_visible() and home.locator("#new-typed").is_visible()
    assert not home.locator("#new-examples").is_visible()
    home.select_option("#new-kind", "equation_sheet")
    for hidden in ("#new-count", "#new-difficulty", "#new-typed", "#new-examples", "#new-figures"):
        assert not home.locator(hidden).is_visible(), hidden
    home.select_option("#new-kind", "study_guide")
    assert home.locator("#new-examples").is_checked() and home.locator("#new-figures").is_visible()
    assert home.locator("#new-examples").bounding_box()["width"] < 30, "not stretched across the row"
    assert not home.locator("#new-count").is_visible()
    home.screenshot(path=SHOTS / f"33-guide-form-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    home.fill("#new-book", "")
    home.fill("#new-edition", "")
    home.fill("#new-subject", "Thermodynamics")
    home.locator("#new-submit").click()
    home.wait_for_function("document.getElementById('new-error').textContent.includes('sections or topics')")
    home.errors.clear()
    home.fill("#new-topics", "The first law")
    home.locator("#new-examples").uncheck()
    home.locator("#new-submit").click()
    row = home.locator(".set-row[data-request]").last
    row.wait_for()
    assert "Study guide" in row.inner_text() and "The first law" in row.inner_text()

    for _ in range(50):
        if len(server.prompts) > before:
            break
        time.sleep(0.1)
    sent = server.prompts[-1]
    for wanted in ("make-doc", "study guide", "Class or subject: Thermodynamics", "The first law", "Include example problems: no",
                   "Include plots and diagrams: yes", f'request_id "{sent["id"]}"', 'kind "study_guide"'):
        assert wanted in sent["text"], wanted
    assert "Textbook" not in sent["text"]

    made = server.tool("create_doc", kind="study_guide", title="First law", subject="Thermodynamics", request_id=sent["id"],
                       blocks=[{"type": "text", "text": "Energy is conserved.", "own_words": True}])
    home.locator(f".set-row[data-doc='{made['doc']}']").wait_for(timeout=8000)
    assert home.locator(f".set-row[data-request='{sent['id']}']").count() == 0
    home.locator("#new-set").click()
    assert home.locator("#new-subject").input_value() != "", "starts from the latest one"
    home.locator("#new-cancel").click()


def test_the_list_can_show_one_kind(server, home, guide):
    sheet = server.tool("create_doc", kind="equation_sheet", title="Sheet to filter", blocks=[{"type": "equation", "latex": "x", "own_words": True}])["doc"]
    worksheet = make_set(server, "Worksheet to filter")
    home.reload()
    home.locator(f".set-row[data-set='{worksheet}']").wait_for()
    home.select_option("#home-filter", "equation_sheet")
    assert home.locator(f".set-row[data-doc='{sheet}']").count() == 1
    assert home.locator(f".set-row[data-doc='{guide}']").count() == 0 and home.locator(".set-row[data-set]").count() == 0
    home.select_option("#home-filter", "worksheet")
    assert home.locator(f".set-row[data-set='{worksheet}']").count() == 1 and home.locator(".set-row[data-doc]").count() == 0
    home.select_option("#home-sort", "progress")
    home.reload()
    home.locator(".set-row").first.wait_for()
    assert home.locator("#home-filter").input_value() == "worksheet"
    home.select_option("#home-filter", "all")
    home.select_option("#home-sort", "recent")


class SourceSite:
    """A stand-in website on this machine, so the real fetching code runs."""
    PAGE = ("<html><body><p>" + "Filler text about physics that pads this page out. " * 40 +
            "<b>Hooke's law</b> says the restoring force is proportional to the displacement.</p></body></html>")


@pytest.fixture(scope="module")
def site():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/hooke":
                self.send_error(404)
                return
            body = SourceSite.PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    running = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}"
    running.shutdown()


QUOTE = "Hooke's law says the restoring force is proportional to the displacement"


def test_a_source_opens_the_quoted_passage_and_is_listed_with_its_check(server, home, site):
    source = {"title": "Hooke's law notes", "url": f"{site}/hooke", "quote": QUOTE}
    made = server.tool("create_doc", kind="study_guide", title="Cited guide", blocks=[
        {"type": "heading", "text": "Springs", "sources": [source]},
        {"type": "text", "text": "The force grows with distance."},
        {"type": "equation", "latex": "F=-kx", "sources": [source]},
        {"type": "note", "text": "Picture a door spring.", "own_words": True}])
    assert made["created"], made
    assert made["sources_confirmed"] == 2 and made["sources_not_auto_checked"] == [] and made["blocks_in_own_words"] == 3, "the note, plus the helper's required blocks"
    home.goto(f"{server.url}&doc={made['doc']}")
    link = home.locator(".doc-body a.source-link").first
    link.wait_for()
    href = link.get_attribute("href")
    assert href.startswith(f"{site}/hooke#:~:text=Hooke") and link.get_attribute("rel") == "noopener noreferrer"
    assert link.get_attribute("target") == "_blank" and link.inner_text() == "[1] Hooke's law notes"
    assert home.locator(".doc-body a.source-link").count() == 2, "both cite one numbered source"
    assert "no source" in home.locator(".own-words").first.inner_text()
    assert "Ctrl+F" in home.locator(".doc-source").inner_text(), "it doesn't promise a jump to the passage"
    listed = home.locator(".doc-sources li")
    assert listed.count() == 1 and QUOTE in listed.inner_text() and "found here" in listed.inner_text()
    with home.context.expect_page() as opened:
        link.click()
    opened.value.wait_for_load_state()
    assert opened.value.url.startswith(f"{site}/hooke")
    opened.value.close()
    home.screenshot(path=SHOTS / f"34-sources-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png", full_page=True)


def test_a_doc_with_an_uncited_claim_or_a_false_quote_or_a_dead_link_is_refused(server, site):
    def attempt(*blocks):
        return server.tool("create_doc", kind="study_guide", title="Refused", blocks=list(blocks))
    uncited = attempt({"type": "text", "text": "A claim."})
    assert uncited["created"] is False and "no source" in uncited["failed"][0]["reason"]
    invented = attempt({"type": "text", "text": "A claim.", "sources": [{"title": "T", "url": f"{site}/hooke", "quote": "the moon is made of cheese today"}]})
    assert "quote isn't on" in invented["failed"][0]["reason"]
    dead = attempt({"type": "text", "text": "A claim.", "sources": [{"title": "T", "url": f"{site}/nowhere", "quote": QUOTE}]})
    assert "404" in dead["failed"][0]["reason"]
    # A heading's source covers its section, and only until the next heading.
    ended = attempt({"type": "heading", "text": "A", "sources": [{"title": "T", "url": f"{site}/hooke", "quote": QUOTE}]},
                    {"type": "text", "text": "Covered."}, {"type": "heading", "text": "B"}, {"type": "text", "text": "Not covered."})
    assert [f["index"] for f in ended["failed"]] == [3]


def test_a_worksheet_shows_sources_only_once_the_answer_is_out(server, home, site):
    source = {"title": "Method notes", "url": f"{site}/hooke", "quote": QUOTE}
    question = {"question": "What does a spring's force depend on?", "answer": "The displacement.", "nudge": "n", "method_hint": "h", "sources": [source]}
    notes = {"first_order_linear": {"summary": "A summary.", "source": "memory", "sources": [source]}}
    made = server.tool("create_set", title="Cited set", textbook=BOOK, problems=[
        {"statement": "\\text{Concept check}", "method": "concept_questions", "section": "1.5", "topic": "T", "questions": [question]},
        PROBLEMS[1]], book_methods=notes)
    assert made["created"], made
    home.goto(f"{server.url}&set={made['set']}")
    home.locator("textarea[data-discussion='q1']").wait_for()
    assert home.locator("[data-sources]").count() == 0, "a source could give the answer away"
    view = server.post("/api/action", {"set": made["set"], "problem": "p1", "action": "tab"})["view"]
    assert "sources" not in view["problem"]["steps"][0]
    home.locator("textarea[data-discussion='q1']").fill("The displacement.")
    home.locator("[data-action='own']").click()
    home.locator("[data-solution] a.source-link").wait_for()
    assert home.locator("article.step.correct a.source-link").inner_text() == "Method notes"
    home.locator(".tab[data-tab='p2']").click()
    home.locator("details.own-method summary", has_text="How ").first.click()
    assert home.locator("details.own-method a.source-link").first.is_visible()

    refused = server.tool("create_set", title="Uncited", textbook=BOOK, problems=[PROBLEMS[1]],
                          book_methods={"first_order_linear": {"summary": "A summary."}})
    assert refused["created"] is False and "no source" in refused["failed"][0]["reason"]


PDF_TEXT = "Waves carry energy but they do not carry mass"


def small_pdf(path, text):
    """One page with real text, built by hand so the test needs no PDF library."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF\n".encode()
    path.write_bytes(out)


def test_a_pdf_source_links_to_its_page_and_needs_the_token(server, home, tmp_path):
    pytest.importorskip("shutil")
    import shutil
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext isn't installed")
    pdf = tmp_path / "slides.pdf"
    small_pdf(pdf, PDF_TEXT)
    made = server.tool("create_doc", kind="study_guide", title="From my slides", blocks=[
        {"type": "text", "text": "Mass stays put.", "sources": [{"title": "Lecture slides", "file": str(pdf), "page": 1, "quote": PDF_TEXT}]}])
    assert made["created"] and made["sources_confirmed"] == 1, made
    home.goto(f"{server.url}&doc={made['doc']}")
    link = home.locator(".doc-body a.source-link")
    link.wait_for()
    href = link.get_attribute("href")
    assert link.inner_text() == "[1] Lecture slides, p. 1" and href.startswith("/source/") and href.endswith("#page=1")

    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}{href.split('#')[0]}", timeout=10) as reply:
        assert reply.read() == pdf.read_bytes() and reply.headers["Content-Type"] == "application/pdf"
    name = href.split("?")[0]
    for bad in (name, f"{name}?token=wrong", f"/source/..%2Fserver.json?token={server.token}", f"/source/server.json?token={server.token}"):
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"http://127.0.0.1:{server.port}{bad}", timeout=10)
        assert refused.value.code in (403, 404), bad

    wrong = {"title": "S", "file": str(pdf), "page": 1, "quote": "this sentence is nowhere in those slides"}
    for source, words in ((wrong, "quote isn't on page 1"), ({**wrong, "page": 9, "quote": PDF_TEXT}, "only 1 pages"), ({**wrong, "page": None, "quote": PDF_TEXT}, "page number")):
        refused = server.tool("create_doc", kind="study_guide", title="No", blocks=[{"type": "text", "text": "x", "sources": [source]}])
        assert words in refused["failed"][0]["reason"], words


SIDE_PANEL_BLOCKS = [
    {"type": "heading", "text": "Start", "own_words": True},
    {"type": "definition", "term": "Amplitude", "meaning": "How far it goes from the middle."},
    {"type": "nomenclature", "rows": [{"symbol": "$k$", "meaning": "Spring stiffness", "unit": "N/m"}]},
    {"type": "equation", "name": "Period", "latex": "T=2\\pi\\sqrt{m/k}"},
    {"type": "table", "tab": "Which formula", "headers": ["Given", "Use"], "rows": [["$k$ and $m$", "$T=2\\pi\\sqrt{m/k}$"]]},
    *[{"type": "text", "text": f"Long body paragraph number {n}. " * 20} for n in range(40)],
]
SIDE_PANEL_BLOCKS = [{**block, "own_words": True} for block in SIDE_PANEL_BLOCKS]


@pytest.fixture
def reference(server, home):
    made = server.tool("create_doc", kind="study_guide", title="Tabs guide", blocks=SIDE_PANEL_BLOCKS)
    assert made["created"], made
    home.goto(f"{server.url}&doc={made['doc']}")
    home.locator(".doc").wait_for()
    return home


def test_quick_reference_opens_beside_the_guide_and_keeps_its_place_while_you_scroll(reference):
    tab = reference
    assert not tab.locator("#ref-panel").is_visible(), "it starts closed"
    tab.set_viewport_size({"width": 1300, "height": 800})
    tab.evaluate("window.scrollTo(0, 2000)")
    reading = """() => {
      const top = document.getElementById('top').getBoundingClientRect().bottom;
      const node = [...document.querySelectorAll('.doc-body > *')].find((n) => n.getBoundingClientRect().bottom > top);
      return [node.textContent.slice(0, 40), node.getBoundingClientRect().top];
    }"""
    before = tab.evaluate(reading)
    tab.locator("#ref-toggle").click()
    after = tab.evaluate(reading)
    assert after[0] == before[0] and abs(after[1] - before[1]) <= 2, "opening it doesn't lose your place"
    panel, main = tab.locator("#ref-panel").bounding_box(), tab.locator("main").bounding_box()
    assert panel["x"] >= main["x"] + main["width"] - 1, "it sits beside the text, not over it"
    assert tab.locator("#ref-tabs button").all_inner_texts() == ["Definitions", "Equations", "Nomenclature", "Which formula"]
    assert "How far it goes" in tab.locator("#ref-body").inner_text()
    tab.screenshot(path=SHOTS / f"35-side-panel-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    tab.locator("#ref-tabs button", has_text="Equations").click()
    assert "Period" in tab.locator("#ref-body").inner_text() and tab.locator("#ref-body .katex").count() > 0
    tab.locator("#ref-tabs button", has_text="Nomenclature").click()
    assert "Spring stiffness" in tab.locator("#ref-body").inner_text()
    tab.locator("#ref-tabs button", has_text="Which formula").click()
    assert "Given" in tab.locator("#ref-body").inner_text()
    tab.evaluate("window.scrollTo(0, 3000)")
    assert tab.locator("#ref-panel").is_visible() and tab.locator("#ref-body").is_visible()
    # They stay in the guide too.
    assert tab.locator(".doc-body .doc-definition").count() == 1 and tab.locator(".doc-body .doc-equation").count() == 1


def test_a_study_guide_must_have_what_the_quick_reference_shows(server):
    text = {"type": "text", "text": "Words.", "own_words": True}
    equation = {"type": "equation", "name": "E", "latex": "x=1", "own_words": True}
    definition = {"type": "definition", "term": "T", "meaning": "M", "own_words": True}
    symbols = {"type": "nomenclature", "rows": [{"symbol": "$x$", "meaning": "M"}], "own_words": True}
    for blocks, words in (([text], "definition block"), ([equation], "definition block"), ([definition, equation], "nomenclature block")):
        with pytest.raises(urllib.error.HTTPError) as refused:
            server.tool("create_doc", bare=True, kind="study_guide", title="No", blocks=blocks)
        assert words in json.load(refused.value)["error"]
    assert server.tool("create_doc", bare=True, kind="study_guide", title="History", blocks=[text, definition])["created"], "no equations, so no symbols needed"
    assert server.tool("create_doc", bare=True, kind="study_guide", title="Physics", blocks=[definition, equation, symbols])["created"]
    assert server.tool("create_doc", bare=True, kind="equation_sheet", title="Sheet", blocks=[equation])["created"], "a sheet has no panel"


def test_quick_reference_tabs_are_grouped_by_section(server, home):
    equation = lambda name: {"type": "equation", "name": name, "latex": "x=1", "own_words": True}
    made = server.tool("create_doc", kind="study_guide", title="Grouped", blocks=[
        {"type": "heading", "text": "Springs"}, equation("One"), equation("Two"), {"type": "heading", "text": "Waves"}, equation("Three"),
        {"type": "definition", "term": "T", "meaning": "M", "own_words": True}, {"type": "nomenclature", "rows": [{"symbol": "$x$", "meaning": "M"}], "own_words": True}])
    home.goto(f"{server.url}&doc={made['doc']}")
    home.locator(".doc").wait_for()
    home.locator("#ref-toggle").click()
    home.locator("#ref-tabs button", has_text="Equations").click()
    assert home.locator("#ref-body .ref-section").all_inner_texts() == ["Springs", "Waves"]
    assert home.locator("#ref-body .doc-equation").count() == 3
    home.locator("#ref-close").click()


def test_quick_reference_expands_to_a_popup_and_collapses_back_to_the_side_panel(reference):
    tab = reference
    tab.set_viewport_size({"width": 1300, "height": 800})
    tab.locator("#ref-toggle").click()
    side = tab.locator("#ref-panel").bounding_box()
    tab.locator("#ref-tabs button", has_text="Equations").click()
    tab.locator("#ref-expand").click()
    big = tab.locator("#ref-panel").bounding_box()
    assert big["width"] > side["width"] * 1.5 and tab.locator("#ref-backdrop").is_visible() and tab.locator("#ref-expand").inner_text() == "Back to side panel"
    assert "Period" in tab.locator("#ref-body").inner_text(), "it stays on the same tab"
    tab.screenshot(path=SHOTS / f"36-popup-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    tab.locator("#ref-expand").click()
    assert tab.locator("#ref-panel").bounding_box()["width"] == side["width"] and not tab.locator("#ref-backdrop").is_visible()
    tab.locator("#ref-expand").click()
    tab.keyboard.press("Escape")
    assert tab.locator("#ref-panel").is_visible() and tab.locator("#ref-panel").bounding_box()["width"] == side["width"], "Escape goes back to the side panel"
    tab.locator("#ref-expand").click()
    tab.locator("#ref-backdrop").click(position={"x": 5, "y": 5})
    assert tab.locator("#ref-panel").bounding_box()["width"] == side["width"]

    tab.locator("#ref-close").click()
    assert not tab.locator("#ref-panel").is_visible()
    tab.locator("#ref-toggle").click()
    assert tab.locator("#ref-panel").is_visible() and tab.locator("#ref-panel").bounding_box()["width"] == side["width"]
    tab.reload()
    tab.locator(".doc").wait_for()
    assert tab.locator("#ref-panel").is_visible() and "Period" in tab.locator("#ref-body").inner_text(), "it remembers"
    tab.emulate_media(media="print")
    assert not tab.locator("#ref-panel").is_visible()
    tab.emulate_media(media="screen")
    tab.locator("#ref-close").click()


def test_quick_reference_fits_a_narrow_window_and_stays_away_from_other_documents(server, reference):
    tab = reference
    tab.set_viewport_size({"width": 420, "height": 700})
    tab.locator("#ref-toggle").click()
    box = tab.locator("#ref-panel").bounding_box()
    assert box["x"] >= 0 and box["width"] <= 420
    tab.locator("#ref-expand").click()
    box = tab.locator("#ref-panel").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 420
    tab.locator("#ref-expand").click()
    tab.locator("#ref-close").click()
    tab.set_viewport_size({"width": 1000, "height": 900})
    sheet = server.tool("create_doc", kind="equation_sheet", title="No panel sheet", blocks=[{"type": "equation", "latex": "x", "own_words": True}])
    tab.goto(f"{server.url}&doc={sheet['doc']}")
    tab.locator(".doc").wait_for()
    assert not tab.locator("#ref-toggle").is_visible(), "a sheet is already all equations"


def upload(server, name, body, **details):
    query = urllib.parse.urlencode({"name": name, "class_name": "Waves", "class_number": "PHYS 101", "subject": "Physics", "doc_type": "Notes", **details})
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/files/upload?{query}", data=body, method="POST",
                                     headers={"X-Worksheet-Token": server.token, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.load(reply)


@pytest.fixture
def library_page(server, home):
    home.goto(f"{server.url}&library=1")
    home.locator(".library-filters").wait_for()
    return home


def test_the_home_page_links_to_the_library(server, home):
    home.locator("#library-link").click()
    home.locator(".library-filters").wait_for()
    assert home.locator("#home-title").inner_text() == "My library" and "library=1" in home.url
    home.locator("#home-link").click()
    home.locator("#new-set").wait_for()


def test_upload_saves_files_filters_them_and_asks_claude_to_index_once(server, library_page, tmp_path):
    tab = library_page
    before = len(server.prompts)
    first, second = tmp_path / "waves-notes.txt", tmp_path / "bad.exe"
    first.write_text("Standing waves have nodes and antinodes in my own handwritten summary of the unit.")
    second.write_text("nope")
    tab.locator("#library-add").click()
    tab.locator("#library-files").set_input_files([str(first), str(second)])
    assert "2 files chosen" in tab.locator("#library-drop").inner_text()
    tab.screenshot(path=SHOTS / f"37-add-files-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")
    tab.fill("#library-class-name", "")
    tab.fill("#library-class-number", "")
    tab.locator("#library-submit").click()
    assert "name or a number" in tab.locator("#library-error").inner_text()
    tab.fill("#library-class-name", "Vibrations")
    tab.fill("#library-class-number", "PHYS 202")
    tab.fill("#library-subject", "Physics")
    tab.select_option("#library-doc-type", "Notes")
    tab.locator("#library-submit").click()
    tab.locator("#library-progress .failed").wait_for()
    assert "isn't supported" in tab.locator("#library-progress .failed").inner_text(), "one bad file doesn't stop the rest"
    tab.errors.clear()
    tab.locator("#library-cancel").click()
    row = tab.locator(".library-row", has_text="waves-notes.txt")
    row.wait_for()
    assert "PHYS 202 Vibrations" in row.inner_text() and "Claude is indexing" in row.inner_text()

    for _ in range(50):
        if len(server.prompts) > before:
            break
        time.sleep(0.1)
    sent = server.prompts[-1]
    file_id = row.get_attribute("data-library-file")
    assert "make-index" in sent["text"] and file_id in sent["text"]

    tab.goto(f"{server.url}")
    tab.wait_for_selector("#main .set-row, #main p")
    assert tab.locator(".set-row[data-request]", has_text=file_id).count() == 0, "indexing shows on the Library page, not the home page"
    assert server.tool("library_save_index", file=file_id, summary="My notes on waves.", topics=[{"topic": "Standing waves", "pages": "1"}])["indexed"]
    assert "My notes on waves." in server.tool("library_overview", class_name="PHYS 202")["overviews"]["PHYS 202 Vibrations"]
    assert server.tool("library_search", query="antinodes handwritten")["hits"][0]["file"] == file_id
    assert "nodes" in server.tool("library_read", file=file_id, pages=[1])["pages"][0]["text"]
    assert server.tool("library_read", file=file_id, outline=True)["outline"][0]["first_line"].startswith("Standing")

    tab.goto(f"{server.url}&library=1")
    row = tab.locator(f".library-row[data-library-file='{file_id}']")
    row.wait_for()
    assert "Ready to use" in row.inner_text()
    with tab.context.expect_page() as opened:
        row.locator("a.name").click()
    opened.value.wait_for_load_state()
    assert "Standing waves" in opened.value.locator("body").inner_text()
    opened.value.close()


def test_the_library_filters_sorts_and_searches_inside_files(server, library_page):
    tab = library_page
    made = [upload(server, "alpha.txt", f"unique{time.time_ns()} ordinary words about pressure in a pipe".encode(), class_name="Fluids", class_number="MAE 300", subject="Engineering", doc_type="Homework"),
            upload(server, "zulu.txt", b"different words about the integral of a function here", class_name="Calculus", class_number="MATH 100", subject="Math", doc_type="Notes")]
    tab.reload()
    tab.locator(".library-row").first.wait_for()
    assert tab.locator(".library-row .name").first.inner_text() == "zulu.txt", "most recent first"
    tab.select_option("[data-sort]", "name")
    names = tab.locator(".library-row .name").all_inner_texts()
    assert names == sorted(names, key=str.lower)
    tab.select_option("[data-sort]", "class")
    assert tab.locator(".library-row[data-library-file] .chip").first.inner_text() != ""
    tab.select_option("[data-sort]", "recent")

    tab.select_option("[data-filter='subject']", "Math")
    assert tab.locator(".library-row .name").all_inner_texts() == ["zulu.txt"]
    tab.select_option("[data-filter='subject']", "")
    tab.select_option("[data-filter='doc_type']", "Homework")
    assert "alpha.txt" in tab.locator(".library-row .name").all_inner_texts() and "zulu.txt" not in tab.locator(".library-row .name").all_inner_texts()
    tab.select_option("[data-filter='doc_type']", "")
    tab.select_option("[data-filter='class']", "MATH 100 Calculus")
    assert tab.locator(".library-row .name").all_inner_texts() == ["zulu.txt"]
    tab.select_option("[data-filter='class']", "")
    tab.select_option("[data-filter='status']", "Not indexed")
    assert tab.locator(".library-row").count() >= 2
    tab.select_option("[data-filter='status']", "")
    tab.screenshot(path=SHOTS / f"38-library-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    tab.fill("[data-search]", "pressure pipe")
    tab.wait_for_function("document.querySelector('.library-hit')")
    assert tab.locator(".library-row .name").all_inner_texts() == ["alpha.txt"]
    hit = tab.locator(".library-hit a")
    assert hit.inner_text() == "part 1" and "#page=1" in hit.get_attribute("href")
    tab.fill("[data-search]", "zulu")
    tab.wait_for_function("document.querySelectorAll('.library-row').length === 1 && document.querySelector('.library-row .name').textContent === 'zulu.txt'")
    tab.fill("[data-search]", "")
    tab.wait_for_function("document.querySelectorAll('.library-row').length > 1")
    assert "Showing" in tab.locator(".library-count").inner_text()

    row = tab.locator(f".library-row[data-library-file='{made[1]['id']}']")
    row.locator("[data-action='library-edit']").click()
    assert tab.locator("#library-class-name").input_value() == "Calculus" and not tab.locator("#library-drop").is_visible()
    tab.fill("#library-class-name", "Calculus II")
    tab.locator("#library-submit").click()
    tab.wait_for_function("id => document.querySelector(`[data-library-file=\"${id}\"]`).textContent.includes('Calculus II')", arg=made[1]["id"])

    tab.on("dialog", lambda dialog: dialog.accept())
    row.locator("[data-action='library-delete']").click()
    tab.wait_for_function("id => !document.querySelector(`[data-library-file=\"${id}\"]`)", arg=made[1]["id"])


def test_files_need_the_token_and_the_form_offers_library_classes(server, home):
    made = upload(server, "secret.txt", b"private words that should need the token to read them", class_name="Private")
    address = f"http://127.0.0.1:{server.port}/file/{made['id']}"
    with urllib.request.urlopen(f"{address}?token={server.token}", timeout=10) as reply:
        assert b"private words" in reply.read() and reply.headers["X-Content-Type-Options"] == "nosniff" and "text/plain" in reply.headers["Content-Type"]
    for bad in (address, f"{address}?token=wrong", f"http://127.0.0.1:{server.port}/file/..%2F..%2Fserver.json?token={server.token}"):
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(bad, timeout=10)
        assert refused.value.code in (403, 404)
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{server.port}/api/files/upload?name=x.txt&class_name=C", data=b"x", method="POST"), timeout=10)
    assert refused.value.code == 403

    home.reload()
    home.locator("#new-set").click()
    home.wait_for_function("[...document.getElementById('new-library-class').options].some(o => o.value === 'PHYS 101 Private')")
    home.select_option("#new-kind", "study_guide")
    home.fill("#new-topics", "Secrets")
    home.select_option("#new-library-class", "PHYS 101 Private")
    before = len(server.prompts)
    home.locator("#new-submit").click()
    home.locator(".set-row[data-request]").first.wait_for()
    for _ in range(50):
        if len(server.prompts) > before:
            break
        time.sleep(0.1)
    assert "library_overview" in server.prompts[-1]["text"] and "PHYS 101 Private" in server.prompts[-1]["text"]
    server.post("/api/action", {"action": "dismiss_request", "id": server.prompts[-1]["id"]})


def test_a_guide_can_cite_a_library_page(server, home):
    made = upload(server, "cite.txt", b"The restoring force is proportional to the displacement of the mass here.", class_name="Cited")
    doc = server.tool("create_doc", kind="study_guide", title="Cites library", blocks=[
        {"type": "text", "text": "Force grows with distance.", "sources": [{"title": "My notes", "library": made["id"], "page": 1, "quote": "restoring force is proportional to the displacement"}]}])
    assert doc["created"] and doc["sources_confirmed"] == 1, doc
    home.goto(f"{server.url}&doc={doc['doc']}")
    link = home.locator(".doc-body a.source-link")
    link.wait_for()
    assert link.inner_text() == "[1] My notes, part 1" and link.get_attribute("href").startswith(f"/file/{made['id']}?") and link.get_attribute("href").endswith("#page=1")


def test_the_home_page_is_called_study_materials(home):
    assert home.locator("#home-title").inner_text() == "Study materials" and home.title() == "Study materials"


def test_help_opens_from_the_home_page_and_covers_every_feature_with_an_example_and_tips(server, home):
    assert home.locator("#help-link").inner_text() == "Help"
    home.locator("#help-link").click()
    home.locator(".help-section").first.wait_for()
    assert home.locator("#home-title").inner_text() == "Help" and home.title() == "Help" and "help=1" in home.url
    for hidden in ("#new-set", "#help-link", "#library-link", "#home-sort", "#set-picker", "#tools", "#fade-row"):
        assert not home.locator(hidden).is_visible(), hidden
    sections = home.locator(".help-section")
    assert sections.count() >= 25
    for section in sections.all():
        assert section.locator(".help-example").inner_text().strip(), section.get_attribute("id")
        assert section.locator("li").count() >= 1, section.get_attribute("id")
    assert home.locator(".help-section .katex-error").count() == 0 and home.locator(".help-section .katex").count() > 5
    assert "[[" not in home.locator("main").inner_text(), "every label is drawn"
    assert home.locator("main").evaluate("n => n.scrollWidth <= n.clientWidth + 1"), "nothing runs off the page"
    home.screenshot(path=SHOTS / f"39-help-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")

    home.locator(".help-contents a", has_text="Exam mode").click()
    home.wait_for_function("Math.abs(document.getElementById('exam').getBoundingClientRect().top - 70) < 60")
    home.screenshot(path=SHOTS / f"40-help-exam-{os.environ.get('WORKSHEET_BROWSER', 'chromium')}.png")
    assert "token=" in home.url, "jumping keeps the private key in the address"
    home.locator("#theme-toggle").click()
    home.locator("#home-link").click()
    home.locator("#new-set").wait_for()


def test_every_button_the_help_page_names_really_exists():
    import re
    help_text = (ROOT / "web/help.js").read_text()
    interface = (ROOT / "web/app.js").read_text() + (ROOT / "web/index.html").read_text()
    labels = set(re.findall(r"\[\[(.+?)\]\]", help_text))
    assert len(labels) > 25, labels
    assert sorted(label for label in labels if label not in interface) == []


def test_the_help_page_mentions_every_feature_area():
    help_text = (ROOT / "web/help.js").read_text().lower()
    for feature in ("exam mode", "pre-filled", "hint", "explain why", "my own method", "skip", "concept", "difficulty", "repertoire", "error log", "cheat sheet",
                    "quick reference", "equation sheet", "print", "source", "library", "index", "own explanation", "privacy", "bookmark"):
        assert feature in help_text, feature


def test_export_downloads_what_is_showing_and_import_brings_it_back(server, library_page, tmp_path):
    tab = library_page
    name = f"export-{time.time_ns()}.txt"
    made = upload(server, name, b"Words about exporting a file between two libraries for safe keeping.", class_name=f"Export {time.time_ns()}")
    other = upload(server, f"other-{time.time_ns()}.txt", b"This one is left out of the export because of the filter set on the page.", class_name=f"Left out {time.time_ns()}")
    tab.reload()
    tab.locator(f"[data-library-file='{made['id']}']").wait_for()
    tab.select_option("[data-filter='class']", made["class_number"] + " " + made["class_name"])
    assert tab.locator(".library-row").count() == 1
    tab.on("dialog", lambda dialog: dialog.accept())
    with tab.expect_download() as download:
        tab.locator("#library-export").click()
    saved = tmp_path / "library-export.zip"
    download.value.save_as(saved)
    import zipfile
    with zipfile.ZipFile(saved) as archive:
        names = archive.namelist()
    assert "library-export.json" in names and any(made["id"] in n for n in names) and not any(other["id"] in n for n in names)
    with pytest.raises(urllib.error.HTTPError) as gone:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/export/{'a' * 32}.zip?token={server.token}", timeout=5)
    assert gone.value.code == 404

    tab.locator(f"[data-library-file='{made['id']}'] [data-action='library-delete']").click()
    tab.wait_for_function('id => !document.querySelector(`[data-library-file="${id}"]`)', arg=made["id"])
    tab.select_option("[data-filter='class']", "")
    tab.locator("#library-import-file").set_input_files(str(saved))
    tab.locator(f"[data-library-file='{made['id']}']").wait_for()
    assert "Imported 1 file" in tab.locator("#toast").inner_text()
    assert server.tool("library_search", query="exporting safe keeping")["hits"][0]["file"] == made["id"]

    tab.locator("#library-import-file").set_input_files(str(saved))
    tab.wait_for_function("document.getElementById('toast').textContent.includes('already here')")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    tab.locator("#library-import-file").set_input_files(str(bad))
    tab.wait_for_function("document.getElementById('toast').textContent.includes(\"isn't a library export\")")
    tab.errors.clear()
    assert tab.locator("#library-export").is_visible() and tab.locator("#library-import").is_visible()


def test_export_and_import_need_the_private_key(server):
    for request in (urllib.request.Request(f"http://127.0.0.1:{server.port}/api/files/import", data=b"x", method="POST"),
                    urllib.request.Request(f"http://127.0.0.1:{server.port}/export/{'a' * 32}.zip"),
                    urllib.request.Request(f"http://127.0.0.1:{server.port}/export/{'a' * 32}.zip?token=wrong")):
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)
        assert refused.value.code == 403
