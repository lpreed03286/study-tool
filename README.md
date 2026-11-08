# math-worksheet

A local study tool for Claude Code: interactive step-by-step ODE worksheets graded by SymPy, plus
study guides and equation sheets for any class, a personal class library, and sources you can check.
It runs on your own computer, on 127.0.0.1, with no accounts or hosting. Your own material stays on
your computer, and anything Claude reads goes to Anthropic under your own Claude account's terms.

## What you need

- [Claude Code](https://claude.com/claude-code), with your own Claude account. The program uses your Claude usage.
- Python 3.10 or newer. The first `/worksheet` sets up SymPy in a private folder, which takes a few minutes and needs internet. On Nix, it uses Nix instead.
- A web browser.
- Optional, for the library: `poppler-utils` for PDFs, and LibreOffice for Word and PowerPoint files.

Developed and tested on Linux. It's written to work on macOS and Windows, but hasn't been tested there.

## Install

At the prompt of a Claude Code terminal session:

```
/plugin install math-worksheet --marketplace lpreed03286/study-tool
```

Answer `y` to add the marketplace, then choose a scope. From a shell, use this instead:

```
claude plugin install math-worksheet --marketplace lpreed03286/study-tool
```

Then start `claude` and type `/worksheet`. Press **Help** on the page for a tour of every feature.

To update later:

```
claude plugin marketplace update study-tool
claude plugin update math-worksheet@study-tool
```

The program starts with an empty library. Nothing of anyone else's comes with it.

## Layout

    hooks/register.tsx      /worksheet, the tools, starts the server, asks Claude
    skills/make-set/        how Claude makes a set and extends the repertoire
    skills/make-course/     how Claude runs an experimental course, one section at a time
    server/mathparse.py     LaTeX to SymPy
    server/equiv.py         exact equality: disproof by evaluation, then symbolic proof
    server/grade.py         one mathematical check per step type
    server/methods.py       the method repertoire; keys computed by SymPy
    server/verify.py        independent verification of every key
    server/engine.py        worksheet rules, progress, error log, coverage
    server/server.py        local web server
    server/worker.py        the process that does the math, so it can be killed
    server/prompts.py       what Claude is asked
    server/library.py       the class library: saved text, search and overviews
    server/sources.py       opens each cited page and checks its quote
    server/documents.py     study guides and equation sheets: plots, examples, drawings
    server/courses.py       courses: syllabus rules, unlocking, mastery and the overview
    server/course_items.py  course check items: multiple choice, numeric and short answer
    web/                    the page, with MathLive and KaTeX vendored
    tests/                  see below

Your data lives in `~/.local/share/math-worksheet/`, never in this folder, so sharing the program can't share your data:

    sets/         verified problem sets
    progress/     where you are in each set
    error_log.json
    coverage.json which methods were checked against which textbook sections
    repertoire/   methods added after install
    courses/      each course, and a short overview of it that Claude reads first

## The home page

`/worksheet` opens a list of your worksheets, which you can sort. **New** opens a
form, and submitting it sends the request to the open Claude Code session as your
own message. Claude builds and verifies the set with the make-set skill, and the
list picks it up by itself. Any question Claude has appears in the terminal. A
request is saved in `requests.json` until it finishes or you dismiss it.

## Study guides and equation sheets

The **New** form can also make a study guide, with or without example problems, or an
equation sheet, for any class. They open as printable pages. Plots are computed from their
formulas, and an example that uses a repertoire method is built and verified like a
worksheet problem. Everything else is written by Claude and labeled that way, because
SymPy can't check it. Diagrams are simple drawings by Claude, not pictures from a book.

## The library

The **Library** page keeps your files for each class. Adding a file saves a copy and its text page
by page, with no Claude usage, so it's searchable at once. Word and PowerPoint files are converted
to PDF with LibreOffice, without speaker notes, so they have real page numbers. Claude then indexes each file once.
Scans and handwriting can't be read by those tools, so Claude reads those pages once and saves what it
sees. Each class gets an overview in `library/classes/`, which Claude reads first, so later work reads only
the pages it needs. Files live in `library/files/`.

## Help

The **Help** button on the home page explains every feature, with an example and tips. Its text is in
`web/help.js`, so update it when a feature changes. A test checks that every button it names exists.

## Exporting and importing

The Library page's **Export** saves the files you're showing as one zip, with their text and index,
and **Import** adds such a zip to a library. Importing never lets a zip choose where anything is
written, and it runs nothing inside it. A zip holds your actual files, so share it with care.

## Quick reference

A study guide's **Quick reference** button opens a side panel of tabs for its definitions, equations
and nomenclature, which stay open while you scroll. **Expand** makes it a larger pop-up, which
collapses back to the side panel.

## Sources

Written claims link to a source, and each link opens that page, or the right page of your own
PDF, with the quote listed so you can find the passage. When something is saved, the server opens each cited page and looks for its quote, so a
made-up link or quote is refused. A site that blocks this check is kept and labeled "not
auto-checked". A claim with no source has to be labeled as Claude's own explanation. This is the one
time the server goes online, and only to the pages cited. Copies of your own files go in
`sources/`. Live replies from Claude, such as Explain why, have no sources.

## Skipping

**Skip this part** shows that step's answer, since later steps build on it, and takes it
out of the problem's count. **Skip this problem** takes a problem out of your progress and
exam mode. Both can be undone, keep your work, and leave the error log alone.

## Concept questions

New generated sets start with a few short written questions on when and why methods
apply. They're the one thing here SymPy can't verify: Claude writes the reference
answers and judges your replies by the mathematics, and the page says so.

## Courses (experimental)

A course teaches a topic you're new to, in any subject. **Courses** on the home page lists
them, and the **New** form starts one from a goal of a sentence or more. Claude researches
the topic with checked sources, agrees a teaching style, the kinds of checks, a mastery bar
and pacing with you in the terminal, and saves a syllabus of chapters and sections with
prerequisites. Then **Generate** makes one section at a time: a lesson, which is an ordinary
study guide, and a check, which is an ordinary set.

A section opens only once its prerequisites are mastered or tested out. The server decides
that from your results, never from what Claude says. Multiple choice and numeric items are
graded exactly, with decimals allowed and the unit always stated. Short answers are judged
by Claude and labeled that way. Course misses are kept out of the worksheet error log, so
they don't change how ODE problems are weighted. Deleting a course keeps its lessons and
checks. It's experimental, so the data format may still change.

## Tests

    python=$(nix build --no-link --print-out-paths .#default)/bin/python3
    PYTHONDONTWRITEBYTECODE=1 $python -m pytest -p no:cacheprovider tests -q

    # Browser tests, on either engine:
    python=$(nix build --no-link --print-out-paths .#browserTests)/bin/python3
    export PLAYWRIGHT_BROWSERS_PATH=$(nix build --no-link --print-out-paths .#playwrightBrowsers)
    export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1
    WORKSHEET_BROWSER=firefox PYTHONDONTWRITEBYTECODE=1 $python -m pytest -p no:cacheprovider tests/test_browser.py -q

    # The mod's own hooks:
    claude plugin test .

Set `MATH_WORKSHEET_DATA` to another folder to run against scratch data.

Vendored versions and checksums are in `web/vendor/VERSIONS.txt`.

## If the page is blank

Only the slider and the two collapsible panels show, with "Loading..." under the repertoire:
uBlock Origin is blocking JavaScript on 127.0.0.1. Turn off its JavaScript blocking switch for
this page and reload. Errors the page hits are shown at the top and written to `page.log` in the
data folder.

## License

MIT, see `LICENSE`. MathLive and KaTeX, in `web/vendor/`, are under their own licenses, included beside them.
