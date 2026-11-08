"use strict";

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || "";
const ML = window.MathfieldElement;
ML.fontsDirectory = "/vendor/mathlive/fonts";
ML.soundsDirectory = null;
if (window.mathVirtualKeyboard) window.mathVirtualKeyboard.container = null;

const DOC_ID = params.get("doc");
const LIBRARY_PAGE = params.has("library");
const HELP_PAGE = params.has("help");
const COURSE_ID = params.get("course");
const COURSES_PAGE = params.has("courses");
const state = { view: null, setId: params.get("set") || null, focused: null, timerHandle: null, draftTimers: new Map() };

const $ = (id) => document.getElementById(id);
const CONCEPT_METHOD = "concept_questions";

const CHEAT = [
  ["\\frac{a}{b}", "\\frac{a}{b}", "fraction"],
  ["x^{2}", "x^{2}", "power"],
  ["c_{1}", "c_{1}", "subscript"],
  ["e^{rt}", "e^{rt}", "exponential"],
  ["\\sin x", "\\sin x", "sine"],
  ["\\cos x", "\\cos x", "cosine"],
  ["\\tan x", "\\tan x", "tangent"],
  ["\\ln x", "\\ln x", "natural log"],
  ["y'", "y'", "first derivative"],
  ["y''", "y''", "second derivative"],
  ["\\int f\\,dx", "\\int f\\,dx", "integral"],
  ["\\sqrt{x}", "\\sqrt{x}", "square root"],
  ["|x|", "|x|", "absolute value"],
  ["\\pm", "\\pm", "plus or minus"],
  ["\\Delta", "\\Delta", "Delta"],
  ["\\begin{pmatrix}a\\\\b\\end{pmatrix}", "\\begin{pmatrix}#?\\\\#?\\end{pmatrix}", "column vector"],
  ["\\alpha", "\\alpha", "alpha"],
  ["\\beta", "\\beta", "beta"],
  ["\\lambda", "\\lambda", "lambda"],
  ["\\mu", "\\mu", "mu"],
  ["\\omega", "\\omega", "omega"],
  ["\\theta", "\\theta", "theta"],
  ["\\pi", "\\pi", "pi"],
];

// --- Talking to the server ---------------------------------------------------

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "X-Worksheet-Token": TOKEN, "Content-Type": "application/json" },
    });
  } catch {
    throw new Error("The worksheet server isn't running. Start it from Claude Code with /worksheet. Your progress is saved.");
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Something went wrong.");
  return body;
}

const act = (body) => api("/api/action", {
  method: "POST",
  body: JSON.stringify({ set: state.setId, problem: state.view && state.view.active, ...body }),
});

async function run(body, button) {
  if (button) button.disabled = true;
  try {
    const reply = await act(body);
    if (reply.view) render(reply.view);
    return reply;
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
  return null;
}

async function waitForAsk(askId) {
  while (true) {
    await new Promise((done) => setTimeout(done, 1000));
    const { state: askState } = await api(`/api/ask?id=${askId}`);
    if (askState !== "pending") return;
  }
}

async function runAsk(body, button, label) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    const reply = await act(body);
    if (reply.view) render(reply.view);
    if (reply.ask) {
      await waitForAsk(reply.ask);
      await refresh();
    }
  } catch (error) {
    toast(error.message);
    button.disabled = false;
    button.textContent = original;
  }
}

async function refresh(problem) {
  const query = new URLSearchParams({ set: state.setId });
  if (problem) query.set("problem", problem);
  render(await api(`/api/view?${query}`));
}

// --- Rendering math ----------------------------------------------------------

function renderTex(tex, display = false) {
  try {
    return window.katex.renderToString(tex, { throwOnError: false, displayMode: display, strict: false });
  } catch {
    return "";
  }
}

// Text with $...$ math. Escapes the text around the math.
function richText(text) {
  const pieces = String(text).split(/(\$[^$]+\$)/g);
  return pieces.map((piece) => {
    if (piece.length > 2 && piece.startsWith("$") && piece.endsWith("$")) return renderTex(piece.slice(1, -1));
    return escapeHtml(piece);
  }).join("");
}

function escapeHtml(text) {
  const node = document.createElement("span");
  node.textContent = text;
  return node.innerHTML;
}

function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "html") node.innerHTML = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

// --- The page ----------------------------------------------------------------

function render(view) {
  const focusInfo = rememberFocus();
  state.view = view;
  renderHeader(view);
  renderMain(view);
  restoreFocus(focusInfo);
  const waiting = view.problem && (view.problem.steps || []).find((step) => step.explaining);
  if (waiting) watchExplanation(waiting.explaining);
}

function watchExplanation(askId) {
  if (state.watching === askId) return;
  state.watching = askId;
  waitForAsk(askId)
    .then(showReadyExplanation)
    .catch((error) => toast(error.message))
    .finally(() => {
      if (state.watching === askId) state.watching = null;
    });
}

// Adds the explanation in place. A full redraw would move the cursor and clear what they're typing.
async function showReadyExplanation() {
  const view = await api(`/api/view?${new URLSearchParams({ set: state.setId })}`);
  const card = document.querySelector("article[data-open-step]");
  const step = card && view.problem && (view.problem.steps || []).find((candidate) => candidate.id === card.dataset.openStep);
  if (!step || !state.view.problem || view.problem.id !== state.view.problem.id) return;
  const parts = card.querySelector("[data-explanation]") ? [] : explainParts(step);
  const waiting = card.querySelector("[data-explaining]");
  if (waiting) waiting.replaceWith(...parts);
  else if (card.querySelector(".feedback")) card.querySelector(".feedback").after(...parts);
  const button = card.querySelector("[data-action='explain']");
  const next = explainButton(step);
  if (button && next) button.replaceWith(next);
}

function rememberFocus() {
  const active = document.activeElement;
  if (active && active.tagName === "MATH-FIELD") return active.dataset.stepKey;
  return null;
}

function restoreFocus(key) {
  const open = document.querySelector("math-field[data-open='true']");
  const same = key && document.querySelector(`math-field[data-step-key="${CSS.escape(key)}"]`);
  const target = same || open;
  if (target) requestAnimationFrame(() => target.focus());
}

function renderHeader(view) {
  const picker = $("set-picker");
  picker.replaceChildren(...view.sets.map((s) => el("option", { value: s.id, text: s.title, selected: s.id === view.set.id })));
  $("book").textContent = [bookName(view.set.textbook), DIFFICULTY_NAMES[view.set.difficulty]].filter(Boolean).join(" \u00b7 ");

  const exam = view.exam;
  const examRunning = exam && !exam.submitted;
  $("exam-toggle").checked = Boolean(exam);
  $("fade-row").hidden = Boolean(exam);

  const showResults = exam && exam.submitted;
  $("tabs").hidden = Boolean(showResults);
  $("tabs").replaceChildren(...view.tabs.map((tab) => {
    const selected = tab.id === view.active;
    const count = examRunning ? "" : tab.unverified ? "not verified" : tab.skipped ? "skipped" : `${tab.done}/${tab.total}`;
    return el("button", {
      class: `tab${tab.complete && !examRunning ? " complete" : ""}${tab.skipped ? " skipped" : ""}`, role: "tab",
      "aria-selected": selected ? "true" : "false", "data-tab": tab.id,
      onclick: () => selectTab(tab.id),
    }, document.createTextNode(`Problem ${tab.number}`), el("span", { class: "count", text: count }));
  }));

  const problem = view.problem;
  if (problem && !exam && !problem.unverified && !problem.skipped) {
    $("fade").value = problem.fade;
    $("fade-value").textContent = `${problem.fade}%`;
    $("fade-count").textContent = `${problem.prefill_count} of ${problem.total} steps start filled in`;
  }
  manageTimer(exam);
}

async function selectTab(id) {
  await saveVisibleDrafts();
  run({ action: "tab", problem: id });
}

function renderMain(view) {
  const main = $("main");
  if (view.exam && view.exam.submitted) {
    main.replaceChildren(examResults(view.exam));
    return;
  }
  const problem = view.problem;
  if (!problem) {
    main.replaceChildren(el("p", { class: "muted", text: "This set has no problems yet." }));
    return;
  }
  const parts = [problemCard(problem, Boolean(view.exam))];
  if (problem.skipped) {
    const back = el("button", { type: "button", class: "primary", text: "Bring it back", "data-action": "unskip-problem" });
    back.addEventListener("click", () => run({ action: "unskip_problem" }, back));
    parts.push(el("div", { class: "note-box", "data-skipped-problem": "true" },
      el("p", { text: "You skipped this problem. It doesn't count toward your progress or appear in exam mode, and any work on it is saved." }),
      el("div", { class: "actions" }, back)));
    main.replaceChildren(...parts);
    return;
  }
  if (problem.unverified) {
    parts.push(el("div", { class: "feedback incorrect", text: "This problem's saved answer key doesn't match what was verified, so it won't be shown or graded. Ask Claude to regenerate it." }));
    main.replaceChildren(...parts);
    return;
  }
  for (const step of problem.steps) parts.push(stepCard(step, problem));
  if (view.exam) {
    parts.push(el("div", { class: "actions" },
      el("button", { class: "primary", text: "Submit exam for grading", onclick: submitExam })));
  } else {
    const remaining = problem.total - problem.steps.length;
    if (remaining > 0) parts.push(el("p", { class: "remaining", text: `${remaining} more step${remaining === 1 ? "" : "s"} after this` }));
    if (problem.complete) parts.push(solutionCard(problem));
    // Adding a typed ODE problem only makes sense for a worksheet, not a course check.
    if (!view.set.course) parts.push(addProblemCard());
  }
  main.replaceChildren(...parts);
}

function problemCard(problem, inExam) {
  const conditions = (problem.ic || []).join(",\\quad ");
  const statement = problem.statement + (conditions ? `,\\quad ${conditions}` : "");
  const card = el("section", { class: "problem-card" },
    el("div", { class: "statement", html: renderTex(statement, true) }));
  const course = state.view && state.view.set.course;
  if (course) return courseProblemCard(card, problem, course, inExam);
  const facts = [problem.topic, problem.section && `Section ${problem.section}`].filter(Boolean).join(" · ");
  if (problem.context) card.append(el("p", { class: "context", html: richText(problem.context) }));
  if (facts || problem.method) {
    const meta = el("div", { class: "meta", text: facts });
    meta.append(el("span", { class: "chip", text: methodName(problem.method) }));
    if (problem.origin === "generated") meta.append(el("span", { class: "chip warn", text: "generated: confirm the topic matches your book" }));
    card.append(meta);
  }
  if (!inExam && problem.book && problem.method !== CONCEPT_METHOD) card.append(bookPanel(problem));
  if (!inExam && !problem.skipped && !problem.unverified) {
    const skip = el("button", { type: "button", class: "skip-problem", text: "Skip this problem", "data-action": "skip-problem" });
    skip.addEventListener("click", () => run({ action: "skip_problem" }, skip));
    card.append(skip);
  }
  return card;
}

// A course set says which course and section it's for, instead of a textbook.
function courseProblemCard(card, problem, course, inExam) {
  const where = course.role === "placement" ? "Placement quiz" : course.section_title || course.section;
  const meta = el("div", { class: "meta" },
    el("a", { href: courseAddress(course.id), "data-course-link": course.id, text: course.title || "Course" }),
    document.createTextNode(where ? ` · ${where}` : ""),
    el("span", { class: "chip experimental", text: "Experimental course" }));
  card.append(meta);
  if (course.role === "placement" && !inExam) {
    card.append(el("p", { class: "muted", text: "Placement only counts in Exam mode, so turn on Exam mode to take it." }));
  }
  if (!inExam && !problem.skipped && !problem.unverified) {
    const skip = el("button", { type: "button", class: "skip-problem", text: "Skip this problem", "data-action": "skip-problem" });
    skip.addEventListener("click", () => run({ action: "skip_problem" }, skip));
    card.append(skip);
  }
  return card;
}

function methodName(id) {
  return String(id || "").replaceAll("_", " ");
}

function bookPanel(problem) {
  const book = problem.book;
  const title = book.book ? `How ${book.book} does this` : "How this method works";
  const panel = el("details", { class: "own-method" }, el("summary", { text: title }));
  if (book.summary) {
    panel.append(el("div", { class: "note-box" },
      el("p", { html: richText(book.summary) }),
      el("p", { class: "muted", text: book.source === "pdf" ? "From the pages you provided." : "From general knowledge of the book, not its pages. Check it against your copy." }),
      sourceRow(book)));
  }
  if (problem.walkthrough) {
    panel.append(el("div", { class: "note-box" }, ...paragraphs(problem.walkthrough)));
  } else {
    const button = el("button", { text: "Ask Claude to walk through the method", style: "margin-top:8px" });
    button.addEventListener("click", () => runAsk({ action: "walkthrough" }, button, "Asking Claude..."));
    panel.append(button);
    if (problem.walkthrough_error) panel.append(el("p", { class: "muted", text: problem.walkthrough_error }));
  }
  return panel;
}

function paragraphs(text) {
  return String(text).split(/\n\s*\n/).map((part) => el("p", { html: richText(part) }));
}

function renderAnswer(answer, format) {
  return format === "text" ? el("p", { html: richText(answer) }) : el("div", { html: renderTex(answer, true) });
}

function figureList(step) {
  return (step.figures || []).map(renderFigure);
}

function stepCard(step, problem) {
  if (step.status === "prefilled") {
    return el("article", { class: "step prefilled" },
      stepTitle(step, "Pre-filled"),
      el("div", { class: "locked" }, renderAnswer(step.answer, step.answer_format)),
      ...figureList(step), sourceRow(step));
  }
  if (step.status === "skipped") {
    const undo = el("button", { type: "button", text: "Undo skip", "data-action": "unskip" });
    undo.addEventListener("click", () => run({ action: "unskip_step", step: step.id }, undo));
    return el("article", { class: "step prefilled", "data-skipped-step": step.id },
      stepTitle(step, "Skipped"),
      el("div", { class: "locked" }, renderAnswer(step.answer, step.answer_format)),
      ...figureList(step), sourceRow(step),
      el("div", { class: "actions" }, undo));
  }
  if (step.status === "correct") return correctCard(step);
  if (step.status === "exam") return examCard(step, problem);
  return openCard(step, problem);
}

function stepTitle(step, word) {
  const title = el("h3", { html: richText(step.label) });
  if (word) title.append(el("span", { class: "status-word", text: ` ${word}` }));
  return title;
}

function correctCard(step) {
  const card = el("article", { class: "step correct" }, stepTitle(step, "Correct"));
  if (step.input === "multiple_choice" || step.input === "numeric") {
    card.append(el("div", { class: "locked course-answer", html: richText(step.readback || step.draft) }));
  } else if (step.input !== "discussion") card.append(el("div", { class: "locked", html: renderTex(step.draft || step.readback, true) }));
  card.append(...figureList(step));
  const chips = el("div", { class: "meta" });
  if (step.hint_level) chips.append(el("span", { class: "chip", text: `hints used: level ${step.hint_level}` }));
  if (step.confidence === "numeric") chips.append(el("span", { class: "chip", text: "confirmed numerically, no symbolic proof" }));
  if (step.confidence === "string") chips.append(el("span", { class: "chip warn", text: "matched as text, couldn't parse as math" }));
  if (step.confidence && step.confidence.startsWith("own method")) chips.append(el("span", { class: "chip", text: step.confidence }));
  if (chips.childNodes.length) card.append(chips);
  if (step.own && step.own.reason) card.append(el("div", { class: "note-box", html: richText(step.own.reason) }));
  if (step.own && step.own.work) card.append(el("div", { class: "note-box" }, el("h4", { text: step.input === "discussion" ? "What you wrote" : "Your method" }), el("p", { html: richText(step.own.work) })));
  const citation = sourceRow(step);
  if (citation) card.append(citation);
  return card;
}

// MathLive refuses some settings until the element is in the page.
function newField(label, initial) {
  const field = new ML();
  field.setAttribute("aria-label", label);
  field.textContent = initial || "";
  field.mathVirtualKeyboardPolicy = "manual";
  // By default a digit ends an exponent, so e^2x would silently become e^2 * x.
  field.smartSuperscript = false;
  field.smartMode = false;
  field.mathModeSpace = "\\,";
  field.addEventListener("mount", () => {
    field.menuItems = [];
    // Plain \\int, not the default with two empty limit boxes.
    field.inlineShortcuts = { ...field.inlineShortcuts, int: "\\int", pm: "\\pm" };
  });
  return field;
}

function mathField(step, initial, onChange) {
  const key = `${state.view.active}/${step.id}`;
  const field = newField(`Answer for ${step.label}`, initial);
  field.dataset.stepKey = key;
  field.dataset.step = step.id;
  const preview = el("div", { class: "preview", "data-preview": step.id, html: initial ? renderTex(initial, true) : "" });
  field.addEventListener("input", () => {
    preview.innerHTML = field.value ? renderTex(field.value, true) : "";
    if (onChange) onChange(field.value);
  });
  field.addEventListener("focus", () => { state.focused = field; });
  return { field, preview };
}

function queueDraft(stepId, latex) {
  clearTimeout(state.draftTimers.get(stepId));
  const problem = state.view.active;
  state.draftTimers.set(stepId, setTimeout(() => {
    act({ action: "draft", problem, step: stepId, latex }).catch(() => {});
  }, 500));
}

// Sends what's in the boxes right now, so nothing typed in the last moment is lost.
function saveVisibleDrafts() {
  const saves = [];
  for (const box of document.querySelectorAll("textarea[data-exam-discussion]")) {
    clearTimeout(state.draftTimers.get(`discussion-${box.dataset.examDiscussion}`));
    saves.push(act({ action: "exam_own", step: box.dataset.examDiscussion, work: box.value, result: "" }).catch(() => {}));
  }
  for (const box of document.querySelectorAll("input[data-numeric]")) {
    clearTimeout(state.draftTimers.get(box.dataset.numeric));
    saves.push(act({ action: "draft", step: box.dataset.numeric, latex: box.value }).catch(() => {}));
  }
  for (const field of document.querySelectorAll("math-field[data-step]")) {
    if ((field.dataset.open === "true" || field.dataset.exam === "true") && field.value) {
      clearTimeout(state.draftTimers.get(field.dataset.step));
      saves.push(act({ action: "draft", step: field.dataset.step, latex: field.value }).catch(() => {}));
    }
  }
  return Promise.all(saves);
}

function choiceInput(step, name, onPick) {
  const group = el("fieldset", { class: "choices", "data-choices": step.id });
  group.append(el("legend", { class: "muted", text: "Choose one" }));
  for (const option of step.options) {
    const input = el("input", { type: "radio", name, value: option, checked: option === step.draft });
    input.addEventListener("change", () => onPick(option));
    group.append(el("label", { class: "choice" }, input, document.createTextNode(option.replace(/^\\text\{|\}$/g, ""))));
  }
  return group;
}

// Course items: the server grades option indices, so options can hold math.
function pickedOptions(draft) {
  const match = /choices:([\d,]*)/.exec(draft || "");
  return match ? match[1].split(",").filter(Boolean).map(Number) : [];
}

function optionsInput(step, name, onChange) {
  const group = el("fieldset", { class: "choices", "data-choices": step.id });
  group.append(el("legend", { class: "muted", text: step.multi ? "Choose all that apply" : "Choose one" }));
  const chosen = new Set(pickedOptions(step.draft));
  step.options.forEach((option, index) => {
    const input = el("input", { type: step.multi ? "checkbox" : "radio", name, value: String(index), checked: chosen.has(index) });
    input.addEventListener("change", () => {
      const picked = [...group.querySelectorAll("input")].filter((box) => box.checked).map((box) => box.value);
      onChange(picked.length ? `choices:${picked.join(",")}` : "");
    });
    group.append(el("label", { class: "choice" }, input, el("span", { html: richText(option) })));
  });
  return group;
}

function numericInput(step, onChange) {
  const input = el("input", { type: "text", inputmode: "decimal", class: "numeric-input", "data-numeric": step.id,
    "aria-label": step.unit ? `Answer in ${step.unit}` : "Answer", autocomplete: "off" });
  input.value = step.draft || "";
  input.addEventListener("input", () => onChange(input.value));
  return { input, row: el("div", { class: "numeric-row" }, input, step.unit && el("span", { class: "unit", text: step.unit })) };
}

function multipleChoiceCard(step) {
  const card = el("article", { class: "step open", "data-open-step": step.id }, stepTitle(step));
  card.append(el("p", { class: "prompt", html: richText(step.prompt) }));
  let picked = step.draft;
  card.append(optionsInput(step, `choice-${step.id}`, (value) => { picked = value; queueDraft(step.id, value); }));
  const submit = el("button", { class: "primary", text: "Submit", "data-action": "submit" });
  submit.addEventListener("click", () => {
    if (!picked) return toast(step.multi ? "Choose at least one first." : "Choose one first.");
    run({ action: "submit", step: step.id, latex: picked }, submit);
    return null;
  });
  card.append(el("div", { class: "actions" }, submit, hintButton(step), skipButton(step), explainButton(step)));
  const feedback = feedbackBox(step);
  if (feedback) card.append(feedback);
  card.append(...explainParts(step), ...hintBoxes(step));
  return card;
}

function numericCard(step) {
  const card = el("article", { class: "step open", "data-open-step": step.id }, stepTitle(step));
  card.append(el("p", { class: "prompt", html: richText(step.prompt) }));
  const { input, row } = numericInput(step, (value) => queueDraft(step.id, value));
  card.append(row);
  const submit = el("button", { class: "primary", text: "Submit", "data-action": "submit" });
  const send = () => (input.value.trim() ? run({ action: "submit", step: step.id, latex: input.value }, submit) : toast("Enter a number first."));
  submit.addEventListener("click", send);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      send();
    }
  });
  card.append(el("div", { class: "actions" }, submit, hintButton(step), skipButton(step), explainButton(step)));
  const feedback = feedbackBox(step);
  if (feedback) card.append(feedback);
  card.append(...explainParts(step), ...hintBoxes(step));
  return card;
}

function hintBoxes(step) {
  return (step.hints || []).map((hint) => {
    const box = el("div", { class: "note-box", "data-hint": hint.level }, el("h4", { text: hint.name }));
    box.append(hint.math ? el("div", { html: renderTex(hint.math, true) }) : el("p", { html: richText(hint.text) }));
    return box;
  });
}

function skipButton(step) {
  const skip = el("button", { type: "button", text: "Skip this part", "data-action": "skip" });
  skip.addEventListener("click", () => run({ action: "skip_step", step: step.id }, skip));
  return skip;
}

function hintButton(step) {
  const level = step.hint_level || 0;
  if (level >= 3) return null;
  const hint = el("button", { text: ["Hint: nudge", "Hint: method", "Hint: answer"][level], "data-action": "hint" });
  hint.addEventListener("click", () => run({ action: "hint", step: step.id }, hint));
  return hint;
}

function feedbackBox(step) {
  if (!step.message || !step.feedback || step.feedback === "correct") return null;
  const box = el("div", { class: `feedback ${step.feedback}`, html: richText(step.message) });
  if (step.readback && step.input === "math") box.append(el("span", { class: "readback", html: `Read as: ${renderTex(step.readback)}` }));
  return box;
}

function explainParts(step) {
  const parts = (step.explanations || []).map((entry) => el("div", { class: "note-box", "data-explanation": "true" },
    el("h4", { text: entry.from === "sympy" ? "What SymPy found" : "Why this is wrong" }),
    ...paragraphs(entry.text)));
  if (step.explaining) {
    parts.push(el("div", { class: "note-box explaining", role: "status", "data-explaining": "true" },
      el("span", { class: "spinner", "aria-hidden": "true" }),
      el("span", { text: "Claude is working out why. It will appear here when it's ready, and you can keep working." })));
  }
  return parts;
}

function explainButton(step) {
  if (!step.can_explain) return null;
  const explain = el("button", { text: step.explaining ? "Explaining..." : "Explain why", "data-action": "explain" });
  explain.addEventListener("click", async () => {
    const reply = await run({ action: "explain", step: step.id }, explain);
    if (reply && reply.ask) watchExplanation(reply.ask);
  });
  if (step.explaining || (step.explanations && step.explanations.length)) explain.disabled = true;
  return explain;
}

function choiceCard(step) {
  const card = el("article", { class: "step open", "data-open-step": step.id }, stepTitle(step));
  card.append(el("p", { class: "prompt", html: richText(step.prompt) }));
  let picked = step.draft;
  card.append(choiceInput(step, `choice-${step.id}`, (option) => { picked = option; }));
  const submit = el("button", { class: "primary", text: "Submit", "data-action": "submit" });
  submit.addEventListener("click", () => {
    if (!picked) return toast("Choose one first.");
    run({ action: "submit", step: step.id, latex: picked }, submit);
    return null;
  });
  card.append(el("div", { class: "actions" }, submit, hintButton(step), skipButton(step), explainButton(step)));
  const feedback = feedbackBox(step);
  if (feedback) card.append(feedback);
  card.append(...explainParts(step), ...hintBoxes(step));
  return card;
}

function discussionCard(step) {
  const card = el("article", { class: "step open", "data-open-step": step.id }, stepTitle(step));
  card.append(...figureList(step));
  card.append(el("p", { class: "prompt", html: richText(step.prompt) }));
  const own = step.own;
  const work = el("textarea", { "data-discussion": step.id, placeholder: step.placeholder || "Write your discussion. Use $...$ for math." });
  work.value = (own && own.work) || "";
  const submit = el("button", { class: "primary", text: "Submit for Claude to read", "data-action": "own" });
  submit.addEventListener("click", () => runAsk({ action: "own", step: step.id, work: work.value, result: "" }, submit, "Claude is reading..."));
  card.append(work, el("div", { class: "actions" }, submit, hintButton(step), skipButton(step)));
  if (own && own.state === "invalid") card.append(el("div", { class: "feedback incorrect", html: richText(own.reason) }));
  if (own && own.state === "pending") card.append(el("p", { class: "muted", text: "Waiting on Claude." }));
  card.append(...hintBoxes(step));
  return card;
}

function openCard(step, problem) {
  if (step.input === "choice") return choiceCard(step);
  if (step.input === "multiple_choice") return multipleChoiceCard(step);
  if (step.input === "numeric") return numericCard(step);
  if (step.input === "discussion") return discussionCard(step);
  const card = el("article", { class: "step open", "data-open-step": step.id }, stepTitle(step));
  card.append(el("p", { class: "prompt", html: richText(step.prompt) }));
  card.append(...figureList(step));
  const { field, preview } = mathField(step, step.draft, (latex) => queueDraft(step.id, latex));
  field.dataset.open = "true";
  card.append(field, preview);

  const submit = el("button", { class: "primary", text: "Submit", "data-action": "submit" });
  const send = () => run({ action: "submit", step: step.id, latex: field.value }, submit);
  submit.addEventListener("click", send);
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  card.append(el("div", { class: "actions" }, submit, hintButton(step), skipButton(step), explainButton(step)));
  const feedback = feedbackBox(step);
  if (feedback) card.append(feedback);
  card.append(...explainParts(step), ...hintBoxes(step));
  card.append(ownMethodPanel(step, problem));
  return card;
}

function ownMethodPanel(step, problem, inExam = false) {
  const own = step.own;
  const panel = el("details", { class: "own-method", open: Boolean(own && (own.state === "invalid" || own.state === "pending")) },
    el("summary", { text: "My own method" }));
  const work = el("textarea", { "data-own-work": step.id, placeholder: "Write your reasoning. Use $...$ for math." });
  work.value = (own && own.work) || "";
  const label = el("label", { text: "Your result for this step (optional, but SymPy will check it)" });
  const { field, preview } = mathField({ ...step, id: `${step.id}-own` }, own && own.result);
  const send = el("button", { text: inExam ? "Save for grading" : "Check my method", "data-action": "own" });
  send.addEventListener("click", () => {
    if (inExam) {
      run({ action: "exam_own", step: step.id, work: work.value, result: field.value }, send);
    } else {
      runAsk({ action: "own", step: step.id, work: work.value, result: field.value }, send, "Claude is checking...");
    }
  });
  panel.append(work, label, field, preview, send);
  if (own && own.state === "invalid") {
    panel.append(el("div", { class: "feedback incorrect", html: richText(own.reason) }));
  } else if (own && inExam) {
    panel.append(el("p", { class: "muted", text: "Saved. Claude will judge this when the exam is submitted." }));
  } else if (own && own.state === "pending") {
    panel.append(el("p", { class: "muted", text: "Waiting on Claude." }));
  }
  return panel;
}

function examCard(step, problem) {
  const card = el("article", { class: "step open" }, stepTitle(step));
  if (step.input === "discussion") {
    card.append(...figureList(step), el("p", { class: "prompt", html: richText(step.prompt) }));
    const work = el("textarea", { "data-exam-discussion": step.id, placeholder: step.placeholder || "Write your discussion. Use $...$ for math." });
    work.value = (step.own && step.own.work) || "";
    const key = `discussion-${step.id}`;
    work.addEventListener("input", () => {
      clearTimeout(state.draftTimers.get(key));
      state.draftTimers.set(key, setTimeout(() => act({ action: "exam_own", step: step.id, work: work.value, result: "" }).catch(() => {}), 500));
    });
    card.append(work);
    return card;
  }
  card.append(el("p", { class: "prompt", html: richText(step.prompt) }));
  if (step.input === "choice") {
    card.append(choiceInput(step, `exam-${state.view.active}-${step.id}`, (option) => {
      act({ action: "draft", step: step.id, latex: option }).catch(() => {});
    }));
    return card;
  }
  if (step.input === "multiple_choice") {
    card.append(optionsInput(step, `exam-${state.view.active}-${step.id}`, (value) => {
      act({ action: "draft", step: step.id, latex: value }).catch(() => {});
    }));
    return card;
  }
  if (step.input === "numeric") {
    const { input, row } = numericInput(step, (value) => queueDraft(step.id, value));
    input.dataset.exam = "true";
    card.append(row);
    return card;
  }
  const { field, preview } = mathField(step, step.draft, (latex) => queueDraft(step.id, latex));
  field.dataset.exam = "true";
  card.append(field, preview, ownMethodPanel(step, problem, true));
  return card;
}

function solutionCard(problem) {
  const card = el("section", { class: "solution-card", "data-solution": "true" }, el("h2", { text: "Worked solution" }));
  for (const row of problem.solution) {
    const item = el("div", { class: "solution-row" }, el("h4", { html: richText(row.label) }),
      el("div", { class: "ans" }, renderAnswer(row.answer, row.answer_format)), ...(row.figures || []).map(renderFigure));
    if (row.note) item.append(el("div", { class: "why", html: richText(row.note) }));
    const citation = sourceRow(row);
    if (citation) item.append(citation);
    if (row.own) item.append(el("div", { class: "why", html: `${row.kind === "discussion" ? "You wrote" : "You used your own method"}: ${richText(row.own)}` }));
    card.append(item);
  }
  const methodSources = problem.book ? sourceRow(problem.book) : null;
  if (methodSources && problem.method !== CONCEPT_METHOD) card.append(el("div", { class: "solution-method" }, el("h4", { text: "About this method" }), methodSources));
  const verified = problem.verified || {};
  if (problem.solution.some((row) => row.status === "skipped")) card.append(el("p", { class: "muted", text: "Includes the parts you skipped." }));
  const routes = [
    verified.residual && `substitution into the ODE ${verified.residual}`,
    verified.checkodesol && `checkodesol ${verified.checkodesol}`,
    verified.matrix_exponential && `matrix exponential ${verified.matrix_exponential}`,
    verified.dsolve && `dsolve ${verified.dsolve}`,
    verified.classification,
    verified.numeric && `numeric integration ${verified.numeric}`,
  ].filter(Boolean).join("; ");
  card.append(el("p", { class: "verified", text: verified.concept ? `${verified.concept}.` : `Answer key verified before you saw it: ${routes}.` }));
  return card;
}

function addProblemCard() {
  const panel = el("details", { class: "own-method problem-card" }, el("summary", { text: "Type in a new problem for this set" }));
  const statement = newField("New problem", "");
  const conditions = el("input", { type: "text", placeholder: "Initial conditions (optional), like y(0)=1, y'(0)=0", style: "width:100%;padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:inherit" });
  const method = el("select", { "aria-label": "Method" });
  fillMethods(method);
  const add = el("button", { class: "primary", text: "Verify and add" });
  add.addEventListener("click", async () => {
    add.disabled = true;
    add.textContent = "Verifying with SymPy...";
    const ic = conditions.value.split(",").map((part) => part.trim()).filter(Boolean);
    const current = state.view.problem || {};
    await run({ action: "add_problem", spec: { statement: statement.value, ic, method: method.value, section: current.section || "", topic: current.topic || "" } });
    add.disabled = false;
    add.textContent = "Verify and add";
  });
  panel.append(el("div", { class: "add-form" }, el("label", { text: "ODE" }), statement,
    el("label", { text: "Initial conditions" }), conditions, el("label", { text: "Method" }), method, add));
  return panel;
}

async function fillMethods(select) {
  const { methods } = await api("/api/methods");
  select.replaceChildren(...methods.filter((m) => m.id !== CONCEPT_METHOD).map((m) => el("option", { value: m.id, text: m.name })));
}

// --- Figures ----------------------------------------------------------------

const SUBSCRIPTS = { 0: "\u2080", 1: "\u2081", 2: "\u2082", 3: "\u2083" };
const SVG_NS = "http://www.w3.org/2000/svg";

function plainName(name) {
  return String(name).replace(/_(\d)/g, (_, digit) => SUBSCRIPTS[digit] || digit);
}

function svgEl(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceTicks(low, high, count = 5) {
  const span = high - low || 1;
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = [1, 2, 5, 10].map((m) => m * power).find((m) => raw <= m) || 10 * power;
  const ticks = [];
  for (let v = Math.ceil(low / unit) * unit; v <= high + unit * 1e-9; v += unit) ticks.push(Math.abs(v) < unit * 1e-9 ? 0 : v);
  return ticks;
}

function tickText(value) {
  const size = Math.abs(value);
  if (size >= 1000 || (size > 0 && size < 0.01)) return value.toExponential(0);
  return String(Number(value.toPrecision(3)));
}

function polyline(points, x, y) {
  return points.map(([a, b]) => `${x(a).toFixed(1)},${y(b).toFixed(1)}`).join(" ");
}

function arrowHead(points, x, y, klass) {
  if (points.length < 3) return null;
  const mid = Math.floor(points.length / 2);
  const [ax, ay] = [x(points[mid - 1][0]), y(points[mid - 1][1])];
  const [bx, by] = [x(points[mid][0]), y(points[mid][1])];
  const length = Math.hypot(bx - ax, by - ay);
  if (length < 0.01) return null;
  const [ux, uy] = [(bx - ax) / length, (by - ay) / length];
  const tip = `${bx.toFixed(1)},${by.toFixed(1)}`;
  const left = `${(bx - 8 * ux - 4 * uy).toFixed(1)},${(by - 8 * uy + 4 * ux).toFixed(1)}`;
  const right = `${(bx - 8 * ux + 4 * uy).toFixed(1)},${(by - 8 * uy - 4 * ux).toFixed(1)}`;
  return svgEl("polygon", { points: `${tip} ${left} ${right}`, class: klass });
}

function legendRow(entries) {
  const row = el("div", { class: "fig-legend" });
  for (const [klass, name] of entries) {
    const key = svgEl("svg", { width: 20, height: 10, "aria-hidden": "true" });
    key.append(svgEl("line", { x1: 0, x2: 20, y1: 5, y2: 5, class: klass }));
    row.append(el("span", {}, key, document.createTextNode(` ${name}`)));
  }
  return row;
}

function renderFigure(figure) {
  const box = el("figure", { class: "figure", "data-figure": figure.kind });
  if (figure.title) box.append(el("figcaption", { class: "fig-title", text: figure.title }));
  box.append(figure.kind === "phase" ? phaseChart(figure) : lineChart(figure));
  if (figure.caption) box.append(el("p", { class: "fig-caption", text: figure.caption }));
  return box;
}

function phaseChart(figure) {
  const size = 420;
  const pad = 34;
  const w = figure.window;
  const x = (v) => pad + ((v + w) / (2 * w)) * (size - pad - 10);
  const y = (v) => size - pad - ((v + w) / (2 * w)) * (size - pad - 10);
  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, class: "chart", role: "img", "aria-label": figure.alt || "Phase portrait" });
  const clipId = `clip-${Math.random().toString(36).slice(2)}`;
  const clip = svgEl("clipPath", { id: clipId });
  clip.append(svgEl("rect", { x: x(-w), y: y(w), width: x(w) - x(-w), height: y(-w) - y(w) }));
  svg.append(clip);
  for (const tick of niceTicks(-w, w)) {
    svg.append(svgEl("line", { x1: x(tick), x2: x(tick), y1: y(-w), y2: y(w), class: tick === 0 ? "axis" : "grid" }));
    svg.append(svgEl("line", { x1: x(-w), x2: x(w), y1: y(tick), y2: y(tick), class: tick === 0 ? "axis" : "grid" }));
    svg.append(svgEl("text", { x: x(tick), y: y(-w) + 15, class: "tick", "text-anchor": "middle" }, tickText(tick)));
    svg.append(svgEl("text", { x: x(-w) - 6, y: y(tick) + 4, class: "tick", "text-anchor": "end" }, tickText(tick)));
  }
  const body = svgEl("g", { "clip-path": `url(#${clipId})` });
  for (const [dx, dy] of figure.eigen || []) {
    const scale = 3 * w / Math.hypot(dx, dy);
    body.append(svgEl("line", { x1: x(-dx * scale), y1: y(-dy * scale), x2: x(dx * scale), y2: y(dy * scale), class: "eigenline" }));
  }
  for (const path of figure.paths) {
    body.append(svgEl("polyline", { points: polyline(path, x, y), class: "trajectory" }));
    const head = arrowHead(path, x, y, "arrow");
    if (head) body.append(head);
  }
  if (figure.ic_path) {
    body.append(svgEl("polyline", { points: polyline(figure.ic_path, x, y), class: "highlight" }));
    const head = arrowHead(figure.ic_path, x, y, "highlight-arrow");
    if (head) body.append(head);
    body.append(svgEl("circle", { cx: x(figure.ic[0]), cy: y(figure.ic[1]), r: 4, class: "highlight-dot" }));
  }
  body.append(svgEl("circle", { cx: x(0), cy: y(0), r: 3.5, class: "origin" }));
  svg.append(body);
  svg.append(svgEl("text", { x: x(w), y: y(-w) + 28, class: "axis-name", "text-anchor": "end" }, plainName(figure.x)));
  svg.append(svgEl("text", { x: x(-w) - 26, y: y(w) + 4, class: "axis-name" }, plainName(figure.y)));

  const readout = el("div", { class: "fig-readout", text: "Point at the plot for coordinates." });
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * size;
    const py = ((event.clientY - box.top) / box.height) * size;
    const vx = ((px - pad) / (size - pad - 10)) * 2 * w - w;
    const vy = ((size - pad - py) / (size - pad - 10)) * 2 * w - w;
    if (Math.abs(vx) <= w && Math.abs(vy) <= w) readout.textContent = `${plainName(figure.x)} = ${vx.toFixed(2)},  ${plainName(figure.y)} = ${vy.toFixed(2)}`;
  });
  const legend = [["trajectory", "Solution curves"]];
  if ((figure.eigen || []).length) legend.push(["eigenline", "Eigenvector directions"]);
  if (figure.ic_path) legend.push(["highlight", "Your initial value problem"]);
  return el("div", {}, legendRow(legend), svg, readout);
}

const END_LABEL_CHARS = 5;

function lineChart(figure) {
  const width = 560;
  const height = 300;
  const margin = { left: 52, right: 44, top: 12, bottom: 36 };
  const all = figure.series.flatMap((s) => s.points);
  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys, 0), Math.max(...ys, 0)];
  const room = (y1 - y0) * 0.06 || 1;
  y0 -= room;
  y1 += room;
  const scale = figure.log_x ? Math.log10 : (v) => v;
  const unscale = figure.log_x ? (v) => 10 ** v : (v) => v;
  const [s0, s1] = [scale(x0), scale(x1)];
  const x = (v) => margin.left + ((scale(v) - s0) / (s1 - s0 || 1)) * (width - margin.left - margin.right);
  const y = (v) => height - margin.bottom - ((v - y0) / (y1 - y0)) * (height - margin.top - margin.bottom);
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": figure.alt || figure.title || "Line chart" });
  for (const tick of niceTicks(y0, y1)) {
    svg.append(svgEl("line", { x1: x(x0), x2: x(x1), y1: y(tick), y2: y(tick), class: tick === 0 ? "axis" : "grid" }));
    svg.append(svgEl("text", { x: x(x0) - 6, y: y(tick) + 4, class: "tick", "text-anchor": "end" }, tickText(tick)));
  }
  const xTicks = figure.log_x ? [0.01, 0.1, 1, 10, 100].filter((v) => v >= x0 && v <= x1) : niceTicks(x0, x1, 6);
  for (const tick of xTicks) {
    if (figure.log_x) svg.append(svgEl("line", { x1: x(tick), x2: x(tick), y1: y(y1), y2: y(y0), class: "grid" }));
    svg.append(svgEl("text", { x: x(tick), y: height - margin.bottom + 16, class: "tick", "text-anchor": "middle" }, tickText(tick)));
  }
  svg.append(svgEl("text", { x: x(x1), y: height - 4, class: "axis-name", "text-anchor": "end" }, plainName(figure.x) + (figure.log_x ? " (log scale)" : "")));
  const ends = figure.series.map((series) => y(series.points[series.points.length - 1][1]));
  // Converging curves would stack their labels, and the legend already names them.
  // A long name would run off the edge, and the legend already names them too.
  const labelsFit = ends.every((a, i) => ends.every((b, j) => i === j || Math.abs(a - b) >= 15))
    && figure.series.every((series) => plainName(series.name).length <= END_LABEL_CHARS);
  figure.series.forEach((series, index) => {
    svg.append(svgEl("polyline", { points: polyline(series.points, x, y), class: `series series-${index + 1}` }));
    const last = series.points[series.points.length - 1];
    if (labelsFit) svg.append(svgEl("text", { x: x(last[0]) + 6, y: y(last[1]) + 4, class: "end-label" }, plainName(series.name)));
  });
  if (figure.marker) svg.append(svgEl("circle", { cx: x(figure.marker[0]), cy: y(figure.marker[1]), r: 4, class: "highlight-dot" }));
  const crosshair = svgEl("line", { y1: y(y1), y2: y(y0), class: "crosshair", visibility: "hidden" });
  svg.append(crosshair);
  const readout = el("div", { class: "fig-readout", text: `Point at the plot to read values, or open the table below.` });
  const base = figure.series[0].points;
  const show = (index) => {
    const at = base[index][0];
    crosshair.setAttribute("x1", x(at));
    crosshair.setAttribute("x2", x(at));
    crosshair.setAttribute("visibility", "visible");
    const values = figure.series.map((s) => `${plainName(s.name)} = ${s.points[index][1].toFixed(3)}`);
    readout.textContent = `${plainName(figure.x)} = ${at.toFixed(3)}:  ${values.join(",  ")}`;
  };
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const value = unscale((((event.clientX - box.left) / box.width) * width - margin.left) / (width - margin.left - margin.right) * (s1 - s0) + s0);
    let index = 0;
    base.forEach((point, k) => { if (Math.abs(point[0] - value) < Math.abs(base[index][0] - value)) index = k; });
    show(index);
  });
  svg.addEventListener("pointerleave", () => crosshair.setAttribute("visibility", "hidden"));

  const table = el("table", { class: "score" });
  table.append(el("tr", {}, el("th", { text: plainName(figure.x) }), ...figure.series.map((s) => el("th", { text: plainName(s.name) }))));
  const stride = Math.max(1, Math.floor(base.length / 12));
  for (let k = 0; k < base.length; k += stride) {
    table.append(el("tr", {}, el("td", { text: tickText(base[k][0]) }), ...figure.series.map((s) => el("td", { text: s.points[k][1].toFixed(3) }))));
  }
  const legend = figure.series.length > 1 ? legendRow(figure.series.map((s, i) => [`series series-${i + 1}`, plainName(s.name)])) : null;
  return el("div", {}, legend, svg, readout, el("details", { class: "fig-table" }, el("summary", { text: "Show as a table" }), table));
}

// --- Exam --------------------------------------------------------------------

function manageTimer(exam) {
  clearInterval(state.timerHandle);
  const timer = $("timer");
  if (!exam || exam.submitted) {
    timer.hidden = true;
    return;
  }
  timer.hidden = false;
  const clockSkew = Date.now() / 1000 - exam.now;
  const tick = () => {
    const elapsed = Date.now() / 1000 - clockSkew - exam.started_at;
    if (exam.limit_seconds) {
      const left = Math.max(0, exam.limit_seconds - elapsed);
      timer.textContent = `${clock(left)} left`;
      if (left <= 0) {
        clearInterval(state.timerHandle);
        submitExam();
      }
    } else {
      timer.textContent = clock(elapsed);
    }
  };
  tick();
  state.timerHandle = setInterval(tick, 1000);
}

function clock(seconds) {
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  return `${String(minutes).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

async function submitExam() {
  await saveVisibleDrafts();
  toast("Grading with SymPy...");
  await run({ action: "exam_submit" });
  $("toast").hidden = true;
  pollExamPending();
}

function pollExamPending() {
  const exam = state.view && state.view.exam;
  if (exam && exam.submitted && exam.score.pending > 0) {
    setTimeout(async () => {
      await refresh();
      pollExamPending();
    }, 2000);
  }
}

function examResults(exam) {
  const score = exam.score;
  const percent = score.total ? Math.round((100 * score.correct) / score.total) : 0;
  const wrapper = el("div", { "data-exam-results": "true" });
  const summary = el("section", { class: "solution-card" }, el("h2", { text: "Exam results" }),
    el("p", { class: "score-big", text: `${score.correct} / ${score.total} (${percent}%)` }),
    el("p", { class: "muted", text: `Time used: ${clock(score.seconds)}${score.pending ? `. Claude is still judging ${score.pending} step(s).` : ""}` }));
  const byType = el("table", { class: "score" }, el("tr", {}, el("th", { text: "Step type" }), el("th", { text: "Score" })));
  for (const [type, row] of Object.entries(score.by_type).sort((a, b) => a[1].correct / a[1].total - b[1].correct / b[1].total)) {
    byType.append(el("tr", {}, el("td", { text: methodName(type) }), el("td", { text: `${row.correct} / ${row.total}` })));
  }
  summary.append(byType);
  wrapper.append(summary);
  for (const problem of exam.problems) {
    const card = el("section", { class: "problem-card" }, el("h3", { text: `Problem ${problem.number}` }),
      el("div", { class: "statement", html: renderTex(problem.statement, true) }));
    for (const row of problem.rows) {
      const good = row.status === "correct";
      const item = el("div", { class: `result-row ${good ? "correct" : "wrong"}` },
        el("div", {}, el("span", { html: richText(row.label) }),
          el("span", { class: "mark", text: row.pending ? "  Waiting on Claude" : good ? "  Correct" : row.status === "blank" ? "  Blank" : "  Not correct" })));
      if (row.answer) item.append(el("div", { html: `You wrote: ${renderTex(row.answer)}` }));
      if (row.work) item.append(el("div", { html: `You wrote: ${richText(row.work)}` }));
      if (!good && !row.pending) {
        item.append(el("div", { html: `Correct: ${row.key_format === "text" ? richText(row.key) : renderTex(row.key)}` }));
        if (row.message && row.status !== "blank") item.append(el("div", { class: "muted", html: richText(row.message) }));
        const citation = sourceRow(row);
        if (citation) item.append(citation);
      }
      card.append(item);
    }
    wrapper.append(card);
  }
  wrapper.append(el("div", { class: "actions" },
    el("button", { class: "primary", text: "Back to practice", onclick: () => run({ action: "exam_close" }) })));
  return wrapper;
}

// --- Fixed controls ----------------------------------------------------------

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(node.hideTimer);
  node.hideTimer = setTimeout(() => { node.hidden = true; }, 6000);
}

function setupTheme() {
  const button = $("theme-toggle");
  const label = () => { button.textContent = document.documentElement.dataset.theme === "dark" ? "Light mode" : "Dark mode"; };
  label();
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("worksheet-theme", next); } catch { /* It just won't be remembered. */ }
    label();
  });
}

function setupControls() {
  setupTheme();
  $("home-link").href = homeAddress();
  $("home-link").addEventListener("click", async (event) => {
    event.preventDefault();
    await saveVisibleDrafts();
    location.href = homeAddress();
  });
  $("set-picker").addEventListener("change", async (event) => {
    saveVisibleDrafts();
    state.setId = event.target.value;
    params.set("set", state.setId);
    history.replaceState(null, "", `?${params}`);
    await refresh();
  });

  let fadeTimer;
  $("fade").addEventListener("input", (event) => {
    $("fade-value").textContent = `${event.target.value}%`;
    clearTimeout(fadeTimer);
    fadeTimer = setTimeout(() => run({ action: "fade", fade: Number(event.target.value) }), 80);
  });

  $("exam-toggle").addEventListener("change", (event) => {
    const running = state.view && state.view.exam;
    if (event.target.checked && !running) {
      event.target.checked = false;
      $("exam-dialog").showModal();
    } else if (!event.target.checked && running) {
      event.target.checked = true;
      if (running.submitted || confirm("Leave the exam? Your exam answers will be discarded and nothing will be graded.")) {
        run({ action: "exam_close" });
      }
    }
  });

  $("exam-dialog").addEventListener("close", () => {
    if ($("exam-dialog").returnValue === "start") {
      const minutes = Number($("exam-minutes").value) || null;
      saveVisibleDrafts();
      run({ action: "exam_start", minutes });
    }
  });

  $("cheat-grid").append(...CHEAT.map(([shown, insert, name]) => el("button", {
    class: "cheat-item", title: name, type: "button",
    onclick: () => {
      const field = state.focused && document.contains(state.focused) ? state.focused : document.querySelector("math-field[data-open='true']");
      if (!field) return;
      field.focus();
      field.executeCommand(["insert", insert]);
    },
  }, el("span", { html: renderTex(shown) }), el("code", { text: insert }))));
  window.addEventListener("beforeunload", saveVisibleDrafts);
}

async function fillRepertoire() {
  try {
    const { methods } = await api("/api/methods");
    $("repertoire-list").replaceChildren(...methods.map((m) => el("div", { class: "method-row" },
      el("b", { text: m.name + (m.origin === "added" ? " (added)" : "") }),
      el("span", { class: "muted", text: `${m.handles}. Limits: ${m.limits}.` }))));
  } catch {
    $("repertoire-list").textContent = "Couldn't load the repertoire.";
  }
}

// --- The home page -----------------------------------------------------------

const POLL_MS = 3000;
const DIFFICULTY_NAMES = { easier: "Easier", harder: "Harder", challenge: "Challenge" };
const SORTS = {
  recent: (a, b) => (b.last_opened || b.created_at) - (a.last_opened || a.created_at),
  newest: (a, b) => b.created_at - a.created_at,
  title: (a, b) => a.title.localeCompare(b.title),
  progress: (a, b) => (a.problems ? a.done / a.problems : 1) - (b.problems ? b.done / b.problems : 1),
};
const KIND_NAMES = { study_guide: "Study guide", equation_sheet: "Equation sheet" };
const COURSE_ASK_NAMES = { next_section: "Making section", retry_check: "Retry check for section",
  edit_syllabus: "Editing the syllabus", change_plan: "Changing the plan" };
const isCourseRequest = (request) => request.form.kind === "course" || Boolean(COURSE_ASK_NAMES[request.form.kind]);
const home = { library: null, waiting: new Set(), pollTimer: null };

const homeAddress = () => `?${new URLSearchParams({ token: TOKEN })}`;
const setAddress = (id) => `?${new URLSearchParams({ token: TOKEN, set: id })}`;
const shortDate = (seconds) => new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function bookName(book) {
  if (!book) return "";
  return [book.name, book.edition && `${book.edition} ed.`].filter(Boolean).join(", ");
}

function setRow(set) {
  const details = [bookName(set.textbook), set.sections.length && `Sections ${set.sections.join(", ")}`].filter(Boolean).join(" \u00b7 ");
  const when = set.last_opened ? `Last opened ${shortDate(set.last_opened)}` : `Made ${shortDate(set.created_at)}`;
  return el("a", { class: "set-row", href: setAddress(set.id), "data-set": set.id },
    el("div", { class: "info" },
      el("span", { class: "name", text: set.title }),
      DIFFICULTY_NAMES[set.difficulty] && el("span", { class: "chip", text: DIFFICULTY_NAMES[set.difficulty] }),
      el("div", { class: "muted", text: details })),
    el("div", { class: "status" },
      el("div", { text: `${set.done} of ${set.problems} done${set.skipped ? ` \u00b7 ${set.skipped} skipped` : ""}` }),
      el("div", { class: "muted", text: when })));
}

function docRow(doc) {
  const details = [doc.subject, bookName(doc.textbook)].filter(Boolean).join(" \u00b7 ");
  return el("a", { class: "set-row", href: `?${new URLSearchParams({ token: TOKEN, doc: doc.id })}`, "data-doc": doc.id },
    el("div", { class: "info" },
      el("span", { class: "name", text: doc.title }),
      el("span", { class: "chip", text: KIND_NAMES[doc.kind] }),
      el("div", { class: "muted", text: details })),
    el("div", { class: "status" }, el("div", { class: "muted", text: `Made ${shortDate(doc.created_at)}` })));
}

function requestRow(request) {
  const { form } = request;
  const stopped = request.state === "failed";
  const name = form.kind === "course" ? form.goal
    : COURSE_ASK_NAMES[form.kind] ? [COURSE_ASK_NAMES[form.kind], form.section].filter(Boolean).join(" ")
      : [form.topics || "Your own problems", bookName({ name: form.book, edition: form.edition })].filter(Boolean).join(" \u00b7 ");
  const message = stopped ? `This didn't finish: ${request.reason}` : "Claude is making this. If it has a question, it'll ask in your terminal.";
  return el("div", { class: `set-row pending${stopped ? " stopped" : ""}`, "data-request": request.id },
    el("div", { class: "info" },
      el("span", { class: "name", text: name }),
      KIND_NAMES[form.kind] && el("span", { class: "chip", text: KIND_NAMES[form.kind] }),
      form.kind === "course" && el("span", { class: "chip experimental", text: "Course, experimental" }),
      !KIND_NAMES[form.kind] && DIFFICULTY_NAMES[form.difficulty] && el("span", { class: "chip", text: DIFFICULTY_NAMES[form.difficulty] }),
      el("div", { class: "muted", text: message })),
    el("div", { class: "actions" },
      stopped && el("button", { type: "button", text: "Try again", onclick: () => changeRequest("retry_request", request.id) }),
      el("button", { type: "button", text: "Dismiss", onclick: () => changeRequest("dismiss_request", request.id) })));
}

function renderHome(library) {
  home.library = library;
  const waiting = new Set(library.requests.filter((r) => r.state === "waiting" && r.form.kind !== "index" && !isCourseRequest(r)).map((r) => r.id));
  if ([...home.waiting].some((id) => !waiting.has(id))) toast("It's ready, and it's in your list now.");
  home.waiting = waiting;

  const shown = $("home-filter").value;
  const items = [...library.sets, ...library.docs].filter((item) => shown === "all" || (item.kind || "worksheet") === shown);
  items.sort(SORTS[$("home-sort").value] || SORTS.recent);
  // Indexing has its own status on the Library page.
  const made = library.requests.filter((request) => request.form.kind !== "index" && !isCourseRequest(request));
  const rows = [...made.map(requestRow), ...items.map((item) => (item.kind ? docRow(item) : setRow(item)))];
  const empty = shown === "all" ? "Nothing here yet. Press New to make your first one." : "None of these yet. Press New to make one.";
  $("main").replaceChildren(...(rows.length ? rows : [el("p", { class: "muted", text: empty })]));

  clearTimeout(home.pollTimer);
  if (waiting.size) home.pollTimer = setTimeout(() => loadHome().catch((error) => toast(error.message)), POLL_MS);
}

async function loadHome() {
  renderHome(await api("/api/library"));
}

async function changeRequest(action, id) {
  home.waiting.delete(id);
  courses.waiting.delete(id);
  try {
    const library = await api("/api/action", { method: "POST", body: JSON.stringify({ action, id }) });
    await afterRequest(library);
  } catch (error) {
    toast(error.message);
  }
}

async function afterRequest(library) {
  if (COURSE_ID) return loadCourse();
  if (COURSES_PAGE) return loadCourses();
  return renderHome(library);
}

function openNewForm() {
  $("new-form").reset();
  $("new-error").hidden = true;
  const latest = home.library ? [...home.library.sets, ...home.library.docs].sort(SORTS.newest)[0] : null;
  if (latest && latest.textbook) {
    $("new-book").value = latest.textbook.name || "";
    $("new-edition").value = latest.textbook.edition || "";
  }
  $("new-subject").value = (latest && latest.subject) || "";
  if (COURSES_PAGE) $("new-kind").value = "course";
  showKindFields();
  fillLibraryClasses();
  $("new-dialog").showModal();
  $($("new-kind").value === "course" ? "new-goal" : "new-topics").focus();
}

async function fillLibraryClasses() {
  try {
    const { files } = await api("/api/files");
    const classes = [...new Set(files.map((file) => file.class))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    $("new-library-class").replaceChildren(el("option", { value: "", text: "Don't use it" }), ...classes.map((name) => el("option", { value: name, text: name })));
  } catch { /* The form still works without it. */ }
}

function showKindFields() {
  const kind = $("new-kind").value;
  for (const node of document.querySelectorAll(".only-worksheet")) node.hidden = kind !== "worksheet";
  for (const node of document.querySelectorAll(".only-guide")) node.hidden = kind !== "study_guide";
  for (const node of document.querySelectorAll(".only-course")) node.hidden = kind !== "course";
  for (const node of document.querySelectorAll(".not-course")) node.hidden = kind === "course";
}

async function submitNewForm(event) {
  event.preventDefault();
  $("new-submit").disabled = true;
  $("new-error").hidden = true;
  try {
    const form = {
      kind: $("new-kind").value, subject: $("new-subject").value, library_class: $("new-library-class").value,
      examples: $("new-examples").checked, figures: $("new-figures").checked,
      book: $("new-book").value, edition: $("new-edition").value, topics: $("new-topics").value,
      count: $("new-count").value, difficulty: $("new-difficulty").value,
      focus: $("new-focus").value, typed: $("new-typed").value, pages: $("new-pages").value,
      goal: $("new-goal").value, background: $("new-background").value, time_per_week: $("new-time").value,
      materials: $("new-materials").value,
    };
    const library = await api("/api/action", { method: "POST", body: JSON.stringify({ action: "request_set", form }) });
    $("new-dialog").close();
    await afterRequest(library);
  } catch (error) {
    $("new-error").textContent = error.message;
    $("new-error").hidden = false;
  } finally {
    $("new-submit").disabled = false;
  }
}

function setupHome() {
  setupTheme();
  try { $("home-sort").value = localStorage.getItem("worksheet-sort") || "recent"; } catch { /* Just the default order. */ }
  $("home-sort").addEventListener("change", () => {
    try { localStorage.setItem("worksheet-sort", $("home-sort").value); } catch { /* It just won't be remembered. */ }
    renderHome(home.library);
  });
  try { $("home-filter").value = localStorage.getItem("worksheet-filter") || "all"; } catch { /* Just show everything. */ }
  $("home-filter").addEventListener("change", () => {
    try { localStorage.setItem("worksheet-filter", $("home-filter").value); } catch { /* It just won't be remembered. */ }
    renderHome(home.library);
  });
  $("new-kind").addEventListener("change", showKindFields);
  $("library-link").href = `?${new URLSearchParams({ token: TOKEN, library: "1" })}`;
  $("courses-link").href = coursesAddress();
  $("help-link").href = `?${new URLSearchParams({ token: TOKEN, help: "1" })}`;
  $("new-set").addEventListener("click", openNewForm);
  $("new-cancel").addEventListener("click", () => $("new-dialog").close());
  $("new-form").addEventListener("submit", submitNewForm);
}

// --- Courses (experimental) ------------------------------------------------------

const PHASE_NAMES = { intake: "Starting", research: "Researching", planning: "Planning", syllabus: "Writing the syllabus",
  active: "In progress", complete: "Complete" };
const PHASE_NOTES = {
  research: "Claude is researching the topic. It will propose a syllabus in your terminal.",
  planning: "Claude is agreeing the style, checks and mastery bar with you in your terminal.",
  syllabus: "Claude is writing the syllabus you agreed on.",
};
const STATUS_NAMES = { locked: "Locked", available: "Available", generating: "Being made", in_progress: "In progress",
  mastered: "Mastered", tested_out: "Tested out" };
const EXAMPLE_NAMES = { worked_first: "worked examples first", after_theory: "examples after the theory", none: "no worked examples" };
const courses = { waiting: new Set(), pollTimer: null };
const coursesAddress = () => `?${new URLSearchParams({ token: TOKEN, courses: "1" })}`;
const courseAddress = (id) => `?${new URLSearchParams({ token: TOKEN, course: id })}`;
const docAddress = (id) => `?${new URLSearchParams({ token: TOKEN, doc: id })}`;
const percent = (ratio) => `${Math.round(ratio * 100)}%`;

function noteReady(requests) {
  const waiting = new Set(requests.filter((r) => r.state === "waiting").map((r) => r.id));
  if ([...courses.waiting].some((id) => !waiting.has(id))) toast("It's ready.");
  courses.waiting = waiting;
  return waiting.size > 0;
}

function pollCourses(busy, load) {
  clearTimeout(courses.pollTimer);
  if (busy) courses.pollTimer = setTimeout(() => load().catch((error) => toast(error.message)), POLL_MS);
}

function courseRow(course) {
  const progress = course.sections ? `${course.done} of ${course.sections} sections mastered` : PHASE_NAMES[course.phase];
  return el("a", { class: "set-row", href: courseAddress(course.id), "data-course": course.id },
    el("div", { class: "info" },
      el("span", { class: "name", text: course.title }),
      el("span", { class: "chip experimental", text: "Experimental" }),
      el("span", { class: "chip", text: PHASE_NAMES[course.phase] }),
      el("div", { class: "muted", text: `Started ${shortDate(course.created_at)}` })),
    el("div", { class: "status" }, el("div", { text: progress })));
}

function renderCourses(view) {
  $("home-title").textContent = "Courses";
  const started = view.requests.filter((r) => r.form.kind === "course");
  const rows = [...started.map(requestRow), ...view.courses.map(courseRow)];
  $("main").replaceChildren(
    el("p", { class: "note-box course-intro", html: "Courses are <b>experimental</b>. A course teaches a topic you're new to, "
      + "one section at a time, and a section opens once you've mastered the ones it builds on." }),
    ...(rows.length ? rows : [el("p", { class: "muted", text: "No courses yet. Press New to start one." })]));
  const busy = noteReady(view.requests) || view.courses.some((c) => ["intake", "research", "planning", "syllabus"].includes(c.phase));
  pollCourses(busy, loadCourses);
}

async function loadCourses() {
  renderCourses(await api("/api/courses"));
}

async function courseAct(body, button) {
  if (button) button.disabled = true;
  try {
    const reply = await api("/api/action", { method: "POST", body: JSON.stringify(body) });
    if (reply.course) renderCourse(reply);
    return reply;
  } catch (error) {
    toast(error.message);
    if (button) button.disabled = false;
    return null;
  }
}

function coursePlan(course) {
  const { plan } = course;
  if (!plan) return null;
  const { presentation, assessment, mastery } = plan;
  const rows = [
    ["Style", presentation.style || "Not set"],
    ["Examples", `${EXAMPLE_NAMES[presentation.examples]}, ${presentation.figures ? "with" : "without"} plots and diagrams`],
    ["Checks", assessment.default_kinds.map((kind) => kind.replace("_", " ")).join(", ")],
    ["Mastery", `${percent(mastery.threshold)} right, with hints on at most ${percent(mastery.max_hints_ratio)} of the items`],
    ...(plan.pacing ? [["Pacing", plan.pacing]] : []),
  ];
  const change = el("button", { type: "button", text: "Change plan", "data-course-action": "change_plan" });
  change.addEventListener("click", () => courseAct({ action: "course_request", kind: "change_plan", course: course.id }, change));
  return el("section", { class: "course-plan" }, el("h2", { text: "Plan" }),
    el("dl", {}, ...rows.flatMap(([term, value]) => [el("dt", { text: term }), el("dd", { text: value })])),
    el("div", { class: "actions" }, change));
}

function sectionCard(course, section, titles, linked) {
  const lastTry = section.attempts[section.attempts.length - 1];
  const card = el("article", { class: "course-section", "data-section": section.id, "data-status": section.status },
    el("div", { class: "course-section-head" },
      el("span", { class: "name", text: section.title }),
      el("span", { class: `chip status-${section.status}`, text: STATUS_NAMES[section.status] }),
      section.mastery && el("span", { class: "muted", text: `Best: ${percent(section.mastery.score)}` })));
  card.append(el("ul", { class: "objectives" }, ...section.objectives.map((objective) => el("li", { html: richText(objective) }))));
  if (section.prereqs.length) {
    card.append(el("div", { class: "muted", text: `Needs: ${section.prereqs.map((id) => titles[id] || id).join(", ")}` }));
  }
  const links = [];
  if (section.lesson_doc) links.push(el("a", { href: docAddress(section.lesson_doc), "data-lesson": section.id, text: "Lesson" }));
  if (section.check_set) {
    const set = linked[section.check_set] || {};
    links.push(el("a", { href: setAddress(section.check_set), "data-check": section.id,
      text: set.total ? `Check (${set.done} of ${set.total} done)` : "Check" }));
  }
  for (const setId of section.practice_sets) links.push(el("a", { href: setAddress(setId), text: (linked[setId] || {}).title || "Practice" }));
  if (links.length) card.append(el("div", { class: "course-links" }, ...links));
  if (lastTry && !lastTry.passed) {
    card.append(el("div", { class: "muted", text: `Last check: ${lastTry.correct} of ${lastTry.total}. Not mastered yet.` }));
  }
  const actions = [];
  if (section.status === "available" && !section.lesson_doc) {
    const make = el("button", { type: "button", class: "primary", text: "Generate", "data-generate": section.id });
    make.addEventListener("click", () => courseAct({ action: "course_request", kind: "next_section", course: course.id, section: section.id }, make));
    actions.push(make);
  }
  if (section.status === "in_progress" && section.check_set && lastTry && !lastTry.passed) {
    const retry = el("button", { type: "button", text: "Retry check", "data-retry": section.id });
    retry.addEventListener("click", () => courseAct({ action: "course_request", kind: "retry_check", course: course.id, section: section.id }, retry));
    actions.push(retry);
  }
  if (actions.length) card.append(el("div", { class: "actions" }, ...actions));
  return card;
}

function courseSyllabus(course, linked) {
  if (!course.chapters.length) return null;
  const sections = course.chapters.flatMap((chapter) => chapter.sections);
  const titles = Object.fromEntries(sections.map((section) => [section.id, section.title]));
  const edit = el("button", { type: "button", text: "Edit syllabus", "data-course-action": "edit_syllabus" });
  edit.addEventListener("click", () => courseAct({ action: "course_request", kind: "edit_syllabus", course: course.id }, edit));
  return el("section", { class: "course-syllabus" },
    el("div", { class: "course-syllabus-head" }, el("h2", { text: "Syllabus" }), edit),
    ...course.chapters.map((chapter) => el("div", { class: "course-chapter" }, el("h3", { text: chapter.title }),
      ...chapter.sections.map((section) => sectionCard(course, section, titles, linked)))));
}

function coursePlacement(course) {
  const placement = course.placement;
  if (!placement || !placement.set) return null;
  return el("p", { class: "note-box" }, el("a", { href: setAddress(placement.set), text: "Placement quiz" }),
    document.createTextNode(placement.result ? " Done. Sections you passed are marked tested out."
      : " Take it in Exam mode. Each section you pass is marked tested out."));
}

function courseHistory(course) {
  if (!course.history.length) return null;
  return el("details", { class: "course-history" }, el("summary", { text: "History" }),
    el("ul", {}, ...[...course.history].reverse().map((entry) => el("li", {},
      el("span", { class: "muted", text: `${shortDate(entry.at)} ` }), document.createTextNode(entry.change)))));
}

function renderCourse(view) {
  const { course } = view;
  document.title = `${course.title} (course)`;
  $("home-title").textContent = course.title;
  const remove = el("button", { type: "button", class: "danger", text: "Delete course", "data-course-action": "delete" });
  remove.addEventListener("click", async () => {
    if (!confirm("Delete this course? Its lessons and checks stay in your list.")) return;
    const done = await courseAct({ action: "course_delete", id: course.id }, remove);
    if (done) location.href = coursesAddress();
  });
  $("main").replaceChildren(...[
    el("a", { class: "back-link", href: coursesAddress(), text: "All courses" }),
    el("div", { class: "course-head" },
      el("span", { class: "chip experimental", text: "Experimental" }),
      el("span", { class: "chip", "data-phase": course.phase, text: PHASE_NAMES[course.phase] }),
      el("span", { class: "muted", text: course.intake.goal })),
    PHASE_NOTES[course.phase] && el("p", { class: "note-box", text: PHASE_NOTES[course.phase] }),
    ...view.requests.map(requestRow),
    coursePlacement(course),
    courseSyllabus(course, view.linked),
    coursePlan(course),
    courseHistory(course),
    el("div", { class: "actions course-danger" }, remove),
  ].filter(Boolean));
  const generating = course.chapters.some((chapter) => chapter.sections.some((section) => section.status === "generating"));
  pollCourses(noteReady(view.requests) || generating || Boolean(PHASE_NOTES[course.phase]), loadCourse);
}

async function loadCourse() {
  renderCourse(await api(`/api/course?${new URLSearchParams({ id: COURSE_ID })}`));
}

// --- Study guides and equation sheets ------------------------------------------

const SOURCE_STATUS = {
  confirmed: "The quote was found here when this was saved.",
  unchecked: "Not auto-checked",
};

function sourceAddress(source) {
  if (source.kind === "file" || source.kind === "library") {
    const query = new URLSearchParams({ token: TOKEN });
    const route = source.kind === "library" ? "file" : "source";
    return `/${route}/${source.file_id}?${query}${source.page ? `#page=${source.page}` : ""}`;
  }
  // A text fragment asks the browser to scroll to the quote. Where it doesn't, the page just opens at the top.
  const quote = encodeURIComponent(source.quote).replaceAll("-", "%2D");
  return `${source.url}${source.url.includes("#") ? "" : "#"}:~:text=${quote}`;
}

function sourceLabel(source) {
  if (!source.page) return source.title;
  return source.unit && source.unit !== "page" ? `${source.title}, ${source.unit} ${source.page}` : `${source.title}, p. ${source.page}`;
}

function sourceTip(source) {
  const status = source.status === "confirmed" ? SOURCE_STATUS.confirmed : `${SOURCE_STATUS.unchecked}: ${source.note}.`;
  return `\u201c${source.quote}\u201d\n${status}`;
}

function sourceLink(source, number) {
  const unchecked = source.status !== "confirmed";
  const link = el("a", { class: `source-link${unchecked ? " unchecked" : ""}`, href: sourceAddress(source), target: "_blank",
    rel: "noopener noreferrer", title: sourceTip(source), "data-source-status": source.status });
  link.textContent = `${number ? `[${number}] ` : ""}${sourceLabel(source)}${unchecked ? " (not auto-checked)" : ""}`;
  return link;
}

// The list of a block's sources, or the honest label for a block with none of its own.
function sourceRow(item, numberOf = () => null) {
  const list = item.sources || [];
  if (!list.length && !item.own_words) return null;
  const row = el("div", { class: "sources-row", "data-sources": "true" }, el("span", { class: "muted", text: list.length ? "Source:" : "" }));
  row.append(...list.map((source) => sourceLink(source, numberOf(source))));
  if (item.own_words) row.append(el("span", { class: "chip own-words", text: "Claude's own explanation, no source" }));
  return row;
}

function docBlock(block) {
  if (block.type === "heading") return el("h2", { html: richText(block.text) });
  if (block.type === "text") return el("p", { html: richText(block.text) });
  if (block.type === "list") return el("ul", {}, ...block.items.map((item) => el("li", { html: richText(item) })));
  if (block.type === "equation") {
    return el("div", { class: "doc-equation" },
      block.name && el("div", { class: "name", html: richText(block.name) }),
      el("div", { class: "math", html: renderTex(block.latex, true) }),
      block.note && el("div", { class: "muted", html: richText(block.note) }));
  }
  if (block.type === "table") {
    return el("div", { class: "table-scroll" }, el("table", { class: "doc-table" },
      el("tr", {}, ...block.headers.map((cell) => el("th", { html: richText(cell) }))),
      ...block.rows.map((row) => el("tr", {}, ...row.map((cell) => el("td", { html: richText(cell) }))))));
  }
  if (block.type === "definition") {
    return el("dl", { class: "doc-definition" }, el("dt", { html: richText(block.term) }), el("dd", { html: richText(block.meaning) }));
  }
  if (block.type === "nomenclature") {
    const units = block.rows.some((row) => row.unit);
    return el("div", { class: "table-scroll" }, el("table", { class: "doc-table" },
      el("tr", {}, el("th", { text: "Symbol" }), el("th", { text: "Meaning" }), units && el("th", { text: "Unit" })),
      ...block.rows.map((row) => el("tr", {}, el("td", { html: richText(row.symbol) }), el("td", { html: richText(row.meaning) }),
        units && el("td", { html: richText(row.unit) })))));
  }
  if (block.type === "note") {
    return el("div", { class: "note-box" }, block.title && el("h4", { html: richText(block.title) }), el("p", { html: richText(block.text) }));
  }
  if (block.type === "plot") return renderFigure(block.figure);
  if (block.type === "diagram") {
    // As an image, so the drawing can't run anything or load anything.
    return el("figure", { class: "figure diagram" },
      el("img", { src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(block.svg)}`, alt: block.alt }),
      block.caption && el("p", { class: "fig-caption", html: richText(block.caption) }));
  }
  if (block.type === "example") return docExample(block);
  return null;
}

function docExample(block) {
  const statement = block.statement
    ? el("div", { class: "statement", html: renderTex(block.statement + (block.ic.length ? `,\\quad ${block.ic.join(",\\quad ")}` : ""), true) })
    : el("p", { html: richText(block.problem) });
  const steps = block.steps.map((step) => {
    const row = el("div", { class: "doc-step" }, el("h4", { html: richText(step.label) }));
    if (step.text) row.append(el("p", { html: richText(step.text) }));
    else {
      row.append(renderAnswer(step.answer, step.answer_format), ...step.figures.map(renderFigure));
      if (step.note) row.append(el("p", { class: "muted", html: richText(step.note) }));
    }
    return row;
  });
  const label = block.verified ? "Answer verified by SymPy" : "Written by Claude, not checked by SymPy";
  return el("section", { class: "doc-example", "data-example": block.verified ? "verified" : "written" },
    el("h3", { html: richText(block.title || "Example") }),
    statement,
    el("div", { class: "meta" }, el("span", { class: `chip${block.verified ? "" : " warn"}`, text: label })),
    el("details", { class: "doc-solution" }, el("summary", { text: "Show worked solution" }), ...steps));
}

function docSourceList(cited) {
  return el("section", { class: "doc-sources" }, el("h2", { text: "Sources" }),
    el("ol", {}, ...cited.map((source, index) => el("li", {},
      sourceLink(source),
      el("div", { class: "muted", text: `\u201c${source.quote}\u201d` }),
      el("div", { class: `muted${source.status === "confirmed" ? "" : " warn-text"}`, text: source.status === "confirmed" ? SOURCE_STATUS.confirmed : `${SOURCE_STATUS.unchecked}: ${source.note}.` }),
      source.url && el("div", { class: "muted source-url", text: source.url })))));
}

function docSourceNote(doc, hasSources) {
  if (hasSources) {
    return "Written by Claude. Each link opens the source, and the quote listed with it shows what to look for: use Ctrl+F if the page doesn't jump there. A confirmed quote shows the source contains that passage, not that Claude's explanation of it is right. SymPy checks only what's labeled as verified.";
  }
  const book = bookName(doc.textbook);
  const from = doc.source === "pdf" ? "the pages you provided" : `general knowledge${book ? ` of ${book}` : ""}, not the book's pages`;
  return `Written by Claude from ${from}. SymPy checks only what's labeled as verified, so check formulas against your book.`;
}

// --- The quick reference panel ----------------------------------------------

const REF_ORDER = ["Definitions", "Equations", "Nomenclature"];
const AUTO_TAB = { definition: "Definitions", equation: "Equations", nomenclature: "Nomenclature" };
const remember = (key, value) => { try { localStorage.setItem(key, value); } catch { /* It just won't be remembered. */ } };
const recall = (key) => { try { return localStorage.getItem(key); } catch { return null; } };

// A block's tabs, on top of where it already sits in the guide.
function referenceTabs(doc) {
  const tabs = new Map();
  let section = "";
  const add = (name, block) => { if (!tabs.has(name)) tabs.set(name, []); tabs.get(name).push({ block, section }); };
  for (const block of doc.blocks) {
    if (block.type === "heading") section = block.text;
    if (AUTO_TAB[block.type]) add(AUTO_TAB[block.type], block);
    if (block.tab && block.tab !== AUTO_TAB[block.type]) add(block.tab, block);
  }
  const rank = (name) => (REF_ORDER.includes(name) ? REF_ORDER.indexOf(name) : REF_ORDER.length);
  return new Map([...tabs].sort((a, b) => rank(a[0]) - rank(b[0])));
}

function setupReference(doc, numberOf) {
  const tabs = referenceTabs(doc);
  if (!tabs.size) return;
  const root = document.documentElement;
  const panel = $("ref-panel");
  let active = tabs.has(recall("worksheet-ref-tab")) ? recall("worksheet-ref-tab") : tabs.keys().next().value;

  const drawn = new Map();
  const drawTab = (name) => {
    if (!drawn.has(name)) {
      let lastSection = null;
      drawn.set(name, tabs.get(name).flatMap(({ block, section }) => {
        const item = el("div", { class: "doc-item" }, docBlock(block), sourceRow(block, numberOf));
        if (section === lastSection || !section) return [item];
        lastSection = section;
        return [el("h3", { class: "ref-section", html: richText(section) }), item];
      }));
    }
    $("ref-body").replaceChildren(...drawn.get(name));
    $("ref-body").scrollTop = 0;
    for (const button of $("ref-tabs").children) button.setAttribute("aria-selected", String(button.textContent === name));
  };
  $("ref-tabs").replaceChildren(...[...tabs.keys()].map((name) => el("button", { type: "button", role: "tab", text: name, onclick: () => {
    active = name;
    remember("worksheet-ref-tab", name);
    drawTab(name);
  } })));

  const setExpanded = (expanded) => {
    panel.classList.toggle("expanded", expanded);
    $("ref-backdrop").hidden = !expanded;
    $("ref-expand").textContent = expanded ? "Back to side panel" : "Expand";
    $("ref-expand").setAttribute("aria-expanded", String(expanded));
  };
  // Opening the panel narrows the text, which would move what you're reading.
  const keepingPlace = (change) => {
    const top = $("top").getBoundingClientRect().bottom;
    const anchor = [...document.querySelectorAll(".doc-body > *")].find((node) => node.getBoundingClientRect().bottom > top);
    const before = anchor ? anchor.getBoundingClientRect().top : 0;
    change();
    if (anchor) window.scrollBy(0, anchor.getBoundingClientRect().top - before);
  };
  const setOpen = (open) => {
    panel.hidden = !open;
    keepingPlace(() => root.classList.toggle("ref-open", open));
    $("ref-toggle").setAttribute("aria-expanded", String(open));
    remember("worksheet-ref-open", open ? "yes" : "no");
    if (open) drawTab(active);
    else setExpanded(false);
  };
  $("ref-toggle").hidden = false;
  $("ref-toggle").addEventListener("click", () => setOpen(panel.hidden));
  $("ref-close").addEventListener("click", () => {
    setOpen(false);
    $("ref-toggle").focus();
  });
  $("ref-expand").addEventListener("click", () => setExpanded(!panel.classList.contains("expanded")));
  $("ref-backdrop").addEventListener("click", () => setExpanded(false));
  // Escape steps back one level: from the pop-up to the side panel, which stays open.
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && panel.classList.contains("expanded")) setExpanded(false);
  });
  setOpen(recall("worksheet-ref-open") === "yes");
}

// --- The class library -------------------------------------------------------

const LIBRARY_SORTS = {
  recent: (a, b) => b.added_at - a.added_at,
  name: (a, b) => a.name.localeCompare(b.name),
  class: (a, b) => a.class.localeCompare(b.class, undefined, { numeric: true }) || a.name.localeCompare(b.name),
  subject: (a, b) => a.subject.localeCompare(b.subject) || a.name.localeCompare(b.name),
  type: (a, b) => a.doc_type.localeCompare(b.doc_type) || a.name.localeCompare(b.name),
  pages: (a, b) => b.pages - a.pages,
};
const LIBRARY_FILTERS = [
  ["class", "Class", (file) => file.class], ["subject", "Subject", (file) => file.subject],
  ["doc_type", "Type", (file) => file.doc_type], ["status", "Status", (file) => libraryStatus(file).word],
];
const shelf = { files: [], shown: [], query: "", hits: null, pollTimer: null, searchTimer: null, editing: null, chosen: [] };
const fileAddress = (file, page) => `/file/${file.id}?${new URLSearchParams({ token: TOKEN })}${page ? `#page=${page}` : ""}`;

function libraryStatus(file) {
  if (file.index_stopped) return { word: "Stopped", text: `Indexing stopped: ${file.index_stopped}`, stopped: true };
  if (file.indexing) return { word: "Indexing", text: "Searchable now. Claude is indexing it." };
  if (!file.indexed) return { word: "Not indexed", text: "Searchable, but Claude hasn't indexed it." };
  if (file.pictures_unread) return { word: "Needs reading", text: `Indexed, but Claude hasn't read ${file.pictures_unread} picture ${file.unit}${file.pictures_unread === 1 ? "" : "s"} yet.` };
  return { word: "Ready", text: "Ready to use." };
}

function libraryChoice(key, fallback) {
  return recall(`worksheet-library-${key}`) || fallback;
}

function renderLibrary() {
  const box = $("main").querySelector(".library-filters") || buildLibraryFilters();
  for (const [key, , read] of LIBRARY_FILTERS) {
    const select = box.querySelector(`[data-filter="${key}"]`);
    const current = select.value;
    const values = [...new Set(shelf.files.map(read).filter(Boolean))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    select.replaceChildren(el("option", { value: "", text: "All" }), ...values.map((value) => el("option", { value, text: value })));
    select.value = values.includes(current) ? current : "";
  }
  const hitsByFile = new Map();
  for (const hit of shelf.hits || []) {
    if (!hitsByFile.has(hit.file)) hitsByFile.set(hit.file, []);
    hitsByFile.get(hit.file).push(hit);
  }
  const query = shelf.query.toLowerCase();
  const shown = shelf.files.filter((file) => LIBRARY_FILTERS.every(([key, , read]) => {
    const wanted = box.querySelector(`[data-filter="${key}"]`).value;
    return !wanted || read(file) === wanted;
  }) && (!query || hitsByFile.has(file.id) || `${file.name} ${file.class} ${file.subject} ${file.doc_type}`.toLowerCase().includes(query)));
  shown.sort(LIBRARY_SORTS[box.querySelector("[data-sort]").value] || LIBRARY_SORTS.recent);
  shelf.shown = shown;

  const list = $("main").querySelector(".library-list");
  const count = $("main").querySelector(".library-count");
  count.textContent = shelf.files.length ? `Showing ${shown.length} of ${shelf.files.length} files` : "";
  list.replaceChildren(...(shown.length ? shown.map((file) => libraryRow(file, hitsByFile.get(file.id) || []))
    : [el("p", { class: "muted", text: shelf.files.length ? "Nothing matches these filters." : "Nothing here yet. Press Add files to start with one of your classes." })]));

  clearTimeout(shelf.pollTimer);
  if (shelf.files.some((file) => file.indexing)) shelf.pollTimer = setTimeout(() => loadLibrary().catch((error) => toast(error.message)), POLL_MS);
}

function libraryRow(file, hits) {
  const status = libraryStatus(file);
  const details = [`${file.pages} ${file.unit}${file.pages === 1 ? "" : "s"}`, `Added ${shortDate(file.added_at)}`].join(" \u00b7 ");
  const actions = [
    el("button", { type: "button", text: "Edit details", "data-action": "library-edit", onclick: () => openLibraryDialog(file) }),
    el("button", { type: "button", text: "Delete", "data-action": "library-delete", onclick: () => deleteLibraryFile(file) }),
  ];
  if (!file.indexing && (!file.indexed || file.pictures_unread)) {
    actions.unshift(el("button", { type: "button", text: file.indexed ? "Finish indexing" : "Ask Claude to index", "data-action": "library-index",
      onclick: () => libraryAct({ action: "library_index", files: [file.id] }) }));
  }
  return el("article", { class: "library-row", "data-library-file": file.id },
    el("div", { class: "top" },
      el("div", {},
        el("a", { class: "name", href: fileAddress(file), target: "_blank", rel: "noopener noreferrer", text: file.name }),
        el("div", {}, ...[file.class, file.subject, file.doc_type].filter(Boolean).map((text) => el("span", { class: "chip", text })))),
      el("div", { class: "muted", text: details })),
    el("div", { class: `state${status.stopped ? " stopped" : ""}`, text: status.text }),
    ...hits.slice(0, 5).map((hit) => el("div", { class: "library-hit" },
      el("a", { href: fileAddress(file, hit.page), target: "_blank", rel: "noopener noreferrer", text: `${hit.unit} ${hit.page}` }),
      document.createTextNode(`: ${hit.snippet}`))),
    hits.length > 5 && el("div", { class: "library-hit", text: `and ${hits.length - 5} more` }),
    el("div", { class: "actions" }, ...actions));
}

function buildLibraryFilters() {
  const search = el("input", { type: "search", "data-search": "true", placeholder: "File names and contents", "aria-label": "Search your library" });
  search.addEventListener("input", () => {
    clearTimeout(shelf.searchTimer);
    shelf.searchTimer = setTimeout(() => runLibrarySearch(search.value.trim()), 250);
  });
  const selects = LIBRARY_FILTERS.map(([key, name]) => {
    const select = el("select", { "data-filter": key, onchange: renderLibrary });
    return el("label", {}, document.createTextNode(name), select);
  });
  const sort = el("select", { "data-sort": "true", onchange: () => {
    remember("worksheet-library-sort", sort.value);
    renderLibrary();
  } }, el("option", { value: "recent", text: "Recently added" }), el("option", { value: "name", text: "File name" }),
  el("option", { value: "class", text: "Class name or number" }), el("option", { value: "subject", text: "Subject" }),
  el("option", { value: "type", text: "Type" }), el("option", { value: "pages", text: "Longest first" }));
  sort.value = libraryChoice("sort", "recent");
  const box = el("div", { class: "library-filters" }, el("label", { class: "grow" }, document.createTextNode("Search"), search), ...selects,
    el("label", {}, document.createTextNode("Sort by"), sort));
  $("main").replaceChildren(box, el("p", { class: "library-count" }), el("div", { class: "library-list" }));
  return box;
}

async function runLibrarySearch(query) {
  shelf.query = query;
  shelf.hits = null;
  if (query && /[a-z0-9]/i.test(query)) {
    const asked = query;
    try {
      const { hits } = await api(`/api/files/search?${new URLSearchParams({ q: query })}`);
      if (shelf.query !== asked) return;
      shelf.hits = hits;
    } catch (error) {
      toast(error.message);
    }
  }
  renderLibrary();
}

async function libraryAct(body) {
  try {
    shelf.files = (await api("/api/action", { method: "POST", body: JSON.stringify(body) })).files;
    renderLibrary();
  } catch (error) {
    toast(error.message);
  }
}

async function deleteLibraryFile(file) {
  if (confirm(`Delete ${file.name} from your library? Source links to it will stop working. The file you added it from isn't touched.`)) {
    await libraryAct({ action: "library_delete", id: file.id });
  }
}

async function loadLibrary() {
  shelf.files = (await api("/api/files")).files;
  renderLibrary();
}

function fillLibrarySuggestions() {
  const options = (id, read) => $(id).replaceChildren(...[...new Set(shelf.files.map(read).filter(Boolean))].map((value) => el("option", { value })));
  options("library-class-names", (file) => file.class_name);
  options("library-class-numbers", (file) => file.class_number);
  options("library-subjects", (file) => file.subject);
}

// Without a file, this adds new ones. With one, it edits that file's details.
function openLibraryDialog(file = null) {
  shelf.editing = file;
  shelf.chosen = [];
  $("library-form").reset();
  $("library-error").hidden = true;
  $("library-progress").hidden = true;
  $("library-progress").replaceChildren();
  $("library-drop").hidden = Boolean(file);
  $("library-dialog-title").textContent = file ? `Edit ${file.name}` : "Add files to your library";
  $("library-submit").textContent = file ? "Save" : "Add to library";
  $("library-submit").disabled = false;
  showChosenFiles();
  fillLibrarySuggestions();
  const from = file || [...shelf.files].sort(LIBRARY_SORTS.recent)[0];
  if (from) {
    $("library-class-name").value = from.class_name;
    $("library-class-number").value = from.class_number;
    $("library-subject").value = from.subject;
    if (from.doc_type) $("library-doc-type").value = from.doc_type;
  }
  $("library-dialog").showModal();
}

function showChosenFiles() {
  const count = shelf.chosen.length;
  $("library-drop-text").textContent = count
    ? `${count} file${count === 1 ? "" : "s"} chosen: ${shelf.chosen.map((file) => file.name).join(", ")}`
    : "Drop files here, or click to choose. PDF, Word, PowerPoint, images and text files are all fine.";
}

function libraryDetails() {
  return {
    class_name: $("library-class-name").value.trim(), class_number: $("library-class-number").value.trim(),
    subject: $("library-subject").value.trim(), doc_type: $("library-doc-type").value,
  };
}

function libraryError(message) {
  $("library-error").textContent = message;
  $("library-error").hidden = false;
}

async function uploadOne(file, details) {
  const query = new URLSearchParams({ name: file.name, ...details });
  let response;
  try {
    response = await fetch(`/api/files/upload?${query}`, {
      method: "POST", body: file, headers: { "X-Worksheet-Token": TOKEN, "Content-Type": "application/octet-stream" },
    });
  } catch {
    throw new Error("The worksheet server isn't running. Start it from Claude Code with /worksheet.");
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "That file couldn't be added.");
  return body;
}

async function submitLibraryForm(event) {
  event.preventDefault();
  const details = libraryDetails();
  $("library-error").hidden = true;
  if (!details.class_name && !details.class_number) return libraryError("Give the class a name or a number.");
  if (shelf.editing) {
    try {
      shelf.files = (await api("/api/action", { method: "POST", body: JSON.stringify({ action: "library_update", id: shelf.editing.id, details }) })).files;
      $("library-dialog").close();
      renderLibrary();
    } catch (error) {
      libraryError(error.message);
    }
    return undefined;
  }
  if (!shelf.chosen.length) return libraryError("Choose at least one file.");
  $("library-submit").disabled = true;
  const progress = $("library-progress");
  progress.hidden = false;
  progress.replaceChildren();
  const added = [];
  let failures = 0;
  for (const file of shelf.chosen) {
    const row = el("li", { text: `${file.name}: saving and reading...` });
    progress.append(row);
    row.scrollIntoView({ block: "nearest" });
    try {
      const entry = await uploadOne(file, details);
      if (entry.duplicate) {
        row.textContent = `${file.name}: already in this class, so skipped.`;
      } else {
        added.push(entry.id);
        row.textContent = `${file.name}: added, ${entry.pages} ${entry.unit}${entry.pages === 1 ? "" : "s"}.`;
      }
    } catch (error) {
      failures += 1;
      row.textContent = `${file.name}: ${error.message}`;
      row.className = "failed";
    }
  }
  $("library-submit").disabled = false;
  if (added.length) await libraryAct({ action: "library_index", files: added });
  else await loadLibrary();
  shelf.chosen = [];
  showChosenFiles();
  if (!failures) $("library-dialog").close();
  if (added.length) toast("Added. Claude will index them now, and they're searchable already.");
  return undefined;
}

async function exportLibrary() {
  const count = shelf.shown.length;
  if (!count) return toast("There's nothing to export. Check your filters.");
  const noun = `${count} file${count === 1 ? "" : "s"}`;
  if (!confirm(`Export the ${noun} you're showing as one zip? It holds your actual files, so only share it with people who should have them.`)) return undefined;
  $("library-export").disabled = true;
  toast(`Packing ${noun}...`);
  try {
    const { export: name } = await api("/api/action", { method: "POST", body: JSON.stringify({ action: "library_export", files: shelf.shown.map((file) => file.id) }) });
    const link = el("a", { href: `/export/${name}?${new URLSearchParams({ token: TOKEN })}`, download: "library-export.zip" });
    document.body.append(link);
    link.click();
    link.remove();
    toast(`Exported ${noun}.`);
  } catch (error) {
    toast(error.message);
  } finally {
    $("library-export").disabled = false;
  }
  return undefined;
}

async function importLibrary(file) {
  $("library-import").disabled = true;
  toast("Importing...");
  try {
    const response = await fetch("/api/files/import", {
      method: "POST", body: file, headers: { "X-Worksheet-Token": TOKEN, "Content-Type": "application/octet-stream" },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "That couldn't be imported.");
    const parts = [`Imported ${body.added.length} file${body.added.length === 1 ? "" : "s"}`];
    if (body.skipped.length) parts.push(`${body.skipped.length} already here`);
    if (body.failed.length) parts.push(`${body.failed.length} couldn't be read`);
    toast(`${parts.join(", ")}.`);
    await loadLibrary();
  } catch (error) {
    toast(error.message);
  } finally {
    $("library-import").disabled = false;
    $("library-import-file").value = "";
  }
}

async function startLibrary() {
  setupTheme();
  $("library-export").addEventListener("click", exportLibrary);
  $("library-import").addEventListener("click", () => $("library-import-file").click());
  $("library-import-file").addEventListener("change", (event) => {
    if (event.target.files[0]) importLibrary(event.target.files[0]);
  });
  $("home-link").href = homeAddress();
  document.title = "My library";
  $("home-title").textContent = "My library";
  $("library-add").addEventListener("click", () => openLibraryDialog());
  $("library-cancel").addEventListener("click", () => $("library-dialog").close());
  $("library-form").addEventListener("submit", submitLibraryForm);
  $("library-files").addEventListener("change", (event) => {
    shelf.chosen = [...event.target.files];
    showChosenFiles();
  });
  const zone = $("library-drop");
  for (const name of ["dragenter", "dragover"]) {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("dragging");
    });
  }
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    shelf.chosen = [...event.dataTransfer.files];
    showChosenFiles();
  });
  await loadLibrary();
}

async function startDoc() {
  setupTheme();
  $("home-link").href = homeAddress();
  $("doc-print").addEventListener("click", () => window.print());
  // A printout has no way to open these.
  window.addEventListener("beforeprint", () => {
    for (const solution of document.querySelectorAll(".doc-solution")) solution.open = true;
  });
  const doc = await api(`/api/doc?${new URLSearchParams({ id: DOC_ID })}`);
  document.title = doc.title;
  $("home-title").textContent = doc.title;
  const facts = [KIND_NAMES[doc.kind], doc.subject, bookName(doc.textbook)].filter(Boolean);
  const cited = [];
  const keyOf = (source) => JSON.stringify([source.url || source.file_id, source.page, source.quote]);
  for (const block of doc.blocks) {
    for (const source of block.sources || []) {
      if (!cited.some((seen) => keyOf(seen) === keyOf(source))) cited.push(source);
    }
  }
  const numberOf = (source) => cited.findIndex((seen) => keyOf(seen) === keyOf(source)) + 1;
  const nodes = doc.blocks.flatMap((block) => {
    const node = docBlock(block);
    const row = sourceRow(block, numberOf);
    if (!row) return [node];
    // A heading stays a direct child, so it can span an equation sheet's columns.
    return block.type === "heading" ? [node, row] : [el("div", { class: "doc-item" }, node, row)];
  });
  if (doc.kind === "study_guide") setupReference(doc, numberOf);
  $("main").replaceChildren(el("article", { class: `doc doc-${doc.kind}` },
    el("h1", { class: "doc-print-title", text: doc.title }),
    el("div", { class: "doc-meta" }, ...facts.map((fact) => el("span", { class: "chip", text: fact }))),
    el("div", { class: "doc-body" }, ...nodes),
    cited.length ? docSourceList(cited) : null,
    el("p", { class: "muted doc-source", text: docSourceNote(doc, cited.length > 0) })));
}

// --- The help page -----------------------------------------------------------

// Help text is ours, so it may hold <code>. [[Label]] names something on screen.
function helpText(text) {
  return String(text).split(/(\$[^$]+\$)/g).map((piece) => {
    if (piece.length > 2 && piece.startsWith("$") && piece.endsWith("$")) return renderTex(piece.slice(1, -1));
    return piece.replace(/\[\[(.+?)\]\]/g, "<b>$1</b>");
  }).join("");
}

function helpSection(section) {
  return el("section", { class: "help-section", id: section.id, "data-help": section.id },
    el("h3", { html: helpText(section.title) }),
    el("p", { html: helpText(section.what) }),
    el("div", { class: "help-label", text: "Example" }),
    el("div", { class: "help-example", html: helpText(section.example) }),
    el("div", { class: "help-label", text: "How to use it well" }),
    el("ul", {}, ...section.how.map((tip) => el("li", { html: helpText(tip) }))));
}

function startHelp() {
  setupTheme();
  $("home-link").href = homeAddress();
  document.title = "Help";
  $("home-title").textContent = "Help";
  const jump = (id) => (event) => {
    event.preventDefault();
    document.getElementById(id).scrollIntoView();
  };
  $("main").replaceChildren(
    el("nav", { class: "help-contents", "aria-label": "Contents" }, el("h2", { text: "Contents" }),
      ...HELP_GROUPS.flatMap((group) => [el("h3", { text: group.title }),
        el("ul", {}, ...group.sections.map((section) => el("li", {},
          el("a", { href: `#${section.id}`, text: section.title, onclick: jump(section.id) }))))])),
    ...HELP_GROUPS.flatMap((group) => [el("h2", { class: "help-group", text: group.title }), ...group.sections.map(helpSection)]));
}

async function start() {
  try {
    if (HELP_PAGE) {
      startHelp();
      return;
    }
    if (LIBRARY_PAGE) {
      await startLibrary();
      return;
    }
    if (COURSE_ID || COURSES_PAGE) {
      setupHome();
      await (COURSE_ID ? loadCourse() : loadCourses());
      return;
    }
    if (DOC_ID) {
      await startDoc();
      return;
    }
    if (!state.setId) {
      setupHome();
      await loadHome();
      return;
    }
    setupControls();
    const { sets } = await api("/api/sets");
    if (!sets.some((s) => s.id === state.setId)) {
      location.replace(homeAddress());
      return;
    }
    await refresh();
    pollExamPending();
    fillRepertoire();
  } catch (error) {
    $("main").replaceChildren(el("div", { class: "feedback restate", text: error.message }));
    window.reportClientError(`${error.message} | ${error.stack || ""}`);
  }
}

start();
