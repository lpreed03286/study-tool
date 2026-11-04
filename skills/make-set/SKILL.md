---
name: make-set
description: Make an ODE worksheet set from a textbook, edition and sections, or from problems the student typed. Use whenever the student asks for a worksheet, practice problems or a problem set, or asks to add a method to the worksheet repertoire.
---

# Making a worksheet set

The tools are `mcp__math-worksheet__*`. Load them with ToolSearch if they aren't listed. Follow these steps in order; the server refuses a set that skips the repertoire check.

## 1. Get the source

If the student has a class library for this, start with `library_overview`, then use `library_search` and `library_read` for only the pages you need, rather than opening files. The make-doc skill's first step describes this.

You need the book's name and edition, plus section numbers or topics. If the student attached a PDF or photos, read the sections from them and treat those pages as the source. Without them, use the book's standard content from general knowledge, and say that it is from memory.

## 2. Check the repertoire for every new topic

Call `check_coverage` with the book and sections. It returns what is already recorded for each section and every method in the repertoire, with what each method handles and its limits.

For each section with no record, decide from what that section actually teaches:

- Which technique or techniques does this section teach, and what kinds of right sides, coefficient types and initial conditions do its problems have?
- Does a repertoire method solve those problems **the way the book does, in steps a student would actually write**? A method that would produce the right answer by a different route is not good enough. Example: a section on reduction of order isn't covered by `constant_coefficient_homogeneous`, even if the answers come out the same.
- Does a listed limit exclude part of the section? Example: `euler_cauchy` handles only homogeneous equations, so a section with nonhomogeneous Cauchy-Euler problems has a gap.

Then close the gaps:

- **Method missing or unsuitable:** write a new method (step 3) before making the set.
- **Method fits:** call `record_coverage` with the methods and a one-sentence rationale saying why they fit this section.

Tell the student which methods you added or matched, and why, in a line or two. A section you recorded before doesn't need checking again.

## 3. Adding a method

Write one Python file directly in the folder `check_coverage` returned as `repertoire_folder`, named `<method_id>.py`. Model it on the methods in the file it returned as `method_examples`, which you should read first. It must define `METHOD`:

```python
import sympy as sp
from grade import dump
from methods import Method, ODE, NotApplicable, make_step, expr_check, general_step, ivp_steps, plan, dy, tex

def build(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    # Raise NotApplicable("why") for anything this method can't do.
    # Compute every key with SymPy. Never type in an answer.
    steps = [make_step("id", "step_type", "Label", "Prompt with $math$", "answer latex",
                       check_spec, "nudge hint", "method hint", "optional note")]
    return plan("<method_id>", ode, steps, y_general, y_ivp)

METHOD = Method(
    "<method_id>", "Display name", "What the steps are.",
    "what it handles", "what it doesn't handle",
    ["step_type", ...], build,
    [{"statement": "..."}, {"statement": "...", "ic": ["y(0)=1"]}],  # at least two
)
```

`check_spec` is one of the kinds in `grade.py`, beside `method_examples`. Use the strongest that fits: `general_solution`, `particular_of`, `integrating_factor`, `antiderivative`, `trial_form`, `linear_system` and `implicit_solution` test what makes the answer correct. `expr`, `equation`, `assignments` and `roots` compare to a key.

Name each step type for the skill it tests, reusing an existing name where the skill is the same, because the error log groups by it. Use the notation in the book. Give each step a nudge that doesn't reveal the method and a method hint that does.

Then call `add_method` with the file's name. It runs every sample through full verification. A method that fails does not join the repertoire; fix the file and try again. Editing an accepted file takes it out of the repertoire until it passes `add_method` again.

A method whose answers can't be verified by substitution into an ODE needs `plan(...)` given an `implicit`/`slope` pair, or a checking route added to `verify.py`. Never weaken a check to get something through. If it truly can't be verified, tell the student instead of adding it.

## 4. Pick the mix

For generated problems, call `study_plan` with the methods and the problem count. Follow its `allocation`: it weights the set toward the student's weak spots. For a weak `step_type`, choose problems that make that step substantial. Tell the student which weak spots you weighted toward.

## 5. Set the difficulty

Pass `difficulty` to `create_set`. Use `textbook` unless the student asked for something else. Harder means more thinking, not uglier arithmetic.

- `textbook`: comparable to the book's worked examples and standard exercises. This is what "off" means on the form.
- `easier`: small whole numbers, simple initial conditions, the plain case of the method and fewer steps.
- `harder`: less friendly numbers and the special cases the section teaches, such as repeated roots or the Modification Rule.
- `challenge`: several ideas in one problem, such as modeling a system, solving it and interpreting the plots.

Stay within what each method's listed limits allow, and tell the student if a level can't be met for a topic. Typed problems stay word for word at any level. Without the student's pages, "comparable to the book" comes from general knowledge of it, so say that it's approximate.

## 6. Start with concept questions

Unless the student asked not to, or every problem in the set is one they typed, make Problem 1 a few short questions on the set's topics. It doesn't count toward the number of problems they asked for, and don't pass it to `study_plan`.

```json
{"statement": "\\text{Concept check}", "method": "concept_questions", "section": "2.7", "topic": "Nonhomogeneous linear ODEs",
 "questions": [{"question": "From a general solution of the homogeneous ODE, which methods can get you a general solution of the nonhomogeneous one?",
                "answer": "Undetermined coefficients or variation of parameters give $y_p$, and then $y=y_h+y_p$.",
                "nudge": "What is missing once you have $y_h$?", "method_hint": "Name the methods that produce $y_p$."}]}
```

- Write 2 or 3 questions, each answerable in a sentence or two. Ask when a method applies, why it works, how two methods relate, or what a result means in a physical setting. A short word problem can set up the setting, but nothing should need computing.
- Match the set's difficulty, and don't give away a later problem's answer.
- SymPy can't check these. You write the reference answers, so state only what you're sure of, in the book's terms, and never cite a page or example number from memory. The page tells the student that these answers aren't SymPy-checked, and Claude judges their replies by the mathematics.

This problem needs no `check_coverage` or `record_coverage` entry. Each question also needs `sources` or `"own_words": true`, as step 9 describes.

## 7. Write the problems

- **Statements** in LaTeX. First-order and higher: `y''+3y'+2y=3e^{2x}`. Exact: `(2xy+1)dx+(x^2+3)dy=0`. Initial conditions go in `ic`: `["y(0)=1", "y'(0)=0"]`.
- **Exact numbers**: fractions, never decimals.
- **Systems** for `linear_system`: `y_1'=-4y_1+y_2,\\quad y_2'=y_1-4y_2`, with `ic` like `["y_1(0)=2", "y_2(0)=0"]`. Give an initial condition when you want the time plot too. `context` is an optional sentence shown above a problem, for a model such as mixing tanks.
- Each problem has `statement`, `method`, `section`, `topic`. Keep the student's typed problems word for word, and set `origin: "typed"` on the request if all of them are typed.
- Don't write answers. The server computes and verifies every key.

## 8. Write the book notes

So hints and explanations teach the method the way the book does, pass `book_methods`, keyed by method id:

- `summary`: how the book presents the method, in its order of steps and its names for things (for example, Kreyszig's Basic Rule, Modification Rule and Sum Rule).
- `notation`: the book's symbols, used in the prompts: `p`, `r`, `mu` (`\\mu` or `F`), `char` (`r` or `\\lambda`), `constant` (`C` or `c`), `aux_name` (`auxiliary equation` or `indicial equation`).
- `step_notes`: optional, step type to one sentence. It is added to that step's method hint.
- `source`: `"pdf"` only if you read the student's pages. Otherwise `"memory"`.

From memory, describe only what you're confident of. Never invent page numbers, example numbers or quotations.

## 9. Cite sources

Answer keys are verified by SymPy, but your written claims aren't, so the student should be able to check those. Each `book_methods` entry and each concept question needs `sources`, or `"own_words": true` if it's your own explanation:

```json
"sources": [{"title": "Euler-Cauchy equation, Paul's Online Notes", "url": "https://...", "quote": "a word-for-word passage from that page"}]
```

For a file in the library, use `{"title": "...", "library": "file id", "page": 12, "quote": "..."}`. For other files of theirs, use `"file": "/path/to/pages.pdf"` instead of `library`.

Find sources with WebSearch and read them with WebFetch before you cite them. **Never cite a URL from memory, or a page you haven't opened in this session.** The `quote` has to be copied word for word: prose rather than an equation, 4 to 60 words, and the passage that supports the claim. A source supports the method, not the book's own presentation of it, so `source: "memory"` still applies to what you say about the book.

When you save, the server opens every source and looks for its quote. A missing page or a quote that isn't there is refused and reported in `failed`, so find a real source. Never reword something just to get past the check.

## 10. Create and report

Call `create_set`. It's all or nothing: if any problem fails verification, nothing is saved and `failed` says why. Fix or replace those problems and call it again. A request from the worksheet page's New form names a `request_id`: pass it to `create_set`, so the page swaps its "making this" row for the finished set.

Then call `open_worksheet`, unless the request came from that form, and say which sources weren't auto-checked. Tell the student, for each problem, its topic, its method and its section, so they can confirm it matches their book. Say plainly that section numbers and topics are from memory if there was no PDF.

## Never

Never commit or push with git for the student. If they want to, give them the commands.
