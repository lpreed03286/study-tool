---
name: make-course
description: Build and run an experimental self-paced course on any topic the student is new to, one section at a time, with sections that unlock as earlier ones are mastered. Use when the student asks to start a course or learn a topic from scratch, or when a request from the course page names a course.
---

# Running a course (experimental)

A course is a syllabus you build with the student, then teach one section at a time. Each section has a lesson, which is an ordinary study guide, and a check, which is an ordinary worksheet set. The server decides what's unlocked and what's mastered from the student's results, so you never set either. Courses are experimental, so say so the first time you report on one.

## 1. Tools

The tools are `mcp__math-worksheet__course_*`, plus `create_doc` and `create_set`. Load them with ToolSearch if they aren't listed. Start every step after intake with `course_overview` for the course. It's short and costs little, so read it instead of keeping the course in your head. Never read the course files directly.

For anything shared with other documents, follow the other skills instead of repeating them here. The make-doc skill covers lessons, blocks and citing sources, and the make-set skill covers math problems and the repertoire check.

## 2. Intake

The request gives a goal, and maybe what it's for (`intention`: `class`, `project`, `work`, `curiosity` or `other`, with `intention_details`), what they want to be able to do by the end (`end_goal`), a background, time per week, material they have, and a library class. A single sentence is enough to start.

If it's too thin to research, such as "physics" with nothing else, ask at most 3 short questions in the terminal. Ask what it's for, what they want to be able to do by the end, and what they already know. Otherwise go ahead.

Call `course_create` with what they gave, a short `title`, and `request_id` if the request names one.

The intention and end goal shape everything after this:

- **A class or exam:** match the class's scope and notation, use their library first, and weight checks toward the kinds of problems the exam will have.
- **A project or work:** start from what the project needs and leave out theory it doesn't use. Prefer applied numeric items.
- **Curiosity:** favour intuition, history and examples over drilling, with lighter checks.
- **End goal:** make it the last chapter's objectives, then plan backward: each earlier section should be something the end goal needs.

## 3. Research

The student's intake is usually thin, so this step is where the course gets its shape.

- If they named a library class, start there, as make-doc step 1 describes. Their own material comes first.
- Then use WebSearch and WebFetch for how the topic is usually taught: university course pages, open textbooks such as OpenStax, and standards bodies. Prefer two or three solid sources over many thin ones.
- Every source has to be a page you opened in this session, with a quote copied word for word, as make-doc step 6 says. Never cite a URL from memory.

Call `course_save_research` with a summary of a few sentences and the sources. It's all or nothing: if a source fails, `failed` says which, so fix that one and call again.

Then propose a draft syllabus in the terminal:

- 4 to 10 chapters, unless the topic clearly needs more.
- 2 to 6 sections in each chapter.
- 1 to 4 objectives for each section, each something the student can show they can do, such as "Compute an orbital period from the semi-major axis".
- Prerequisites, using section ids. Keep them minimal, since an extra prerequisite only delays the student.

Base it on the research, and say where it follows a source and where it's your own judgement. Show how the syllabus leads to their end goal, if they gave one.

## 4. Planning

Agree on four things with the student in the terminal, then save them.

- **Presentation:** offer 2 or 3 concrete options and recommend one that fits their intake. For example: concise study guide with worked examples first, or analogy-heavy with plots and diagrams, or derivation-heavy for someone with a strong math background. Settle on `examples` (`worked_first`, `after_theory` or `none`) and whether to use `figures`.
- **Assessment:** which item kinds suit which kind of section. A definitions section suits `multiple_choice` and `short_answer`, and a calculation section suits `numeric`, or `math` where a repertoire method genuinely fits. Set `default_kinds`, and plan to set `assessment_kinds` on any section that needs different ones.
- **Mastery bar:** the share of items that must be right (`threshold`, 0.8 unless they want otherwise), and how many may use a hint (`max_hints_ratio`, 0.5 unless they want otherwise).
- **Pacing:** in their words, such as "one section every few days".

Call `course_save_plan`. Then call `course_save_syllabus` with the agreed chapters. Use ids like `c1` for chapters and `c1-s1` for sections. Ids are permanent, so choose them once.

Offer a placement quiz if the intake suggests they already know the early material. See step 6.

## 5. One section

Only one section per request, unless the student asks for more.

1. Call `course_overview`, then `course_section` for the section. If its status is `locked`, stop and tell the student which prerequisites are left. The server refuses anyway.
2. Read `prereq_performance` and `last_attempt`. If an earlier section was passed with missed objectives, revisit those briefly in this lesson.
3. **The lesson:** write a study guide by the make-doc rules, in the plan's presentation style, covering every objective. Call `create_doc` with `kind: "study_guide"`, `course`, `section` and `course_role: "lesson"`. Add `regenerate: true` only if the student asked for a new lesson.
4. **The check:** call `create_set` with `course`, `section`, `course_role: "check"`, a `title`, `textbook: {"name": "<course title>"}` and the problems below. Pass `request_id` here if the request names one, since this is the last thing you make. Don't add a concept-check problem, because the check is already that.
5. Report in 2 or 3 lines: what the section covers, the item count by kind, how many are graded exactly against how many are judged by Claude, and how many sources were confirmed. Then call `open_worksheet`, unless the request came from the course page.

### Check items

Write 5 to 10 items that cover every objective, using the section's assessment kinds from `course_section`. Put them in one problem with the `course_items` method. Its `section` is the course section id:

```json
{"statement": "\\text{Check: Kepler's laws}", "method": "course_items", "section": "c1-s2", "topic": "Kepler's laws",
 "items": [
   {"kind": "multiple_choice", "prompt": "Which law says each orbit is an ellipse?", "objective": "State Kepler's three laws",
    "options": ["The first", "The second", "The third"], "correct": 0,
    "nudge": "Which one is about shape?", "method_hint": "The first law describes the orbit's shape.", "sources": [...]},
   {"kind": "numeric", "prompt": "A planet orbits at 4 AU. What is its period?", "objective": "Use the third law",
    "answer": "8", "unit": "years", "tolerance": {"relative": 0.01},
    "nudge": "Square the period, cube the distance.", "method_hint": "$T^2 = a^3$ in years and AU.", "own_words": true},
   {"kind": "short_answer", "prompt": "Why does a planet move fastest near the Sun?", "objective": "Explain the second law",
    "answer": "Equal areas in equal times: near the Sun the radius is short, so it must sweep faster.",
    "nudge": "Think about the area swept.", "method_hint": "Kepler's second law.", "sources": [...]}]}
```

- **Every item:** `kind`, `prompt`, `objective` (copy the section's objective text exactly, since results are grouped by it), `nudge` (doesn't give away the answer), `method_hint` (names the idea to use), and `sources` or `own_words: true`, cited as in make-doc step 6.
- **`multiple_choice`:** 2 to 8 `options` and `correct`, the index or a list of indices of the right ones, counting from 0. Make the wrong options plausible mistakes, not jokes. If more than one is right, the student is told to pick all that apply.
- **`numeric`:** `answer` as a number (decimals are fine), a `tolerance` of either `{"absolute": ...}` or `{"relative": ...}`, and a `unit` whenever the quantity has one. The student enters only the number, and the unit is shown beside the box. Say in the prompt what precision is expected, and choose a tolerance that allows reasonable rounding but not a wrong method.
- **`short_answer`:** a reference `answer`. Claude judges the student's reply by its substance, and the page says so. Use these for "why" and "explain", not for things an exact item can check.

Work out every numeric answer and every correct option carefully. The server only checks that each key passes its own check and that a wrong answer fails, not that your key is true.

**`math` items:** only when a repertoire method genuinely teaches the objective, as the make-set skill decides. Add them as extra problems in the same `create_set` call, with that method, the course section id as `section`, and `textbook: {"name": "<course title>"}`. They need the repertoire check first: call `check_coverage` with that textbook and section id, then `record_coverage`. Never force a method onto an objective it doesn't fit.

## 6. Retries, placement and changes

- **Retry check:** call `course_section`, read `last_attempt.missed_objectives`, and build a new check with fresh items, about two thirds on the missed objectives. Call `create_set` with `course_role: "check"`. The new set becomes the section's check, and earlier attempts are kept.
- **Placement quiz:** choose early sections with `course_placement`. Every prerequisite of a chosen section has to be chosen too. Then call `create_set` with `course_role: "placement"` and one `course_items` problem per section, each with its own section id as `section`. Placement counts only when taken in exam mode, so tell the student to use Exam mode for it. Sections that pass are marked tested out.
- **Syllabus edits:** call `course_save_syllabus` with a `change` note. Keep the id of every section that survives, even when it's renamed or moved, so its progress stays. Never reuse a removed section's id. Use `partial: true` to replace only the chapters you pass.
- **Plan changes:** call `course_save_plan` again with the whole plan and a `change` note. It applies to sections not generated yet. Regenerate an existing lesson only if the student asks.
- Pass `request_id` to the save call when the request names one.

## Never

Never generate content for a locked section, or more than one section per request unless the student asks. Never try to set mastery or status, since only results do that. Never commit or push with git for the student. If they want to, give them the commands.
